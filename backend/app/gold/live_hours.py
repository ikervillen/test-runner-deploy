"""A bounded retry plan over the visible Gold publication."""

from collections import defaultdict
from datetime import timedelta
from functools import cache
from types import SimpleNamespace

import pyarrow.dataset as ds

from .attribution import ACCEPTED, source_id
from .common import IntegrityError, digest
from .evidence import Evidence
from .lanes import _clip, hour_holding
from .metrics import Window

RETRY_REASONS = {"SOURCE_COVERAGE_PENDING", "MIA_SOURCE_COVERAGE_PENDING"}


def retry_closures(
    previous,
    coverage,
    cutoff,
    budget,
    *,
    closed=None,
    ar_changed=None,
    waiting=None,
    prepare=None,
    older=None,
):
    from .pipeline import LIVE_FAMILIES, _coverage_ready

    start, _ = hour_holding(cutoff)
    lowest = start - timedelta(hours=7)
    field = ds.field
    upper = start + timedelta(microseconds=1)
    if older:
        lowest, upper = older
    pending = (field("estado").cast("string") == "WAITING_DEPENDENCY") if older else ds.scalar(True)
    rows = previous.rows(
        "publication_scope_status",
        expression=(
            (field("source").cast("string") == "quality_scores")
            & (field("period_start") >= lowest)
            & (field("period_start") < upper)
            & pending
        ),
        file_pruning_predicate=[
            ("period_start", ">=", lowest),
            ("period_start", "<", upper),
            ("source", "=", "quality_scores"),
            *([("estado", "=", "WAITING_DEPENDENCY")] if older else []),
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
        if older and not reasons & RETRY_REASONS:
            continue
        revised = ar_changed(row, window) if ar_changed else False
        if not revised and row["period_end"] == chunk.cutoff and not reasons & RETRY_REASONS:
            if closed is not None and row["estado"] == "READY":
                closed.append(window.identity)
            continue
        if prepare is not None:
            prepare(window.start, window.cutoff, origin=production["origen_fenix"])
        if (older or ar_changed or len(result) < 12) and _coverage_ready(
            probe, production, window, [], LIVE_FAMILIES
        ):
            result.append([identity, left.isoformat(), right.isoformat(), chunk.cutoff.isoformat()])
        elif revised:
            if waiting is not None:
                waiting.append(window.identity)
            if closed is not None and row["estado"] == "READY":
                closed.append(window.identity)
        if len(result) == 12 and closed is None and not ar_changed and not older:
            break
    return result


def ar_pin(document, silver_refs):
    """The dependencies that can change the accepted interpretation of an AR row."""
    refs = {
        key: silver_refs.get(key)
        for key in (
            "LISTA_AR",
            "SIP_DEFECTO_STATUS",
            "SIP_DEFECTO_AR",
            "MP9_CAMBIO_MODELO",
            "CATALOGO_PRODUCCIONES",
            "SIP_MODELO",
        )
    }
    return digest(
        [
            refs,
            document["registry"],
            document["association_cases"],
            document["bundle"]["catalog_composition"],
            document["bundle"]["cases"].get("LISTA_AR", []),
        ]
    )


def _ar_by_production(rows, decisions):
    assigned = {
        d["source_id"]: d["productions"][0]
        for d in decisions
        if d["source_family"] == "LISTA_AR"
        and d["status"] in ACCEPTED
        and len(d["productions"]) == 1
        and d["zone"] in {"ZF", "ZC"}
    }
    result = defaultdict(list)
    for row in rows:
        key = source_id("LISTA_AR", row)
        if key in assigned:
            result[assigned[key]].append((key, row))
    return result


def _ar_window(rows, categories, window):
    relevant = []
    for key, row in rows:
        left = max(window.start, row["FECHA_ON"])
        right = min(window.cutoff, row["FECHA_OFF"] or window.cutoff)
        if left < right:
            relevant.append((key, left, right, categories.get(str(row["COD_AR"]))))
    return sorted(relevant)


def ar_revisions(root, visible, document, current, cutoff, budget):
    """Compare a score with its own immutable inputs, including moves and deletions."""
    low = hour_holding(cutoff)[0] - timedelta(hours=7)
    plan_bounds = (low, cutoff)
    legacy_categories = document.get("legacy_ar_categories")
    if legacy_categories is None:
        legacy_categories = {
            r["codigo"]: r["categoria"]
            for r in visible.rows(
                "defect_catalog",
                expression=ds.field("espacio_codigo").cast("string") == "fenix_ar",
                max_rows=budget,
            )
        }
        document["legacy_ar_categories"] = legacy_categories
    work = 0
    baseline = document.pop("ar_window_cache", {})
    remembered = {}
    document["ar_window_cache"] = remembered
    revisions = {}
    document["ar_scope_changes"] = revisions

    @cache
    def inputs(identity):
        path = root / "inputs"
        if not (path / f"{identity}.json").is_file():
            path = root / "published" / "evidence"
        return Evidence.open(path, identity, families={"LISTA_AR", "observation_assignments"})

    @cache
    def old_assignments(identity):
        nonlocal work
        evidence = inputs(identity)
        rows = evidence.rows("LISTA_AR", window=plan_bounds, max_rows=budget - work)
        decisions = evidence.rows(
            "observation_assignments",
            expression=ds.field("source_family").cast("string") == "LISTA_AR",
            max_rows=budget - work,
        )
        work += sum(counts["rows_decoded"] for counts in evidence.read_stats.values())
        if work >= budget:
            raise IntegrityError("Hourly AR revision exceeds its explicit row budget")
        return _ar_by_production(rows, decisions)

    @cache
    def current_assignments():
        return _ar_by_production(*current(plan_bounds))

    def changed(scope, window):
        identity = scope.get("evidence_reference")
        key = digest([window.identity, window.cutoff, identity])
        before = baseline.get(key)
        if before is None:
            prior = inputs(identity)
            pinned = prior.document.get("ar_categories", legacy_categories)
            if (
                prior.document.get("ar_pin") == document["ar_pin"]
                and pinned == document["ar_categories"]
            ):
                return False
            before = digest(
                _ar_window(old_assignments(identity)[window.production_id], pinned, window)
            )
        remembered[key] = before
        after = _ar_window(
            current_assignments()[window.production_id], document["ar_categories"], window
        )
        fingerprint = digest(after)
        if before == fingerprint:
            return False
        revisions[window.identity] = {
            "previous_evidence": identity,
            "production": window.production_id,
            "cutoff": window.cutoff.isoformat(),
            "before": before,
            "after": fingerprint,
        }
        return True

    return changed
