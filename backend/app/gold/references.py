"""Statistical populations are closed fabrications, not individual machine samples."""

from __future__ import annotations

from collections import defaultdict

from app.domain.score_math import _sample_sigma

from .common import digest


def population(
    target: dict, candidates: list[dict], segments: dict[str, dict]
) -> tuple[str, str | None, list[dict]]:
    eligible = [
        p
        for p in candidates
        if p["estado_produccion"] == "CLOSED"
        and p["estado_dato"] == "VALID"
        and p["id_produccion"] != target["id_produccion"]
    ]
    own = [
        p
        for p in eligible
        if p["codigo_modelo_botella"] == target["codigo_modelo_botella"]
        and target["codigo_modelo_botella"] is not None
    ]
    if own:
        return "MODEL", target["codigo_modelo_botella"], own
    segment = segments.get(target["codigo_modelo_botella"], {})
    value = segment.get("segmento_calidad")
    if segment.get("estado_correspondencia") != "CONFIRMED" or value is None:
        return "SEGMENT", None, []
    return (
        "SEGMENT",
        value,
        [
            p
            for p in eligible
            if segments.get(p["codigo_modelo_botella"], {}).get("segmento_calidad") == value
            and segments[p["codigo_modelo_botella"]]["estado_correspondencia"] == "CONFIRMED"
        ],
    )


def build(
    target: dict,
    candidates: list[dict],
    segments: dict[str, dict],
    rates_by_production: dict[str, list[dict]],
    *,
    input_version: str,
    catalog_version: str,
    selection_id: str = "all",
    minimum: int = 3,
) -> dict:
    level, value, members = population(target, candidates, segments)
    # A same-model history never falls back to segment merely for too few samples.
    # Uncalculable rates remain excluded/pending, not fake zero samples.
    members = [p for p in members if p["id_produccion"] in rates_by_production]
    identity = digest(
        [
            "production-rates-v1",
            level,
            value,
            selection_id,
            catalog_version,
            sorted((p["id_produccion"], rates_by_production[p["id_produccion"]]) for p in members),
        ]
    )
    samples = defaultdict(list)
    for p in members:
        for row in rates_by_production[p["id_produccion"]]:
            if row["percent"] is not None:
                samples[(row["espacio_defecto"], row["codigo_defecto"])].append(row["percent"])
    values = []
    for (space, code), series in sorted(samples.items()):
        mean = sum(series) / len(series)
        values.append(
            {
                "reference_id": identity,
                "espacio_defecto": space,
                "codigo_defecto": code,
                "muestras": len(series),
                "media_percent": mean if len(series) >= minimum else None,
                "sigma_percent": _sample_sigma(series, mean) if len(series) >= minimum else None,
                "estado": "READY" if len(series) >= minimum else "WAITING_INPUT",
            }
        )
    return {
        "sets": [
            {
                "reference_id": identity,
                "nivel": level,
                "valor": value or "UNRESOLVED",
                "seleccion_id": selection_id,
                "reference_rule_id": "closed-model-first-v1",
                "catalog_version": catalog_version,
                "estado": "READY" if members else "WAITING_INPUT",
            }
        ],
        "members": [
            {
                "reference_id": identity,
                "id_produccion": p["id_produccion"],
                "rates_version": digest(rates_by_production[p["id_produccion"]]),
            }
            for p in members
        ],
        "values": values,
    }
