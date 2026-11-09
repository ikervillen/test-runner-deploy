from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.factory import create_app


@pytest.fixture
def authenticated_client(data_root: Path) -> Iterator[TestClient]:
    settings = Settings(
        data_root=data_root,
        alerts_rejections_root=data_root / "avisos_rechazos_parquet",
        inspected_totals_root=data_root / "inspeccionadas_totales_parquet",
        defect_severity_path=data_root / "Tabla defectos con criticidades.xlsx",
        alert_defect_normalization_path=data_root / "Programacion defectos MDL_enviado V01.xlsx",
        segment_dar_profiles_path=data_root / "segment_dar_profiles.json",
        auth_mode="trusted_proxy",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_trusted_proxy_mode_requires_identity(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/catalog")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_trusted_proxy_mode_requires_viewer_role(authenticated_client: TestClient) -> None:
    response = authenticated_client.get(
        "/api/catalog",
        headers={"X-Forwarded-User": "iker", "X-Forwarded-Roles": "other"},
    )

    assert response.status_code == 403


def test_trusted_proxy_allows_viewer_and_restricts_metrics(
    authenticated_client: TestClient,
) -> None:
    viewer_headers = {
        "X-Forwarded-User": "iker",
        "X-Forwarded-Roles": "quality_viewer",
    }
    assert authenticated_client.get("/api/catalog", headers=viewer_headers).status_code == 200
    assert authenticated_client.get("/api/metrics", headers=viewer_headers).status_code == 403

    metrics_headers = {
        "X-Forwarded-User": "infra",
        "X-Forwarded-Roles": "quality_viewer, quality_admin",
    }
    response = authenticated_client.get("/api/metrics", headers=metrics_headers)
    assert response.status_code == 200
    assert "calidad_http_requests_total" in response.text
