"""Apply confirmed Silver changes to Live evidence partitions and their lineage."""

import json
from collections import defaultdict
from datetime import datetime, timedelta

import pyarrow as pa

from .common import IntegrityError
from .evidence import _fragment_bounds, _write_table
from .silver_incremental import FullReadRequired, read_changes

EPOCH = datetime(1970, 1, 1)
LINEAGE_SCHEMA = pa.schema([("family", pa.string()), ("key", pa.string()), ("proof", pa.string())])


def partition(family, row, seconds):
    stamp = row["FECHA_ON" if family == "LISTA_AR" else "FECHA"]
    return row["origen_fenix"], (stamp - EPOCH) // timedelta(seconds=seconds)


def _lineage_part(root, family, key, proofs):
    rows = [
        {"family": family, "key": identity, "proof": json.dumps(proof, sort_keys=True)}
        for identity, entries in proofs.items()
        for proof in entries
    ]
    rows.sort(key=lambda row: (row["key"], row["proof"]))
    if rows:
        table = pa.Table.from_pylist(rows, schema=LINEAGE_SCHEMA)
        return {**_write_table(root, table), "partition": [family, *key]}
    return None


def bootstrap_lineage(root, family, table, proofs, seconds):
    """Build locality once for a legacy or rebuilt family from its accepted rows."""
    from ingesta_delta_lake.pipeline_rows import business_key

    grouped = defaultdict(dict)
    for batch in table.to_batches():
        for row in batch.to_pylist():
            identity = business_key(family, row)
            if identity in proofs:
                grouped[partition(family, row, seconds)][identity] = proofs[identity]
    if sum(map(len, grouped.values())) != len(proofs):
        raise IntegrityError("Gold lineage does not belong to the accepted family")
    return [
        part
        for key, group in sorted(grouped.items())
        if (part := _lineage_part(root, family, key, group)) is not None
    ]


def update(snapshot, previous, family, after, schema, *, budget, seconds):
    """Return new fact and lineage descriptors without reopening untouched Parquet."""
    from ingesta_delta_lake.pipeline_rows import business_key, conflicting_keys

    before = previous.document["silver_refs"].get(family)
    refs = {r["name"]: r for r in previous.document["tables"]}
    if before is None and after is None:
        if refs[family]["rows"]:
            raise FullReadRequired
        return refs[family], [], 0
    if "parts" not in refs[family] and refs[family]["rows"]:
        raise FullReadRequired

    by_partition = defaultdict(dict)
    positions = {}

    def locate(identity, row):
        key = partition(family, row, seconds)
        if identity in positions and positions[identity] != key:
            raise IntegrityError("A Silver business key spans different Gold partitions")
        positions[identity] = key

    changes, count = read_changes(
        snapshot, family, before, after, schema.names, budget, locate=locate
    )
    for identity, value in changes.items():
        by_partition[positions[identity]][identity] = value

    facts = {tuple(part["partition"]): part for part in refs[family].get("parts", [])}
    lineage = {
        tuple(part["partition"][1:]): part
        for part in refs["__lineage"].get("parts", [])
        if part["partition"][0] == family
    }
    scanned = 0
    if not by_partition:
        return refs[family], list(lineage.values()), count
    for key, edits in sorted(by_partition.items()):
        old_fact = facts.pop(key, None)
        old_lineage = lineage.pop(key, None)
        scanned += sum(p["rows"] for p in (old_fact, old_lineage) if p)
        if scanned > budget:
            raise IntegrityError("Affected Gold fragments exceed their explicit row budget")
        rows = (
            {
                business_key(family, row): row
                for row in previous._read_part(family, old_fact).to_pylist()
            }
            if old_fact
            else {}
        )
        proofs = defaultdict(list)
        if old_lineage:
            for row in previous._read_part("__lineage", old_lineage).to_pylist():
                if row["family"] != family or row["key"] not in rows:
                    raise IntegrityError("Gold lineage partition differs from its facts")
                proofs[row["key"]].append(json.loads(row["proof"]))
        for identity, (row, entries) in edits.items():
            if row is None:
                rows.pop(identity, None)
                proofs.pop(identity, None)
            else:
                rows[identity] = row
                proofs[identity] = entries
        if conflicting_keys(family, rows):
            raise FullReadRequired
        if rows:
            table = pa.Table.from_pylist(list(rows.values()), schema=schema)
            facts[key] = {
                **_write_table(previous.root, table),
                "partition": list(key),
                "time_bounds": _fragment_bounds(family, table),
            }
            if (part := _lineage_part(previous.root, family, key, proofs)) is not None:
                lineage[key] = part

    descriptors = [item for _, item in sorted(facts.items())]
    row_count = sum(item["rows"] for item in descriptors)
    if row_count > budget:
        raise IntegrityError("Incremental Gold Silver view exceeds its explicit row budget")
    ref = (
        {"name": family, "parts": descriptors, "rows": row_count}
        if descriptors
        else {
            "name": family,
            **_write_table(previous.root, pa.Table.from_pylist([], schema=schema)),
        }
    )
    return ref, list(lineage.values()), count
