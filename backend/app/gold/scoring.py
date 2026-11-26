"""One score from complete compatible bases, using the legacy scalar formula module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from app.domain.score_config import SEVERITIES, validate_score_config
from app.domain.score_math import (
    _aql_band_score,
    _dar_score,
    _mia_absolute_rate_factor,
    _ScoreConfig,
    _sigma_score,
    _status,
    _zero_aql_score,
)

from .common import IntegrityError
from .metrics import Window


@dataclass(frozen=True)
class ScoreResult:
    tables: dict[str, list[dict]]
    pending: tuple[str, ...]


def validate_config(raw: dict) -> None:
    validate_score_config(raw)

    def numbers(value):
        if isinstance(value, dict):
            for item in value.values():
                numbers(item)
        elif isinstance(value, list):
            for item in value:
                numbers(item)
        elif isinstance(value, float) and not isfinite(value):
            raise IntegrityError("Configuration contains non-finite numbers")

    numbers(raw)
    for component in ("mia", "aql", "dar", "zc"):
        if not raw[component].get("enabled", True):
            raise IntegrityError("Official Gold publication requires every agreed score family")


LIVE_PARTIAL_MIA_FORMULA = "live-score-mia-pending-v1"
PARTIAL_FORMULA_PREFIX = "shared-score-partial-v2:"
SCORE_COMPONENTS = frozenset(("MIA", *SEVERITIES, "DAR", "ZC"))


def has_official_total(formula: str | None) -> bool:
    return not (
        formula == LIVE_PARTIAL_MIA_FORMULA or (formula or "").startswith(PARTIAL_FORMULA_PREFIX)
    )


def calculate(
    window: Window,
    *,
    mia: dict,
    ar: dict | None,
    zc: list[dict],
    reference: dict,
    config: dict,
    config_id: str,
    profile_confirmed: bool,
    coverage_ready: bool,
    calculation_time: datetime,
    line: str | None = None,
    ar_pending_reason: str = "ASSIGNED_CAPACITY_PENDING",
    configuration_status: str = "CONFIRMED",
    live_mia: bool = False,
    mia_coverage_ready: bool = True,
    component_coverage: dict[str, str] | None = None,
) -> ScoreResult:
    """Evaluate credited components; partial rows carry an explicit membership and status."""
    validate_config(config)
    independent = component_coverage is not None
    blockers = component_coverage or {}
    ar_components = ar.get("pending_by_component", {}) if independent and ar else {}
    ar_pending = [blockers["AR"]] if "AR" in blockers else []
    dar_pending, zc_pending = [], [blockers["ZC"]] if "ZC" in blockers else []
    if not profile_confirmed:
        dar_pending.append("MODEL_SEGMENT_DAR_PROFILE_PENDING")
    if "AR_NOT_APPLICABLE" not in ar_pending and (
        ar is None or ar["exposure"]["tiempo_disponible_us"] <= 0
    ):
        ar_pending.append(ar_pending_reason)
    if "ZC_NOT_APPLICABLE" not in zc_pending and (not zc or sum(r["teoricos"] for r in zc) <= 0):
        zc_pending.append("ZC_THEORETICAL_BASE_MISSING")
    stats = {(r["espacio_defecto"], r["codigo_defecto"]): r for r in reference["values"]}
    mia_pending = [blockers["MIA"]] if "MIA" in blockers else []
    if "MIA_NOT_APPLICABLE" not in mia_pending and mia["exposure"]["inspeccionadas"] <= 0:
        mia_pending.append("LISTA_RECH_BASE_MISSING")
    if not independent and not mia_coverage_ready:
        mia_pending.append("MIA_SOURCE_COVERAGE_PENDING")
    for rate in mia["rates"] if "MIA_NOT_APPLICABLE" not in mia_pending else ():
        stat = stats.get((rate["espacio_defecto"], rate["codigo_defecto"]))
        if stat is None or stat["estado"] != "READY":
            mia_pending.append(f"MIA_REFERENCE_PENDING:{rate['codigo_defecto']}")
    pending = ar_pending + dar_pending + zc_pending + list(ar_components.values())
    if not coverage_ready:
        pending.append("SOURCE_COVERAGE_PENDING")
    reasons = tuple(sorted(set(pending + mia_pending)))
    if not independent and (pending or (mia_pending and not live_mia)):
        return ScoreResult({}, reasons)
    evaluate_mia = not mia_pending
    score_config = _ScoreConfig(int(config["version"]), config)
    identity = window.identity
    tables: dict[str, list[dict]] = {
        name: []
        for name in (
            "quality_scores",
            "quality_score_components",
            "mia_score_details",
            "aql_score_details",
            "dar_score_details",
            "zc_score_details",
        )
    }

    def component(name, score, weight, basis):
        tables["quality_score_components"].append(
            {
                "id_resultado": identity,
                "componente": name,
                "score": round(score, 4),
                "peso": weight,
                "contribucion": round(score * weight, 4),
                "basis_reference": basis,
            }
        )

    mia_weight = float(config["weights"]["mia_defect"])
    for rate in mia["rates"] if evaluate_mia else ():
        stat = stats[(rate["espacio_defecto"], rate["codigo_defecto"])]
        mean, sigma = stat["media_percent"], stat["sigma_percent"]
        z = (rate["percent"] - mean) / sigma if sigma > 0 else None
        value = (
            _sigma_score(z, score_config)
            if z is not None
            else float(config["mia"]["zero_sigma_policy"]["fixed_score"])
            if rate["percent"] > mean
            else 0.0
        )
        value *= _mia_absolute_rate_factor(rate["percent"], score_config)
        tables["mia_score_details"].append(
            {
                "id_resultado": identity,
                "espacio_defecto": rate["espacio_defecto"],
                "codigo_defecto": rate["codigo_defecto"],
                "rechazos": rate["rechazos"],
                "inspeccionadas": rate["inspeccionadas"],
                "current_percent": round(rate["percent"], 4),
                "historical_mean_percent": round(mean, 4),
                "historical_sigma_percent": round(sigma, 4),
                "z_score": round(z, 4) if z is not None else None,
                "score": round(value, 4),
                "peso": mia_weight,
                "contribucion": round(value * mia_weight, 4),
                "muestras": stat["muestras"],
            }
        )
    if evaluate_mia:
        mia_total = sum(r["contribucion"] for r in tables["mia_score_details"])
        weight = mia_weight * len(tables["mia_score_details"])
        # Empty observed defect set has no per-defect evaluated weight, as in legacy.
        component(
            "MIA", mia_total / max(weight, 1.0), weight, mia["exposure"]["inspected_basis_id"]
        )
        tables["quality_score_components"][-1]["contribucion"] = round(mia_total, 4)
    if ar is not None and not ar_pending:
        available = ar["exposure"]["tiempo_disponible_us"]
        by = {(r["ambito"], r["codigo_ambito"]): r for r in ar["amounts"]}
        for category in SEVERITIES:
            if category in ar_components:
                continue
            affected = by.get(("CATEGORY", category), {}).get("tiempo_afectado_us", 0)
            exposure = affected / available * 100
            limit = float(config["aql"]["category_limits_percent"][category])
            if limit <= 0:
                value, ratio = _zero_aql_score(exposure, config["aql"]), None
            else:
                ratio = exposure / limit
                value = _aql_band_score(ratio)
            weight = float(config["weights"]["categories"][category])
            component(category, value, weight, ar["exposure"]["capacity_basis_id"])
            tables["aql_score_details"].append(
                {
                    "id_resultado": identity,
                    "categoria": category,
                    "affected_cavity_minutes": round(affected / 60_000_000, 4),
                    "total_cavity_minutes": round(available / 60_000_000, 4),
                    "exposure_percent": round(exposure, 4),
                    "aql_percent": limit,
                    "ratio": round(ratio, 4) if ratio is not None else None,
                    "score": round(value, 4),
                    "peso": weight,
                    "contribucion": round(value * weight, 4),
                    "top_defect_code": ar.get("top_defects", {}).get(category),
                }
            )
        if not dar_pending and "DAR" not in ar_components:
            dar_amount = by.get(("DAR_TOTAL", "DAR"), {}).get("tiempo_afectado_us", 0)
            dar_value = _dar_score(dar_amount / available * 100, config["dar"])
            component(
                "DAR",
                dar_value,
                float(config["weights"]["dar"]),
                ar["exposure"]["capacity_basis_id"],
            )
            for r in ar["amounts"]:
                if r["ambito"] != "DAR_GROUP":
                    continue
                affected = r["tiempo_afectado_us"]
                exposure = affected / available * 100
                tables["dar_score_details"].append(
                    {
                        "id_resultado": identity,
                        "grupo_id": r["codigo_ambito"],
                        "eventos": r["eventos"],
                        "affected_minutes": round(affected / 60_000_000, 4),
                        "share_percent": round(100 * affected / dar_amount, 4)
                        if dar_amount
                        else 0.0,
                        "exposure_percent": round(exposure, 4),
                        "score": round(_dar_score(exposure, config["dar"]), 4),
                        "threshold_rule_id": config_id,
                    }
                )
    if not zc_pending:
        actual = sum(r["rueda"] for r in zc) / sum(r["teoricos"] for r in zc) * 100
        targets = config["zc"]["target_percent"]
        target = float(targets.get("by_line", {}).get(line, targets["default"]))
        value = min(10.0, max(0.0, target - actual) / float(config["zc"]["penalty_divisor_points"]))
        weight = float(config["weights"]["zc"])
        component("ZC", value, weight, zc[0]["id_base_zc"])
        tables["zc_score_details"].append(
            {
                "id_resultado": identity,
                "target_percent": target,
                "actual_percent": round(actual, 4),
                "loss_points": round(max(0.0, target - actual), 4),
                "score": round(value, 4),
                "peso": weight,
                "contribucion": round(value * weight, 4),
                "muestras": sum(r["muestras"] for r in zc),
                "basis_reference": zc[0]["id_base_zc"],
            }
        )
    total = round(sum(r["contribucion"] for r in tables["quality_score_components"]), 4)
    weight = round(sum(r["peso"] for r in tables["quality_score_components"]), 4)
    if not tables["quality_score_components"]:
        return ScoreResult({}, reasons or ("NO_EVALUABLE_WEIGHT",))
    if weight <= 0 and not (independent and reasons):
        return ScoreResult({}, ("NO_EVALUABLE_WEIGHT",))
    partial = independent and bool(reasons)
    formula = (
        PARTIAL_FORMULA_PREFIX
        + ",".join(sorted(r["componente"] for r in tables["quality_score_components"]))
        if partial
        else "shared-score-math-v1"
        if evaluate_mia
        else LIVE_PARTIAL_MIA_FORMULA
    )
    tables["quality_scores"].append(
        {
            "id_resultado": identity,
            **window.key,
            "config_id": config_id,
            "formula_version": formula,
            "reference_id": reference["sets"][0]["reference_id"]
            if evaluate_mia and reference["sets"]
            else None,
            "total_penalty": total,
            "normalized_score": round(total / weight, 4) if weight else 0.0,
            "evaluated_weight": weight,
            "estado_interpretacion": "partial" if partial else _status(total, score_config),
            "fecha_calculo": calculation_time,
            "configuration_status": configuration_status,
        }
    )
    return ScoreResult(tables, reasons)
