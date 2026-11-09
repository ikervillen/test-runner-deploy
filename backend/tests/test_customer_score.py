from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.domain.errors import ApplicationError
from app.domain.models import BranchFile, MetricId, Production, TrendFilters, TrendRequest, WindowId
from app.domain.score_config import ScoreConfig as _ScoreConfig
from app.domain.score_config import validate_score_config as _validate_score_config
from app.domain.score_math import (
    _aql_band_score,
    _dar_score,
    _dar_threshold_label,
    _mia_absolute_rate_factor,
    _status,
)
from app.infrastructure.scoring import PolarsCustomerScoreRepository
from app.infrastructure.scoring.catalog import SeverityWorkbook
from app.infrastructure.scoring.intervals import merged_interval_summary


def _repository(data_root: Path) -> PolarsCustomerScoreRepository:
    return PolarsCustomerScoreRepository(
        data_root / "customer_score.json",
        data_root / "Tabla defectos con criticidades.xlsx",
        data_root / "avisos_rechazos_parquet",
        data_root / "inspeccionadas_totales_parquet",
        data_root / "Rendimiento_ZC.xlsx",
        {},
    )


def test_customer_score_endpoint_returns_parallel_scores(client: TestClient) -> None:
    response = client.post(
        "/api/customer-score/query",
        json={
            "selection": {
                "production_ids": ["AV23_PROD_001"],
                "branches": ["AV23-1", "AV23-2"],
                "metric": "rejected_count",
                "window": "10m",
                "filters": {
                    "defect_codes": [],
                    "cavities": [],
                    "colors": [],
                    "sections": [],
                    "machine_codes": [],
                },
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["meta"]["config_version"] == 1
    assert payload["meta"]["effective_config"]["zc"]["target_percent"]["default"] == 95.0
    assert len(payload["productions"]) == 1
    row = payload["productions"][0]
    assert row["production_id"] == "AV23_PROD_001"
    assert row["total_penalty"] >= 0
    assert 0 <= row["normalized_score"] <= 10
    assert "main_causes" in row
    assert "components" in row
    critical = next(item for item in row["aql_categories"] if item["category"] == "Critico")
    assert critical["affected_cavity_minutes"] == 15
    assert critical["total_cavity_minutes"] == 61


def test_customer_score_reuses_the_latest_matching_server_query(client: TestClient) -> None:
    payload = {
        "selection": {
            "production_ids": ["AV23_PROD_001"],
            "branches": ["AV23-1", "AV23-2"],
            "metric": "rejected_count",
            "window": "10m",
            "filters": {},
        }
    }

    first = client.post("/api/customer-score/query", json=payload)
    second = client.post("/api/customer-score/query", json=payload)

    assert first.status_code == 200
    assert first.json()["meta"]["cache_hit"] is False
    assert second.status_code == 200
    assert second.json()["meta"]["cache_hit"] is True


def test_customer_score_regression_reference_is_stable(client_with_zc: TestClient) -> None:
    response = client_with_zc.post(
        "/api/customer-score/query",
        json={
            "selection": {
                "production_ids": ["AV23_PROD_001"],
                "branches": ["AV23-1", "AV23-2"],
                "metric": "rejected_count",
                "window": "10m",
                "filters": {
                    "defect_codes": [],
                    "cavities": [],
                    "colors": [],
                    "sections": [],
                    "machine_codes": [],
                },
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["warnings"] == []
    assert payload["meta"]["files_read"] == 7
    assert payload["meta"]["rows_scanned"] == 17

    production = payload["productions"]
    assert [row["production_id"] for row in production] == ["AV23_PROD_001"]
    row = production[0]
    assert row["total_penalty"] == 205.0
    assert row["evaluated_weight"] == 32.0
    assert row["normalized_score"] == 6.4062
    assert row["status"] == "critical"
    assert row["main_causes"] == [
        {
            "component": "Critico",
            "label": "Critico",
            "contribution": 100.0,
            "share_of_total_penalty": 0.4878,
        }
    ]

    components = {component["component"]: component for component in row["components"]}
    assert {component: values["contribution"] for component, values in components.items()} == {
        "Critico": 100.0,
        "Grave": 0.0,
        "Mayor": 0.0,
        "Menor": 20.0,
        "Leve": 0.0,
        "DAR": 80.0,
        "ZC": 5.0,
    }

    categories = {item["category"]: item for item in row["aql_categories"]}
    assert categories["Critico"] == {
        "category": "Critico",
        "affected_cavity_minutes": 15.0,
        "total_cavity_minutes": 61.0,
        "exposure_percent": 24.5902,
        "aql_percent": 0.05,
        "ratio": 491.8033,
        "score": 10.0,
        "weight": 10.0,
        "contribution": 100.0,
        "top_defect_code": 4,
        "top_defect_label": "Rechupe interior",
    }
    assert categories["Menor"]["contribution"] == 20.0
    assert categories["Menor"]["top_defect_code"] == 5

    assert row["dar"] == {
        "event_count": 2,
        "affected_minutes": 15.0,
        "total_cavity_minutes": 61.0,
        "exposure_percent": 24.5902,
        "aql_percent": 0.0,
        "score": 10.0,
        "weight": 8.0,
        "contribution": 80.0,
        "details": (
            "2 eventos DAR deduplicados; 24.5902 % min-cavidad; "
            "Corcho: Rechupe int. 45 mm: codigos normalizados [4]; "
            "Ovalizacion: codigos normalizados [3]."
        ),
        "breakdown": [
            {
                "group": "Corcho: Rechupe int. 45 mm",
                "codes": [4],
                "event_count": 1,
                "affected_minutes": 10.0,
                "share_percent": 66.6667,
                "exposure_percent": 16.3934,
                "score": 10.0,
                "threshold_label": "exposicion >= 0.40 %",
            },
            {
                "group": "Ovalizacion",
                "codes": [3],
                "event_count": 1,
                "affected_minutes": 5.0,
                "share_percent": 33.3333,
                "exposure_percent": 8.1967,
                "score": 10.0,
                "threshold_label": "exposicion >= 0.40 %",
            },
        ],
    }
    assert row["zc"] == {
        "target_percent": 95.0,
        "actual_percent": 90.0,
        "loss_points": 5.0,
        "score": 2.5,
        "weight": 2.0,
        "contribution": 5.0,
        "windows": 1,
    }

    shifts = row["shift_scores"]
    assert len(shifts) == 1
    assert shifts[0]["shift"] == "2024-02-13 Manana"
    assert shifts[0]["total_penalty"] == 205.0
    assert shifts[0]["normalized_score"] == 6.4062
    assert shifts[0]["status"] == "critical"


def test_customer_score_endpoint_accepts_score_config_override(client: TestClient) -> None:
    response = client.post(
        "/api/customer-score/query",
        json={
            "selection": {
                "production_ids": ["AV23_PROD_001"],
                "branches": ["AV23-1", "AV23-2"],
                "metric": "rejected_count",
                "window": "10m",
                "filters": {
                    "defect_codes": [],
                    "cavities": [],
                    "colors": [],
                    "sections": [],
                    "machine_codes": [],
                },
            },
            "score_config_override": {
                "aql": {"category_limits_percent": {"Critico": 0.2}},
                "weights": {"categories": {"Critico": 7}, "zc": 0},
                "zc": {"target_percent": {"default": 93}},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    config = payload["meta"]["effective_config"]
    assert config["aql"]["category_limits_percent"]["Critico"] == 0.2
    assert config["weights"]["categories"]["Critico"] == 7.0
    assert config["weights"]["zc"] == 0.0
    assert config["zc"]["target_percent"]["default"] == 93.0
    critical = next(
        item
        for item in payload["productions"][0]["aql_categories"]
        if item["category"] == "Critico"
    )
    assert critical["aql_percent"] == 0.2
    assert critical["weight"] == 7.0


def test_customer_score_reports_when_no_component_is_evaluable(client: TestClient) -> None:
    response = client.post(
        "/api/customer-score/query",
        json={
            "selection": {
                "production_ids": ["AV23_PROD_001"],
                "branches": ["AV23-1", "AV23-2"],
                "metric": "rejected_count",
                "window": "10m",
                "filters": {
                    "defect_codes": [],
                    "cavities": [],
                    "colors": [],
                    "sections": [],
                    "machine_codes": [],
                },
            },
            "score_config_override": {
                "weights": {
                    "mia_defect": 0,
                    "categories": {
                        "Critico": 0,
                        "Grave": 0,
                        "Mayor": 0,
                        "Menor": 0,
                        "Leve": 0,
                    },
                    "dar": 0,
                    "zc": 0,
                }
            },
        },
    )

    assert response.status_code == 200
    row = response.json()["productions"][0]
    assert row["components"] == []
    assert row["total_penalty"] == 0.0
    assert row["evaluated_weight"] == 0.0
    assert row["normalized_score"] == 0.0
    assert row["warnings"] == ["Sin componentes evaluables para esta produccion."]


def test_score_configuration_rejects_incoherent_dar_thresholds() -> None:
    config_path = Path(__file__).parents[2] / "config" / "customer_score.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["dar"]["progressive_thresholds_percent"] = {"score_5": 0.4, "score_10": 0.1}

    with pytest.raises(ApplicationError, match="score_5"):
        _validate_score_config(raw)


def test_dar_codes_switch_from_legacy_to_new_codes(data_root: Path) -> None:
    workbook = SeverityWorkbook(data_root / "Tabla defectos con criticidades.xlsx")

    legacy_codes = workbook.dar_code_groups(use_new_codes=False)
    new_codes = workbook.dar_code_groups(use_new_codes=True)

    assert legacy_codes["Verticalidad"] == (212, 230)
    assert new_codes["Verticalidad"] == (113, 414)
    assert legacy_codes["Corcho: Rechupe int. 45 mm"] == (302,)
    assert new_codes["Corcho: Rechupe int. 45 mm"] == (115,)
    assert legacy_codes["Rosca: Line over"] == (110,)
    assert new_codes["Rosca: Line over"] == (27,)


def test_shift_alert_normalization_accepts_late_text_values(data_root: Path) -> None:
    repository = _repository(data_root)
    production = Production(
        id="AV23_PROD_001",
        line="AV23",
        start_day=datetime(2024, 2, 13).date(),
        end_day=datetime(2024, 2, 15).date(),
        branches=(),
    )
    events = pl.DataFrame(
        {
            "defect_code": [None] * 100 + [212],
            "defect_description": [None] * 100 + ["Verticalidad"],
        }
    )

    normalized = repository.defects.annotate(production, events)

    assert normalized.schema["_normalized_description"] == pl.String
    assert normalized.row(-1, named=True)["_normalized_description"] == "Verticalidad"


def test_mia_rates_use_real_inspected_totals(data_root: Path) -> None:
    repository = _repository(data_root)
    production_dir = data_root / "AV23_PROD_001_20240213__20240215"
    production = Production(
        id="AV23_PROD_001",
        line="AV23",
        start_day=datetime(2024, 2, 13).date(),
        end_day=datetime(2024, 2, 15).date(),
        branches=(
            BranchFile(
                branch="AV23-1",
                path=production_dir / "AV23-1.parquet",
                rows=4,
                data_start=datetime(2024, 2, 13, 7, 1),
                data_end=datetime(2024, 2, 13, 8, 2),
            ),
            BranchFile(
                branch="AV23-2",
                path=production_dir / "AV23-2.parquet",
                rows=1,
                data_start=datetime(2024, 2, 13, 7, 2),
                data_end=datetime(2024, 2, 13, 7, 2),
            ),
        ),
    )
    request = TrendRequest(
        production_ids=("AV23_PROD_001",),
        branches=("AV23-1", "AV23-2"),
        metric=MetricId.REJECTED_COUNT,
        window=WindowId.TEN_MINUTES,
        filters=TrendFilters(),
    )

    scan = repository.mia.production_rates(request, (production,), respect_branch_selection=True)

    by_code = {rate.defect_code: rate for rate in scan.rates}
    assert by_code[3].rejected == 18
    assert by_code[3].inspected == 1000
    assert by_code[3].percent == pytest.approx(1.8)
    assert scan.files_read == 3
    assert scan.rows_scanned == 7
    assert scan.warnings == ()


def test_dar_overlapping_old_new_events_do_not_duplicate_cavity_minutes() -> None:
    start = datetime(2026, 1, 10, 8, 0)
    count, minutes = merged_interval_summary(
        {
            ("Ovalizacion", "1D"): [
                (start, start + timedelta(minutes=10)),
                (start + timedelta(minutes=2), start + timedelta(minutes=12)),
            ],
            ("Ovalizacion", "2D"): [
                (start, start + timedelta(minutes=5)),
            ],
        }
    )

    assert count == 2
    assert minutes == 17


def test_mia_absolute_rate_factor_is_progressive_and_capped() -> None:
    config = _ScoreConfig(
        version=1,
        raw={
            "mia": {
                "absolute_rate_factor": {
                    "enabled": True,
                    "min_relevant_percent": 0.4,
                    "full_weight_span_percent": 2.0,
                }
            }
        },
    )

    assert _mia_absolute_rate_factor(0.39, config) == 0.0
    assert _mia_absolute_rate_factor(1.4, config) == pytest.approx(0.5)
    assert _mia_absolute_rate_factor(2.4, config) == 1.0
    assert _mia_absolute_rate_factor(3.0, config) == 1.0


def test_aql_score_is_progressive_between_one_and_two_aql() -> None:
    assert _aql_band_score(0.0) == 0.0
    assert _aql_band_score(0.5) == pytest.approx(2.5)
    assert _aql_band_score(1.0) == 5.0
    assert _aql_band_score(1.5) == pytest.approx(7.5)
    assert _aql_band_score(2.0) == 10.0
    assert _aql_band_score(3.0) == 10.0


def test_dar_score_is_progressive_between_configured_thresholds() -> None:
    config = {
        "progressive_thresholds_percent": {
            "enabled": True,
            "score_5": 0.1,
            "score_10": 0.4,
        }
    }

    assert _dar_score(0.0, config) == 0.0
    assert _dar_score(0.05, config) == pytest.approx(2.5)
    assert _dar_score(0.1, config) == pytest.approx(5.0)
    assert _dar_score(0.25, config) == pytest.approx(7.5)
    assert _dar_score(0.4, config) == 10.0
    assert _dar_score(0.8, config) == 10.0
    assert _dar_threshold_label(0.05, config) == "0 < exposicion < 0.10 %"
    assert _dar_threshold_label(0.25, config) == "0.10 % <= exposicion < 0.40 %"
    assert _dar_threshold_label(0.4, config) == "exposicion >= 0.40 %"


def test_status_uses_total_penalty_thresholds() -> None:
    config = _ScoreConfig(
        version=1,
        raw={
            "score": {
                "interpretation": {
                    "basis": "total_penalty",
                    "good_max": 40,
                    "observed_max": 80,
                    "bad_max": 120,
                }
            }
        },
    )

    assert _status(40, config) == "good"
    assert _status(80, config) == "observed"
    assert _status(120, config) == "bad"
    assert _status(121, config) == "critical"
