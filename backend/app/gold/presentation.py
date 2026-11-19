"""Project complete Gold score/detail rows onto the established application contract."""

from dataclasses import asdict
from datetime import timedelta

from app.domain.customer_score import CustomerScoreComponent
from app.domain.score_math import _dar_threshold_label, _ScoreConfig, main_causes

from .scoring import LIVE_PARTIAL_MIA_FORMULA


def score_payload(score, tables, config, labels, profiles):
    identity = score["id_resultado"]

    def details(name):
        return [r for r in tables.get(name, []) if r["id_resultado"] == identity]

    parts = details("quality_score_components")
    components = [
        dict(
            component=r["componente"],
            label=r["componente"],
            score=r["score"],
            weight=r["peso"],
            contribution=r["contribucion"],
            details={
                "MIA": "Tasa de rechazo frente a la referencia MIA del corte.",
                "DAR": "Presencia de los defectos DAR aplicables al segmento.",
                "ZC": "Rendimiento de zona caliente frente al objetivo.",
            }.get(r["componente"], "Minutos-cavidad afectados frente a la capacidad disponible."),
        )
        for r in parts
    ]
    by_part = {r["componente"]: r for r in parts}
    capacity = next(
        (
            r
            for r in tables["ar_window_exposures"]
            if r["capacity_basis_id"] == by_part["DAR"]["basis_reference"]
        ),
        None,
    )
    available = capacity["tiempo_disponible_us"] / 60_000_000 if capacity else 0
    dar_amount = next(
        (
            r["tiempo_afectado_us"] / 60_000_000
            for r in tables["ar_window_bases"]
            if r["capacity_basis_id"] == by_part["DAR"]["basis_reference"]
            and r["ambito"] == "DAR_TOTAL"
        ),
        0,
    )
    dar_detail = details("dar_score_details")
    dar = by_part["DAR"]
    zc = details("zc_score_details")[0]
    warnings = (
        ["DAR provisional: pendiente de aprobación específica por segmento."]
        if score.get("configuration_status") == "PROVISIONAL_DAR"
        else []
    )
    if score.get("formula_version") == LIVE_PARTIAL_MIA_FORMULA:
        warnings.append("MIA pendiente. La valoración incluye los componentes evaluados.")
    return dict(
        total_penalty=score["total_penalty"],
        normalized_score=score["normalized_score"],
        evaluated_weight=score["evaluated_weight"],
        status=score["estado_interpretacion"],
        main_causes=[
            asdict(r)
            for r in main_causes(
                tuple(CustomerScoreComponent(**c) for c in components),
                score["total_penalty"],
                _ScoreConfig(config["version"], config),
            )
        ],
        components=components,
        mia_details=[
            dict(
                defect_code=int(r["codigo_defecto"]),
                defect_label=labels.get(("fenix_mia", r["codigo_defecto"]), r["codigo_defecto"]),
                rejected_count=r["rechazos"],
                inspected_count=r["inspeccionadas"],
                current_percent=r["current_percent"],
                historical_mean_percent=r["historical_mean_percent"],
                historical_sigma_percent=r["historical_sigma_percent"],
                z_score=r["z_score"],
                score=r["score"],
                weight=r["peso"],
                contribution=r["contribucion"],
                samples=r["muestras"],
            )
            for r in details("mia_score_details")
        ],
        aql_categories=[
            dict(
                category=r["categoria"],
                affected_cavity_minutes=r["affected_cavity_minutes"],
                total_cavity_minutes=r["total_cavity_minutes"],
                exposure_percent=r["exposure_percent"],
                aql_percent=r["aql_percent"],
                ratio=r["ratio"],
                score=r["score"],
                weight=r["peso"],
                contribution=r["contribucion"],
                top_defect_code=int(r["top_defect_code"]) if r["top_defect_code"] else None,
                top_defect_label=labels.get(("fenix_ar", r["top_defect_code"])),
            )
            for r in details("aql_score_details")
        ],
        dar=dict(
            event_count=sum(r["eventos"] for r in dar_detail),
            affected_minutes=dar_amount,
            total_cavity_minutes=available,
            exposure_percent=100 * dar_amount / available if available else 0,
            aql_percent=config["dar"].get("aql_percent"),
            score=dar["score"],
            weight=dar["peso"],
            contribution=dar["contribucion"],
            details="Unión temporal de eventos DAR por cavidad.",
            breakdown=[
                dict(
                    group=r["grupo_id"],
                    codes=sorted(
                        {int(p["codigo"]) for p in profiles if p["grupo_id"] == r["grupo_id"]}
                    ),
                    event_count=r["eventos"],
                    affected_minutes=r["affected_minutes"],
                    share_percent=r["share_percent"],
                    exposure_percent=r["exposure_percent"],
                    score=r["score"],
                    threshold_label=_dar_threshold_label(r["exposure_percent"], config["dar"]),
                )
                for r in dar_detail
            ],
        ),
        zc=dict(
            target_percent=zc["target_percent"],
            actual_percent=zc["actual_percent"],
            loss_points=zc["loss_points"],
            score=zc["score"],
            weight=zc["peso"],
            contribution=zc["contribucion"],
            windows=zc["muestras"],
        ),
        warnings=warnings,
    )


def shift_context(row):
    # Scheduled boundary defines the shift even when production starts mid-shift.
    start = row["fin"] - timedelta(hours=8)
    name = {6: "Manana", 14: "Tarde", 22: "Noche"}[start.hour]
    day = start.date()
    return dict(
        shift=f"{day}:{name}",
        operational_day=day.isoformat(),
        shift_name=name,
        start=row["inicio"].isoformat(),
        end=row["corte"].isoformat(),
    )
