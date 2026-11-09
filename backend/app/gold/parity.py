"""Real-data equivalence of Gold MIA amounts and shared formulas against E00.

The historical reference population deliberately follows the frozen LEGACY rules
here. This is a validation oracle, never a production fallback for D06/P01.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pyarrow.parquet as pq

from app.domain.score_math import (
    _mia_absolute_rate_factor,
    _sample_sigma,
    _ScoreConfig,
    _sigma_score,
)
from app.infrastructure.repositories.catalog import CsvCatalogRepository

from .common import IntegrityError, atomic_json
from .metrics import Window, mia_bases


def _amounts(root: Path, p, filters) -> dict:
    inspections = pq.read_table(
        root / "inspeccionadas_totales_parquet" / p.line / f"{p.id}.parquet"
    ).to_pylist()
    source = []
    for branch in p.branches:
        source.extend(pq.read_table(branch.path).to_pylist())
    start = min(
        [r["timestamp"] for r in inspections] + [r["LISTA_DEFECTOS_FECHA_REAL"] for r in source]
    ) - timedelta(minutes=10)
    end = max(
        [r["timestamp"] for r in inspections] + [r["LISTA_DEFECTOS_FECHA_REAL"] for r in source]
    )
    seen = []
    for r in inspections:
        seen.append(
            {
                "id_produccion": p.id,
                "linea": p.line,
                "ramal": r["branch_code"],
                "periodo_inicio": r["timestamp"] - timedelta(minutes=10),
                "periodo_fin": r["timestamp"],
                "codigo_maquina_inspeccion": r["zf_machine_code"],
                "cavidad": r["cavity"],
                "seccion": r["section"],
                "color": r["model_color"],
                "inspeccionadas": int(r["inspected_total"]),
            }
        )
    rejected = []
    for r in source:
        rejected.append(
            {
                "id_produccion": p.id,
                "linea": p.line,
                "ramal": r["LISTA_DEFECTOS_RAMAL"],
                "periodo_inicio": r["LISTA_DEFECTOS_FECHA_REAL"] - timedelta(minutes=10),
                "periodo_fin": r["LISTA_DEFECTOS_FECHA_REAL"],
                "codigo_maquina_inspeccion": r["LISTA_DEFECTOS_COD_MAQ_ZF"],
                "cavidad": r["LISTA_DEFECTOS_CAVIDAD_TOTAL"],
                "seccion": r["LISTA_DEFECTOS_SECCION"],
                "color": r["LISTA_DEFECTOS_MODELO_COLOR"],
                "espacio_defecto": "legacy_mia",
                "codigo_defecto": str(r["LISTA_DEFECTOS_COD_DEF"]),
                "rechazos": int(r["LISTA_DEFECTOS_RECH"]),
            }
        )
    selection = {k: v for k, v in filters.items() if v and k != "defect_codes"}
    return mia_bases(
        rejected,
        seen,
        Window(p.id, "PRODUCTION", start, end, end),
        selection=selection,
        defect_codes={str(c) for c in filters["defect_codes"]} if filters["defect_codes"] else None,
    )


def compare(reference: Path) -> dict:
    root = reference / "inputs"
    expected = json.loads((reference / "expected.json").read_text(encoding="utf-8"))
    config = json.loads((root / "customer_score.json").read_text(encoding="utf-8"))
    scalar = _ScoreConfig(config["version"], config)
    productions = CsvCatalogRepository(
        root / "AV-2024-2026_parquet", root / "AV-2024-2026_parquet/manifest.csv"
    ).list_productions()
    by_id = {p.id: p for p in productions}
    cache = {}
    cases = []
    for name, case in expected.items():
        filters = case["request"]["filters"]
        key = json.dumps(filters, sort_keys=True)
        if key not in cache:
            cache[key] = {p.id: _amounts(root, p, filters) for p in productions}
        amounts = cache[key]
        target = case["request"]["production_ids"][0]
        actual = {int(r["codigo_defecto"]): r for r in amounts[target]["rates"]}
        differences = []
        expected_rates = {r["defect_code"]: r for r in case["mia_bases"]}
        if set(actual) != set(expected_rates):
            differences.append(
                {
                    "field": "defect_set",
                    "expected": sorted(expected_rates),
                    "actual": sorted(actual),
                }
            )
        for code, old in expected_rates.items():
            if code not in actual:
                continue
            new = actual[code]
            for field, old_field in [
                ("rechazos", "rejected"),
                ("inspeccionadas", "inspected"),
                ("percent", "percent"),
            ]:
                if new[field] != old[old_field]:
                    differences.append(
                        {
                            "code": code,
                            "field": field,
                            "expected": old[old_field],
                            "actual": new[field],
                        }
                    )
        target_row = case["result"]["productions"][0]
        for detail in target_row["mia_details"]:
            code = detail["defect_code"]
            rate = actual.get(code)
            if rate is None:
                continue
            series: list[float] = []
            for identity, base in sorted(amounts.items()):
                if (
                    config["mia"]["historical_scope"]["exclude_current_production"]
                    and identity == target
                ):
                    continue
                series.extend(
                    r["percent"] for r in base["rates"] if int(r["codigo_defecto"]) == code
                )
            if len(series) < config["mia"]["historical_scope"]["min_samples"]:
                raise IntegrityError("Legacy detail has no matching historical sample population")
            mean = sum(series) / len(series)
            sigma = _sample_sigma(series, mean)
            z = (rate["percent"] - mean) / sigma if sigma > 0 else None
            score = (
                _sigma_score(z, scalar)
                if z is not None
                else config["mia"]["zero_sigma_policy"]["fixed_score"]
                if rate["percent"] > mean
                else 0
            )
            score *= _mia_absolute_rate_factor(rate["percent"], scalar)
            fields = {
                "current_percent": round(rate["percent"], 4),
                "historical_mean_percent": round(mean, 4),
                "historical_sigma_percent": round(sigma, 4),
                "z_score": round(z, 4) if z is not None else None,
                "score": round(score, 4),
                "contribution": round(score * detail["weight"], 4),
                "samples": len(series),
            }
            for field, value in fields.items():
                if value != detail[field]:
                    differences.append(
                        {"code": code, "field": field, "expected": detail[field], "actual": value}
                    )
        cases.append(
            {
                "case": name,
                "status": "MATCH" if not differences else "DIFFERENT",
                "production_id": by_id[target].id,
                "compared_defects": len(actual),
                "inspected": amounts[target]["exposure"]["inspeccionadas"],
                "differences": differences,
            }
        )
    result = {
        "schema_id": "gold-real-parity-v1",
        "mode": "legacy_analytical_v1-validation-only",
        "status": "MATCH" if all(r["status"] == "MATCH" for r in cases) else "DIFFERENT",
        "cases": cases,
        "scope": (
            "Exact MIA amounts and MIA formula outputs; "
            "not a claim that D03/D06 historical differences vanish"
        ),
    }
    atomic_json(reference / "gold-mia-parity.json", result)
    return result
