"""Apply confirmed Silver changes to Live evidence partitions and their lineage."""

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.parquet as pq

from .common import IntegrityError
from .evidence import (
    OPERATIONS,
    Evidence,
    _fragment_bounds,
    _write_table,
    checked_file,
    evidence_files,
)
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


def migrate_lineage(previous, seconds, *, batch_size):
    """Re-index legacy proofs once on disk, retaining all original evidence references."""
    from ingesta_delta_lake.pipeline_rows import business_key

    refs = {r["name"]: r for r in previous.document["tables"]}
    lineage = refs.get("__lineage")
    if lineage is None:
        raise IntegrityError("No accepted legacy lineage to migrate")
    scratch = previous.root.parent / "checkpoint"
    scratch.mkdir(exist_ok=True)
    outputs = []
    with (
        TemporaryDirectory(prefix="lineage-migration-", dir=scratch) as temporary,
        closing(sqlite3.connect(f"{temporary}/lineage.sqlite")) as db,
    ):
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA temp_store=FILE")
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        db.execute(f"PRAGMA max_page_count={1024**3 // page_size}")
        db.executescript("""
            CREATE TABLE loc (family TEXT, key TEXT, origin TEXT, slot INTEGER,
                              PRIMARY KEY(family, key)) WITHOUT ROWID;
            CREATE TABLE proofs (family TEXT, origin TEXT, slot INTEGER, key TEXT, payload TEXT,
                                 PRIMARY KEY(family, origin, slot, key, payload)) WITHOUT ROWID;
        """)
        with db:
            for family in sorted(OPERATIONS):
                ref = refs[family]
                if ref["rows"] and "parts" not in ref:
                    raise IntegrityError(f"Legacy Gold facts lack bounded partitions for {family}")
                for part in ref.get("parts", []):
                    parquet = pq.ParquetFile(checked_file(previous.root, part))
                    if parquet.metadata.num_rows != part["rows"]:
                        raise IntegrityError("Legacy fact count differs from its descriptor")
                    for batch in parquet.iter_batches(batch_size=batch_size):
                        db.executemany(
                            "INSERT INTO loc VALUES(?,?,?,?)",
                            (
                                (family, business_key(family, r), *partition(family, r, seconds))
                                for r in batch.to_pylist()
                            ),
                        )
            for part in evidence_files(lineage):
                # Legacy catalogue fragments already use an independent partition.
                key = part.get("partition")
                if key and key[0] not in OPERATIONS:
                    outputs.append(part)
                    continue
                parquet = pq.ParquetFile(checked_file(previous.root, part))
                if parquet.metadata.num_rows != part["rows"]:
                    raise IntegrityError("Legacy lineage count differs from its descriptor")
                for batch in parquet.iter_batches(batch_size=batch_size):
                    for row in batch.to_pylist():
                        family, identity, proof = row["family"], row["key"], row["proof"]
                        if family in OPERATIONS:
                            result = db.execute(
                                "INSERT INTO proofs SELECT family,origin,slot,key,? FROM loc "
                                "WHERE family=? AND key=?",
                                (proof, family, identity),
                            )
                            if result.rowcount != 1:
                                raise IntegrityError("Legacy Gold lineage has no accepted fact")
                        else:
                            db.execute(
                                "INSERT INTO proofs VALUES(?, 'catalog', 0, ?, ?)",
                                (family, identity, proof),
                            )
        groups = db.execute("SELECT DISTINCT family,origin,slot FROM proofs ORDER BY 1,2,3")
        for family, origin, slot in groups:
            total = db.execute(
                "SELECT COUNT(*) FROM proofs WHERE family=? AND origin=? AND slot=?",
                (family, origin, slot),
            ).fetchone()[0]
            # Each operational partition is the unit of subsequent atomic reconciliation.
            if total > previous.document["row_budget"]:
                raise IntegrityError(
                    f"Legacy Gold partition exceeds its reconciliation budget: {family}"
                )
            rows = db.execute(
                "SELECT key,payload FROM proofs WHERE family=? AND origin=? AND slot=? "
                "ORDER BY 1,2",
                (family, origin, slot),
            )
            table = pa.Table.from_pylist(
                [{"family": family, "key": key, "proof": proof} for key, proof in rows],
                schema=LINEAGE_SCHEMA,
            )
            outputs.append(
                {**_write_table(previous.root, table), "partition": [family, origin, slot]}
            )
    if sum(part["rows"] for part in outputs) != lineage["rows"]:
        raise IntegrityError("Migrated Gold lineage count differs")
    refs["__lineage"] = {"name": "__lineage", "parts": outputs, "rows": lineage["rows"]}
    return Evidence(
        previous.root,
        previous.publication_id,
        {**previous.document, "tables": list(refs.values()), "fragment_lineage": seconds},
    )


