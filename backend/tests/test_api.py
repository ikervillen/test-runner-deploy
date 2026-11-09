from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_and_request_id(client: TestClient) -> None:
    response = client.get("/api/health", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request"
    assert response.json() == {"status": "ok", "data_root_available": True}


def test_readiness_reports_data_version_and_missing_resources(client: TestClient) -> None:
    response = client.get("/api/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["data_version"]
    assert "archivo rendimiento ZC" in body["missing_resources"]


def test_catalog_is_built_from_manifest(client: TestClient) -> None:
    response = client.get("/api/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["lines"] == ["AV23"]
    assert body["productions"][0]["id"] == "AV23_PROD_001"
    assert [branch["id"] for branch in body["productions"][0]["branches"]] == [
        "AV23-1",
        "AV23-2",
    ]
    assert {item["id"] for item in body["windows"]} == {"10m", "1h", "4h", "8h"}
    assert body["filters"]["defect_codes"] == [3, 4]


def test_validation_error_has_unified_format(client: TestClient) -> None:
    response = client.post("/api/customer-score/query", json={})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "REQUEST_VALIDATION_ERROR"
    assert error["request_id"]


def test_invalid_branch_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/customer-score/query",
        json={
            "selection": {
                "production_ids": ["AV23_PROD_001"],
                "branches": ["AV32-4"],
                "metric": "rejected_count",
                "window": "10m",
                "filters": {},
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_BRANCH_SELECTION"


def test_customer_quality_returns_auditable_label(client: TestClient) -> None:
    response = client.post(
        "/api/customer-quality/query",
        json={
            "selection": {
                "production_ids": ["AV23_PROD_001"],
                "branches": ["AV23-1", "AV23-2"],
                "metric": "rejected_count",
                "window": "10m",
                "filters": {},
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rows"][0]["production_id"] == "AV23_PROD_001"
    assert body["rows"][0]["label"] == "good"
    assert body["validation_rows"][0]["validation_case"] == "sin_reclamacion_relevante"


def test_public_api_exposes_only_quality_operations(client: TestClient) -> None:
    paths = client.get("/api/openapi.json").json()["paths"]

    assert set(paths) == {
        "/api/health",
        "/api/ready",
        "/api/catalog",
        "/api/customer-quality/query",
        "/api/customer-score/query",
    }
