"""Read-only scenario recalculation over published Gold amounts.

A scenario is a query with physical filters, a partial branch selection or parameter
overrides. It reuses the same scalar engine as the publisher and never writes results.
"""

from __future__ import annotations

from datetime import datetime

from . import references
from .common import PendingBasis, digest
from .metrics import Window, ar_bases, mia_bases, production_windows, zc_bases
from .scoring import calculate

DETAILS = (
    "quality_score_components",
    "mia_score_details",
    "aql_score_details",
    "dar_score_details",
    "zc_score_details",
)


def rates_from_amounts(amounts, exposure, codes=None):
    if exposure["inspeccionadas"] <= 0:
        return []
    return [
        dict(
            espacio_defecto=r["espacio_defecto"],
            codigo_defecto=r["codigo_defecto"],
            rechazos=r["rechazos"],
            inspeccionadas=exposure["inspeccionadas"],
            percent=100 * r["rechazos"] / exposure["inspeccionadas"],
        )
        for r in amounts
        if r["inspected_basis_id"] == exposure["inspected_basis_id"]
        and r["rechazos"] > 0
        and (codes is None or r["codigo_defecto"] in codes)
    ]


def _reference_rates(q, members, need_detail, filters, codes, rejections, inspections):
    """Historical MIA rates for the reference population, from amounts or from detail."""
    production_exposures = {
        r["id_produccion"]: r
        for r in q.rows("mia_window_exposures")
        if r["tipo_ventana"] == "PRODUCTION"
    }
    amounts = q.rows("mia_window_bases")
    rates = {}
    for identity in members:
        exposure = production_exposures.get(identity)
        if exposure is None or exposure["inspeccionadas"] <= 0:
            continue
        if need_detail:
            window = Window(
                identity,
                exposure["tipo_ventana"],
                exposure["inicio"],
                exposure["fin"],
                exposure["corte"],
            )
            rates[identity] = mia_bases(
                rejections, inspections, window, selection=filters, defect_codes=codes
            )["rates"]
        else:
            rates[identity] = rates_from_amounts(amounts, exposure, codes)
    return rates, amounts


def recalculate(q, production, payload, config, bundle, mappings, scopes, branches, valid):
    """Scores for one production under a scenario; returns (scores, detail tables, reasons)."""
    identity = production["id_produccion"]
    exposures = q.rows("mia_window_exposures", [identity])
    members = {r["id_produccion"] for r in q.rows("mia_reference_members")}
    candidates = q.rows("productions")
    filters = {
        k: v for k, v in payload.selection.filters.model_dump().items() if k != "defect_codes" and v
    }
    codes = {str(c) for c in payload.selection.filters.defect_codes} or None
    partial_branches = bool(branches and branches != valid)
    need_detail = bool(filters or partial_branches)
    inspections = q.rows("mia_inspections", members | {identity}) if need_detail else []
    rejections = q.rows("mia_rejections", members | {identity}) if need_detail else []
    rates, amounts = _reference_rates(
        q, members, need_detail, filters, codes, rejections, inspections
    )
    ref = references.build(
        production,
        candidates,
        mappings,
        rates,
        input_version=q.snapshot.publication_id,
        catalog_version=digest(bundle),
        minimum=config["mia"]["historical_scope"]["min_samples"],
    )
    capacity = q.rows("production_capacity", [identity])
    events = q.rows("ar_intervals", [identity])
    links = q.rows("zc_production_links", [identity])
    zc_ids = {r["id_observacion"] for r in links}
    counters = q.rows("zc_observations", zc_ids, "id_observacion")
    result_tables = {name: [] for name in DETAILS}
    result_tables.update(ar_window_bases=[], ar_window_exposures=[])
    scores, reasons = [], []
    cutoff = max((r["corte"] for r in exposures), default=None)
    if cutoff is None:
        return [], result_tables, ["NO_OPERATIVE_WINDOW"]
    mapping = mappings.get(production["codigo_modelo_botella"], {})
    selection_id = digest(payload.selection.model_dump(mode="json"))
    exposure_index = {(r["tipo_ventana"], r["inicio"], r["corte"]): r for r in exposures}
    selected_filters = {
        **filters,
        **({"branches": [int(b.rsplit("-", 1)[1]) for b in branches]} if partial_branches else {}),
    }
    coverage_pending = {
        s["scope_id"] for s in scopes if "SOURCE_COVERAGE_PENDING" in (s["reason_code"] or "")
    }
    for original in production_windows(production, cutoff):
        w = Window(
            identity, original.kind, original.start, original.end, original.cutoff, selection_id
        )
        e = exposure_index.get((w.kind, w.start, w.cutoff))
        if e is None:
            reasons.append("WINDOW_NOT_PUBLISHED")
            continue
        if need_detail:
            mia = mia_bases(
                rejections, inspections, w, selection=selected_filters, defect_codes=codes
            )
        else:
            mia = {"exposure": {**e, **w.key}, "rates": rates_from_amounts(amounts, e, codes)}
        ar_reason = "ASSIGNED_CAPACITY_PENDING"
        try:
            ar = ar_bases(events, capacity, w)
        except PendingBasis as exc:
            ar, ar_reason = None, exc.code
        if ar:
            result_tables["ar_window_exposures"].append(ar["exposure"])
            result_tables["ar_window_bases"].extend(ar["amounts"])
        result = calculate(
            w,
            mia=mia,
            ar=ar,
            zc=zc_bases(counters, w, zc_ids),
            reference=ref,
            config=config,
            config_id=digest([config, bundle]),
            profile_confirmed=mapping.get("estado_correspondencia") == "CONFIRMED"
            and mapping.get("estado_dar") != "PENDING",
            coverage_ready=original.identity not in coverage_pending,
            calculation_time=datetime.now(),
            line=production["linea"],
            ar_pending_reason=ar_reason,
            configuration_status="PROVISIONAL_DAR"
            if mapping.get("estado_dar") == "PROVISIONAL"
            else "CONFIRMED",
        )
        reasons.extend(result.pending)
        scores.extend(result.tables.get("quality_scores", []))
        for name in DETAILS:
            result_tables[name].extend(result.tables.get(name, []))
    return scores, result_tables, reasons
