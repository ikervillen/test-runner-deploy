from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.factory import create_app
from app.infrastructure.aggregates.alerts import AlertAggregateResolver, build_alert_aggregates
from app.infrastructure.aggregates.mia import MiaAggregateResolver
from app.infrastructure.aggregates.zc import ZcAggregateResolver
from app.infrastructure.repositories.catalog import CsvCatalogRepository


def test_alert_aggregate_builds_a_production_bundle_and_detects_staleness(
    data_root: Path,
    tmp_path: Path,
    client_with_zc: TestClient,
) -> None:
    assert client_with_zc is not None
    alerts_root = data_root / "avisos_rechazos_parquet"
    output_root = tmp_path / "quality_aggregates"

    release = build_alert_aggregates(
        data_root=data_root,
        alerts_root=alerts_root,
        inspected_root=data_root / "inspeccionadas_totales_parquet",
        hot_end_path=data_root / "Rendimiento_ZC.xlsx",
        output_root=output_root,
        release_id="fixture-release",
    )

    assert release.release_id == "fixture-release"
    assert release.bundles[0].rows == 4
    production = CsvCatalogRepository(data_root, data_root / "manifest.csv").list_productions()[0]
    resolver = AlertAggregateResolver(output_root, alerts_root)
    paths = resolver.paths_for(production)
    assert paths is not None
    assert paths[0].is_file()
    mia_resolver = MiaAggregateResolver(
        output_root,
        data_root,
        data_root / "inspeccionadas_totales_parquet",
    )
    production_rates = mia_resolver.rates_for(production)
    shift_rates = mia_resolver.rates_for(production, "2024-02-13_Manana")
    assert production_rates is not None
    assert shift_rates is not None
    assert production_rates.height > 0
    assert shift_rates.height > 0
    zc_resolver = ZcAggregateResolver(output_root, data_root / "Rendimiento_ZC.xlsx")
    zc_observations = zc_resolver.observations()
    assert zc_observations is not None
    assert zc_observations.height == 1

    source = alerts_root / "AV23" / "2024-02-13.parquet"
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    assert resolver.paths_for(production) is None


def test_aggregate_query_matches_the_source_score(
    client: TestClient,
    data_root: Path,
    tmp_path: Path,
) -> None:
    aggregate_root = tmp_path / "quality_aggregates"
    build_alert_aggregates(
        data_root=data_root,
        alerts_root=data_root / "avisos_rechazos_parquet",
        inspected_root=data_root / "inspeccionadas_totales_parquet",
        hot_end_path=data_root / "Rendimiento_ZC.xlsx",
        output_root=aggregate_root,
        release_id="parity-release",
    )
    payload = {
        "selection": {
            "production_ids": ["AV23_PROD_001"],
            "branches": ["AV23-1", "AV23-2"],
            "metric": "rejected_count",
            "window": "10m",
            "filters": {},
        }
    }
    baseline = client.post("/api/customer-score/query", json=payload)
    settings = Settings(
        data_root=data_root,
        alerts_rejections_root=data_root / "avisos_rechazos_parquet",
        inspected_totals_root=data_root / "inspeccionadas_totales_parquet",
        hot_end_yield_path=data_root / "Rendimiento_ZC.xlsx",
        defect_severity_path=data_root / "Tabla defectos con criticidades.xlsx",
        alert_defect_normalization_path=data_root / "Programacion defectos MDL_enviado V01.xlsx",
        segment_dar_profiles_path=data_root / "segment_dar_profiles.json",
        quality_aggregate_root=aggregate_root,
    )
    with TestClient(create_app(settings)) as aggregate_client:
        aggregated = aggregate_client.post("/api/customer-score/query", json=payload)

    assert baseline.status_code == 200
    assert aggregated.status_code == 200
    baseline_row = baseline.json()["productions"][0]
    aggregate_row = aggregated.json()["productions"][0]
    assert aggregate_row["total_penalty"] == baseline_row["total_penalty"]
    assert aggregate_row["shift_scores"] == baseline_row["shift_scores"]