def update(snapshot, previous, family, after, schema, *, budget, seconds):
    """Return new fact and lineage descriptors without reopening untouched Parquet."""
    from ingesta_delta_lake.pipeline_rows import business_key, conflicting_keys

    before = previous.document["silver_refs"].get(family)
    refs = {r["name"]: r for r in previous.document["tables"]}
    if before is None and after is None:
        if refs[family]["rows"]:
            raise FullReadRequired
        return refs[family], [], 0
    if not {"parts", "parts_root"} & refs[family].keys() and refs[family]["rows"]:
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

    fact_ref, lineage_ref = refs[family], refs["__lineage"]
    indexed_facts = "parts_root" in fact_ref
    indexed_lineage = "family_roots" in lineage_ref
    fact_changes, lineage_changes = {}, {}
    facts = {tuple(part["partition"]): part for part in fact_ref.get("parts", [])}
    lineage = {
        tuple(part["partition"]): part
        for part in lineage_ref.get("parts", [])
        if part["partition"][0] == family
    }
    lineage_root = lineage_ref.get("family_roots", {}).get(family)

    def lineage_result(root):
        return (
            [{"family_index": family, "root": root}] if indexed_lineage else list(lineage.values())
        )

    scanned = 0
    if not by_partition:
        return fact_ref, lineage_result(lineage_root), count
    previous.read_stats[family]["changed_keys"] += len(changes)
    previous.read_stats[family]["rebuilt_partitions"] += len(by_partition)
    for key, edits in sorted(by_partition.items()):
        lineage_key = (family, *key)
        old_fact = (
            previous.index.find(fact_ref["parts_root"], key)
            if indexed_facts
            else facts.pop(key, None)
        )
        old_lineage = (
            previous.index.find(lineage_root, lineage_key)
            if indexed_lineage
            else lineage.pop(lineage_key, None)
        )
        fact_changes[key] = None
        lineage_changes[lineage_key] = None
        scanned += sum(p["rows"] for p in (old_fact, old_lineage) if p)
        if count + scanned > budget:
            raise IntegrityError("Affected Gold fragments exceed their reconciliation budget")
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
            fact_changes[key] = {
                **_write_table(previous.root, table),
                "partition": list(key),
                "time_bounds": _fragment_bounds(family, table),
            }
            if (part := _lineage_part(previous.root, family, key, proofs)) is not None:
                lineage_changes[lineage_key] = part

    if indexed_lineage:
        lineage_root = previous.index.updated(lineage_root, lineage_changes)
    else:
        lineage.update({key: value for key, value in lineage_changes.items() if value})
    if indexed_facts:
        node = previous.index.updated(fact_ref["parts_root"], fact_changes)
        return (
            dict(name=family, parts_root=node, rows=node["rows"] if node else 0),
            (lineage_result(lineage_root)),
            count,
        )
    facts.update({key: value for key, value in fact_changes.items() if value})
    descriptors = [item for _, item in sorted(facts.items())]
    row_count = sum(item["rows"] for item in descriptors)
    ref = (
        {"name": family, "parts": descriptors, "rows": row_count}
        if descriptors
        else {
            "name": family,
            **_write_table(previous.root, pa.Table.from_pylist([], schema=schema)),
        }
    )
    return ref, lineage_result(lineage_root), count
