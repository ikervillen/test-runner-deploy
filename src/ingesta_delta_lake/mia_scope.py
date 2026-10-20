"""Bound MIA reads by complete origin/time contexts, retaining append evidence."""

from datetime import datetime

import pyarrow as pa
import pyarrow.dataset as ds

from .common import IntegrityError, digest


def scope_for(parsed, model):
    bounds, groups = {}, set()
    for entry in parsed.entries:
        row = {"origen_fenix": entry[0], **dict(zip(model.FIELDS, entry[1:], strict=True))}
        origin, stamp = row["origen_fenix"], row["FECHA"]
        start, end = bounds.get(origin, (stamp, stamp))
        bounds[origin] = min(start, stamp), max(end, stamp)
        groups.add(digest(model.group(row)))
    return {
        "ranges": [[origin, *bounds[origin]] for origin in sorted(bounds)],
        "groups": sorted(groups),
    }


def expression(scope):
    ranges = scope["ranges"] if isinstance(scope, dict) else scope
    result = ds.scalar(False)
    seen = set()
    for origin, start, end in ranges:
        if not origin or origin in seen:
            raise IntegrityError("Invalid MIA read scope")
        seen.add(origin)
        first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if first.tzinfo or last.tzinfo or first.microsecond or last.microsecond or first > last:
            raise IntegrityError("Invalid MIA read scope timestamps")
        result |= (
            (ds.field("origen_fenix") == origin)
            & (ds.field("FECHA") >= first)
            & (ds.field("FECHA") <= last)
        )
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
    actual, before = files(table), prepared["before_files"]
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
        dataset = table.to_pyarrow_dataset()
        if dataset.schema != schema:
            raise IntegrityError("Incompatible MIA schema")
        return dataset.scanner(
            filter=None if self.scope is None else expression(self.scope), batch_size=8192
        ).to_batches()

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
