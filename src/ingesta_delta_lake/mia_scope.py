"""Bound MIA reads by complete origin/time contexts, retaining append evidence."""

from collections import defaultdict
from datetime import datetime

import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import DeltaTable

from .common import IntegrityError, digest


def scope_for(parsed, model, *, exact_periods=False, inspection_signatures=False):
    bounds, groups, signatures = {}, set(), set()
    periods = defaultdict(set)
    for entry in parsed.entries:
        row = {"origen_fenix": entry[0], **dict(zip(model.FIELDS, entry[1:], strict=True))}
        origin, stamp = row["origen_fenix"], row["FECHA"]
        start, end = bounds.get(origin, (stamp, stamp))
        bounds[origin] = min(start, stamp), max(end, stamp)
        signature = model.group(row)
        groups.add(digest(signature))
        if inspection_signatures:
            signatures.add(signature)
        if exact_periods:
            periods[origin].add(stamp)
    scope = {
        "ranges": [[origin, *bounds[origin]] for origin in sorted(bounds)],
        "groups": sorted(groups),
    }
    if exact_periods:
        scope["periods"] = {origin: sorted(stamps) for origin, stamps in periods.items()}
    if inspection_signatures:
        scope["signatures"] = [list(value) for value in sorted(signatures, key=digest)]
    return scope


def expression(scope):
    ranges = scope["ranges"] if isinstance(scope, dict) else scope
    periods = scope.get("periods") if isinstance(scope, dict) else None
    result = ds.scalar(False)
    seen = set()
    for origin, start, end in ranges:
        if not origin or origin in seen:
            raise IntegrityError("Invalid MIA read scope")
        seen.add(origin)
        first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if first.tzinfo or last.tzinfo or first.microsecond or last.microsecond or first > last:
            raise IntegrityError("Invalid MIA read scope timestamps")
        date = ds.field("FECHA")
        in_range = (date >= first) & (date <= last)
        if periods is not None:
            stamps = [datetime.fromisoformat(value) for value in periods.get(origin, [])]
            if not stamps or any(
                stamp.tzinfo or stamp.microsecond or not first <= stamp <= last for stamp in stamps
            ):
                raise IntegrityError("Invalid exact MIA read periods")
            in_range &= date.isin(pa.array(stamps, type=pa.timestamp("us")))
        result |= (ds.field("origen_fenix").cast(pa.string()) == origin) & in_range
    if periods is not None and set(periods) != seen:
        raise IntegrityError("Exact MIA read periods differ from their declared origins")
    if isinstance(scope, dict) and "signatures" in scope:
        exact = ds.scalar(False)
        for signature in scope["signatures"]:
            origin, stamp, model, line, machine, cavity = signature
            if digest(signature) not in scope["groups"] or origin not in seen:
                raise IntegrityError("Invalid LISTA_RECH inspection signature")
            field = ds.field("CAVIDAD").cast(pa.string())
            exact |= (
                (ds.field("origen_fenix").cast(pa.string()) == origin)
                & (ds.field("FECHA") == datetime.fromisoformat(stamp))
                & (ds.field("MOLDE") == model)
                & (ds.field("LINEA") == line)
                & (ds.field("COD_MAQ_ZF") == machine)
                & (field.is_null() if cavity is None else field == cavity)
            )
        result &= exact
    return result


def files(table):
    if table is None:
        return []
    actions = pa.table(table.get_add_actions())
    result = sorted(
        zip(
            actions["path"].to_pylist(),
            actions["size_bytes"].to_pylist(),
            actions["num_records"].to_pylist(),
            strict=True,
        )
    )
    if any(count is None for _, _, count in result):
        raise IntegrityError("MIA append requires Delta row-count statistics")
    return [list(row) for row in result]


def verify_files(table, prepared, *, after):
    actual = files(table)
    if "before_files_hash" in prepared:
        base_version = prepared["before_version"]
        before = actual
        if after:
            before = (
                files(DeltaTable(table.table_uri, version=base_version))
                if base_version is not None
                else []
            )
        if digest(before) != prepared["before_files_hash"]:
            raise IntegrityError("MIA snapshot file manifest changed")
    else:
        before = prepared["before_files"]
    if not after:
        if actual != before:
            raise IntegrityError("MIA snapshot file manifest changed")
        return
    old = {r[0]: r for r in before}
    current = {r[0]: r for r in actual}
    if any(current.get(path) != row for path, row in old.items()):
        raise IntegrityError("MIA append replaced previously confirmed files")
    if sum(r[2] for path, r in current.items() if path not in old) != len(prepared["inserts"]):
        raise IntegrityError("MIA append row count differs from its durable intention")


class ScopedRead:
    scope = None
    scope_groups = None

    def set_scope(self, scope):
        expression(scope)  # Validate even an empty/new destination.
        self.scope = scope
        self.scope_groups = set(scope["groups"]) if isinstance(scope, dict) else None

    def includes(self, row):
        return self.scope_groups is None or digest(self.model.group(row)) in self.scope_groups

    def batches(self, table, schema):
        # MERGE files may carry Arrow string_view metadata. Dictionary decoding
        # keeps Arrow's equality kernels compatible while preserving file/time
        # pruning, bounded batches and the published (plain string) schema.
        dataset = table.to_pyarrow_dataset(
            parquet_read_options=ds.ParquetReadOptions(dictionary_columns=["origen_fenix"])
        )
        decoded = pa.schema(
            [
                f.with_type(f.type.value_type) if pa.types.is_dictionary(f.type) else f
                for f in dataset.schema
            ]
        )
        if decoded != schema:
            raise IntegrityError("Incompatible MIA schema")
        for batch in dataset.scanner(
            filter=None if self.scope is None else expression(self.scope), batch_size=8192
        ).to_batches():
            yield batch.cast(schema)

    def validate_scope(self, rows):
        if self.scope is None:
            return
        ranges = self.scope["ranges"] if isinstance(self.scope, dict) else self.scope
        bounds = {o: (start, end) for o, start, end in ranges}
        for row in rows:
            dates = bounds.get(row["origen_fenix"])
            if dates is None or not dates[0] <= row["FECHA"] <= dates[1] or not self.includes(row):
                raise IntegrityError("MIA insertion outside its validated read scope")


def group_holds(cases):
    groups, origins = set(), set()
    for case in cases:
        if "group" in case:
            groups.add(digest(case["group"]))
        else:
            refs = case.get("evidence", {})
            refs = refs if isinstance(refs, list) else [refs]
            origins.update(r["origin"] for r in refs if r.get("origin"))
    return sorted(groups), sorted(origins)
