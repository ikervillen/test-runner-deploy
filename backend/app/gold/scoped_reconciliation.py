"""Reconcile a pinned Silver family in atomic, individually bounded partitions."""

import json
from contextlib import suppress
from datetime import timedelta
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.dataset as ds

from .common import IntegrityError, atomic_json, digest, immutable_json, read_json
from .evidence import _fragment_bounds, _write_table
from .fragment_delta import EPOCH, _lineage_part


def _files(table, field):
    if table is None:
        return {}
    actions = pa.table(table.get_add_actions(flatten=True))
    if not actions.num_rows:
        return {}
    lo, hi = f"min.{field}", f"max.{field}"
    if lo not in actions.column_names or hi not in actions.column_names:
        raise IntegrityError("Silver reconciliation requires Delta time statistics")
    result = {}
    for batch in actions.select(["path", lo, hi]).to_batches(max_chunksize=4096):
        for path, first, last in zip(*(col.to_pylist() for col in batch), strict=True):
            if first is None or last is None:
                raise IntegrityError("Silver reconciliation found a file without time bounds")
            result[path] = (first, last)
    return result


def _changed_intervals(previous, table, ref, family, field, seconds, fact, *, rescan):
    from deltalake import DeltaTable
    from deltalake.exceptions import DeltaError

    after = _files(table, field)
    before = previous.document.get("silver_refs", {}).get(family)
    old = None
    if (
        not rescan
        and ref
        and before
        and before["table_id"] == ref["table_id"]
        and before["rejected_hashes"] == ref.get("rejected_hashes", [])
    ):
        # An unavailable Delta version requires a bounded rebuild.
        with suppress(DeltaError, OSError):
            old = _files(DeltaTable(table.table_uri, version=before["version"]), field)
    if old is None:
        ranges = after.values()
    else:
        ranges = [
            bounds
            for files, other in ((old, after), (after, old))
            for path, bounds in files.items()
            if other.get(path) != bounds
        ]
    delta = timedelta(seconds=seconds)
    intervals = [((first - EPOCH) // delta, (last - EPOCH) // delta) for first, last in ranges]
    if old is None and fact:
        intervals.append(fact["slots"])
    return _merge_intervals(intervals)


def _merge_intervals(intervals):
    combined = []
    for first, last in sorted(intervals):
        if combined and first <= combined[-1][1] + 1:
            combined[-1][1] = max(combined[-1][1], last)
        else:
            combined.append([first, last])
    return combined


def _scopes(intervals, origins, after):
    for first, last in intervals:
        for slot in range(max(first, after[0]) if after else first, last + 1):
            for origin in origins:
                if not after or (slot, origin) > tuple(after):
                    yield slot, origin


def _roots(previous, family):
    refs = {ref["name"]: ref for ref in previous.document["tables"]}
    facts, proofs = refs[family], refs["__lineage"]
    lineage = proofs.get("family_roots", {}).get(family)
    if "family_roots" not in proofs:
        parts = (
            {tuple(part["partition"]): part for part in previous.parts(proofs, families={family})}
            if "parts" in proofs
            else {}
        )
        lineage = previous.index.updated(None, parts)
    fact = facts.get("parts_root")
    if "parts_root" not in facts:
        parts = {tuple(part["partition"]): part for part in facts.get("parts", [])}
        if facts["rows"] and not parts:
            raise IntegrityError("Silver reconciliation requires segmented previous facts")
        fact = previous.index.updated(None, parts)
    return fact, lineage


def run(
    checkpoint,
    previous,
    own,
    family,
    schema,
    *,
    origins,
    seconds,
    budget,
    batch_size,
    certified=None,
    historical_id=None,
    archive_id=None,
    archive_rejections=(),
):
    from ingesta_delta_lake.pipeline_rows import business_key, review_hash
    from ingesta_delta_lake.pipeline_view import SilverView

    ref = own.manifest["tables"].get(family)
    rejected = set(ref.get("rejected_hashes", [])) if ref else set()
    rejected.update(archive_rejections)
    field = "FECHA_ON" if family == "LISTA_AR" else "FECHA"
    table = own.table(family) if ref else None
    identity = digest(
        [
            "silver-scoped-reconciliation-v1",
            previous.publication_id,
            own.manifest["publication_id"],
            family,
            sorted(origins),
            seconds,
            historical_id,
            archive_id,
            budget,
            sorted(rejected),
        ]
    )
    path = checkpoint / f"{identity}.json"
    plan_path = checkpoint / "plans" / f"{identity}.json"
    if path.exists():
        state = read_json(path)
        plan = read_json(plan_path)
        if state.get("identity") != identity or state.get("plan") != digest(plan):
            raise IntegrityError("Gold reconciliation checkpoint identity differs")
        intervals = plan["intervals"]
    else:
        fact, lineage = _roots(previous, family)
        intervals = _changed_intervals(
            previous,
            table,
            ref,
            family,
            field,
            seconds,
            fact,
            rescan=(
                previous.document.get("source_vector", [None])[-1] != historical_id
                or previous.document.get("archive") != archive_id
            ),
        )
        plan = {"intervals": intervals}
        immutable_json(plan_path, plan)
        state = dict(
            identity=identity,
            plan=digest(plan),
            fact=fact,
            lineage=lineage,
            cursor=None,
            examined=0,
            peak_rows=0,
            retained=False,
        )
        atomic_json(path, state)

    # Keep the validated Delta object and the Arrow dataset pinned for all scopes of this run.
    dataset = (
        table.to_pyarrow_dataset(
            parquet_read_options=ds.ParquetReadOptions(dictionary_columns=["origen_fenix"])
        )
        if table is not None
        else None
    )

    def batches(_family, *, batch_size, expression):
        if dataset is not None:
            yield from dataset.scanner(
                filter=expression, batch_size=batch_size, use_threads=False
            ).to_batches()

    view = SilverView(live=SimpleNamespace(manifest=own.manifest, batches=batches))
    for slot, origin in _scopes(intervals, sorted(origins), state["cursor"]):
        key = (origin, slot)
        lineage_key = (family, *key)
        stamp = EPOCH + slot * timedelta(seconds=seconds)
        date = ds.field(field)
        expression = (
            (ds.field("origen_fenix").cast(pa.string()) == origin)
            & (date >= stamp)
            & (date < stamp + timedelta(seconds=seconds))
        )
        existing = previous.index.find(state["fact"], key)
        old_lineage = previous.index.find(state["lineage"], lineage_key)
        old_count = sum(part["rows"] for part in (existing, old_lineage) if part)
        if old_count >= budget:
            raise IntegrityError("Gold reconciliation scope exceeds its work budget")
        result = view.rows(
            family, max_rows=budget - old_count, batch_size=batch_size, expression=expression
        )
        if result.conflicts:
            raise IntegrityError("Gold scoped reconciliation requires explicit conflict review")
        rows = {business_key(family, row): row for row in result.rows}
        proofs = result.lineages
        if existing:
            planned = len(rows) + existing["rows"] + (old_lineage["rows"] if old_lineage else 0)
            if planned > budget:
                raise IntegrityError("Gold reconciliation scope exceeds its work budget")
            old_proofs = {}
            if old_lineage:
                for row in previous._read_part("__lineage", old_lineage).to_pylist():
                    old_proofs.setdefault(row["key"], []).append(json.loads(row["proof"]))
            old = previous._read_part(family, existing)
            for row in old.to_pylist():
                identity = business_key(family, row)
                sources = old_proofs.get(identity, [])
                if identity in rows:
                    if (
                        row == rows[identity]
                        and sources
                        and ref
                        and all(p["source_table"] == ref["table_id"] for p in sources)
                    ):
                        proofs[identity] = sources
                    continue
                if review_hash(family, row) in rejected:
                    continue
                if certified and certified(family, key, identity, sources):
                    continue
                state["retained"] = True
                rows[identity], proofs[identity] = row, sources
            # Each scope owns its memory. Disable retention of decoded reconciled Parquet.
            previous._fragments.pop(family, None)
            previous._fragments.pop("__lineage", None)
        else:
            planned = len(rows)
        if planned > budget:
            raise IntegrityError("Gold reconciliation scope exceeds its work budget")
        part = None
        if rows:
            arrow = pa.Table.from_pylist(list(rows.values()), schema=schema)
            part = dict(
                _write_table(previous.root, arrow),
                partition=list(key),
                time_bounds=_fragment_bounds(family, arrow),
            )
        lineage = _lineage_part(previous.root, family, key, proofs)
        state.update(
            fact=previous.index.updated(state["fact"], {key: part}),
            lineage=previous.index.updated(state["lineage"], {lineage_key: lineage}),
            cursor=[slot, origin],
            examined=state["examined"] + 1,
            peak_rows=max(state["peak_rows"], planned),
        )
        atomic_json(path, state)
    root = state["fact"]
    return (
        dict(name=family, parts_root=root, rows=root["rows"] if root else 0),
        [{"family_index": family, "root": state["lineage"]}],
        state,
    )
