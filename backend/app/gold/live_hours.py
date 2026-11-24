"""A bounded retry plan over the visible Gold publication."""

from datetime import timedelta
from types import SimpleNamespace

import pyarrow.dataset as ds

from .lanes import _clip, hour_holding
from .metrics import Window

RETRY_REASONS = {"SOURCE_COVERAGE_PENDING", "MIA_SOURCE_COVERAGE_PENDING"}


def retry_closures(previous, coverage, cutoff, budget, *, closed=None):
    from .pipeline import LIVE_FAMILIES, _coverage_ready

    start, _ = hour_holding(cutoff)
    lowest = start - timedelta(hours=7)
    field = ds.field
    rows = previous.rows(
        "publication_scope_status",
        expression=(
            (field("source").cast("string") == "quality_scores")
            & (field("period_start") >= lowest)
            & (field("period_start") <= start)
        ),
        file_pruning_predicate=[
            ("period_start", ">=", lowest),
            ("period_start", "<=", start),
            ("source", "=", "quality_scores"),
        ],
        max_rows=budget,
    )
    ids = sorted({r["id_produccion"] for r in rows if r["id_produccion"]})
    if not ids:
        return []
    by_id = {
        p["id_produccion"]: p
        for p in previous.rows(
            "productions",
            expression=field("id_produccion").cast("string").isin(ids),
            file_pruning_predicate=[("id_produccion", "in", ids)],
            max_rows=budget,
        )
    }
    probe = SimpleNamespace(manifest={"coverage": coverage})
    result = []
    for row in sorted(rows, key=lambda r: (r["period_start"], r["scope_id"])):
        identity, left = row["id_produccion"], row["period_start"]
        if identity not in by_id:
            continue
        production = by_id[identity]
        right = left + timedelta(hours=1)
        chunk = _clip(production, left, right, right)
        if chunk is None or chunk.cutoff > cutoff:
            continue
        window = Window(identity, "HOUR", left, right, chunk.cutoff)
        if row["scope_id"] != window.identity:
            continue
        reasons = set((row.get("reason_code") or "").split(";"))
        if row["period_end"] == chunk.cutoff and not reasons & RETRY_REASONS:
            if closed is not None and row["estado"] == "READY":
                closed.append(window.identity)
            continue
        if len(result) < 12 and _coverage_ready(probe, production, window, [], LIVE_FAMILIES):
            result.append([identity, left.isoformat(), right.isoformat(), chunk.cutoff.isoformat()])
        if len(result) == 12 and closed is None:
            break
    return result
