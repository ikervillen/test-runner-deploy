import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.factory import create_app
from app.gold.pipeline import run

from .test_pipeline import CUTOFF, bundle
from .test_pipeline import calculation as calculation


@pytest.fixture
def gold_api(calculation, tmp_path):
    snap, config = calculation
    root = tmp_path / "gold"
    run(
        snap.root,
        root,
        bundle=bundle(),
        config=config,
        cutoff=CUTOFF,
        environment="test",
        plant="AV",
    )
    settings = Settings(
        data_root=tmp_path / "no-legacy-files",
        data_backend="gold",
        gold_root=root,
        customer_score_config_path=tmp_path / "nonexistent.json",
    )
    return TestClient(create_app(settings)), root


def payload():
    return {
        "selection": {
            "production_ids": ["production-3"],
            "branches": ["AV23-1"],
            "metric": "rejected_count",
            "window": "1h",
            "filters": {},
        }
    }


def test_catalog_and_complete_score_use_only_pinned_gold(gold_api):
    client, _ = gold_api
    assert client.get("/api/ready").status_code == 200
    catalog = client.get("/api/catalog").json()
    assert catalog["source"] == "gold"
    assert len(catalog["productions"]) == 4
    response = client.post("/api/customer-score/query", json=payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["meta"]["publication_id"] == catalog["publication_id"]
    assert body["productions"][0]["shift_scores"]
    assert len(body["productions"][0]["components"]) == 8
    assert client.post("/api/customer-score/query", json=payload()).json()["meta"]["cache_hit"]
    unknown = client.post(
        "/api/customer-quality/query", json={"selection": payload()["selection"]}
    ).json()
    assert unknown["rows"][0]["label"] == "unknown"
    status = client.get("/api/gold/status")
    assert status.status_code == 200
    coverage = status.json()["coverage"]
    assert [c["id_produccion"] for c in coverage] == [f"production-{n}" for n in range(4)]
    assert {c["estado"] for c in coverage} == {"READY"}
    assert coverage[3]["calculated_until"] == "2026-11-01T13:00:00"
    assert coverage[3]["missing"] == []


def test_hourly_endpoint_keeps_history_unavailable_without_affecting_live(gold_api):
    client, _ = gold_api
    unavailable = client.get("/api/history/catalog")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "HISTORY_UNAVAILABLE"
    live = client.get("/api/live/scores")
    assert live.status_code == 200, live.text
    assert len(live.json()["hours"]) == 8
    assert live.json()["hours"][-1]["state"] == "open"
    assert live.json()["hours"][-1]["results"] == []


def test_custom_configuration_and_machine_filter_recalculate_without_writes(gold_api):
    client, root = gold_api
    before = (root / "current.json").read_bytes()
    custom = {**payload(), "score_config_override": {"weights": {"dar": 12}}}
    response = client.post("/api/customer-score/query", json=custom)
    assert response.status_code == 200, response.text
    row = response.json()["productions"][0]
    assert row["dar"]["weight"] == 12
    custom["selection"]["filters"] = {"machine_codes": [12]}
    response = client.post("/api/customer-score/query", json=custom)
    assert response.status_code == 200, response.text
    assert response.json()["productions"]
    assert (root / "current.json").read_bytes() == before


def test_selection_validation_and_unavailable_publication(gold_api):
    client, root = gold_api
    invalid = payload()
    invalid["selection"]["branches"] = ["OTHER-1"]
    assert client.post("/api/customer-score/query", json=invalid).status_code == 400
    (root / "current.json").rename(root / "saved-pointer.json")
    assert client.get("/api/ready").status_code == 503
    assert client.post("/api/customer-score/query", json=payload()).status_code == 503


def test_pending_sources_and_provisional_dar_are_explicit(calculation, tmp_path):
    snap, config = calculation
    provisional = bundle()
    provisional["model_segments"][0]["estado_dar"] = "PROVISIONAL"
    root = tmp_path / "provisional"
    run(
        snap.root,
        root,
        bundle=provisional,
        config=config,
        cutoff=CUTOFF,
        environment="test",
        plant="AV",
    )
    client = TestClient(create_app(Settings(data_root=root, data_backend="gold")))
    response = client.post("/api/customer-score/query", json=payload()).json()
    assert "DAR provisional" in response["productions"][0]["warnings"][0]
    missing = bundle()
    missing["model_segments"][0].update(estado_dar="PENDING", profile_id=None)
    run(
        snap.root,
        root,
        bundle=missing,
        config=config,
        cutoff=CUTOFF,
        environment="test",
        plant="AV",
        recalculate_closed_ids={"production-3"},
        recalculation_reason="DAR awaiting approval",
    )
    pending = client.post("/api/customer-score/query", json=payload()).json()
    assert pending["status"] == "pending"
    assert pending["productions"] == []
    assert pending["pending_productions"][0]["production_id"] == "production-3"
    assert pending["meta"]["publication_id"] != response["meta"]["publication_id"]


def test_parameters_budgets_and_positive_weight_are_validated(gold_api):
    client, _ = gold_api
    invalid = {**payload(), "score_config_override": {"weights": {"dar": -1}}}
    assert client.post("/api/customer-score/query", json=invalid).status_code == 400
    invalid["score_config_override"] = {
        "weights": {
            "mia_defect": 0,
            "dar": 0,
            "zc": 0,
            "categories": {name: 0 for name in ("Critico", "Grave", "Mayor", "Menor", "Leve")},
        }
    }
    response = client.post("/api/customer-score/query", json=invalid)
    assert response.status_code == 200
    assert response.json()["productions"] == []
    assert "NO_EVALUABLE_WEIGHT" in response.json()["pending_productions"][0]["reasons"]
    client.app.state.settings.gold_query_max_rows = 1
    assert client.post("/api/customer-score/query", json=payload()).status_code == 413


def test_gold_roles_protect_scenarios_and_metrics(gold_api):
    client, _ = gold_api
    client.app.state.settings.auth_mode = "trusted_proxy"
    assert client.get("/api/ready").status_code == 200
    assert client.post("/api/customer-score/query", json=payload()).status_code == 401
    viewer = {"X-Forwarded-User": "quality-user", "X-Forwarded-Roles": "quality_viewer"}
    assert (
        client.post("/api/customer-score/query", json=payload(), headers=viewer).status_code == 200
    )
    scenario = {**payload(), "score_config_override": {"weights": {"dar": 12}}}
    assert (
        client.post("/api/customer-score/query", json=scenario, headers=viewer).status_code == 403
    )
    manager = {**viewer, "X-Forwarded-Roles": "quality_viewer,quality_manager"}
    assert (
        client.post("/api/customer-score/query", json=scenario, headers=manager).status_code == 200
    )
    assert client.get("/api/metrics", headers=viewer).status_code == 403
    admin = {**viewer, "X-Forwarded-Roles": "quality_viewer,quality_admin"}
    metrics = client.get("/api/metrics", headers=admin)
    assert metrics.status_code == 200
    assert "calidad_gold_last_success_timestamp_seconds" in metrics.text


def test_closed_details_keep_their_original_catalog_in_baseline_and_scenario(gold_api, calculation):
    client, root = gold_api
    snap, config = calculation
    original = client.post("/api/customer-score/query", json=payload()).json()["productions"][0]
    revised = bundle()
    revised["profiles"][0]["groups"][0].update(id="new-group", label="New definition")
    run(
        snap.root,
        root,
        bundle=revised,
        config=config,
        cutoff=CUTOFF,
        environment="test",
        plant="AV",
    )
    response = client.post("/api/customer-score/query", json=payload())
    assert response.status_code == 200, response.text
    row = response.json()["productions"][0]
    assert row["dar"]["breakdown"] == original["dar"]["breakdown"]
    scenario = {**payload(), "score_config_override": {"weights": {"dar": 12}}}
    response = client.post("/api/customer-score/query", json=scenario)
    assert response.status_code == 200, response.text
    assert response.json()["productions"][0]["dar"]["breakdown"][0]["codes"] == [53]
