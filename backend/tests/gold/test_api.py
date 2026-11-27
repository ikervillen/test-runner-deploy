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


def test_live_bulk_reads_weighted_contributions_from_the_pinned_delta(gold_api, monkeypatch):
    from datetime import UTC, timedelta
    from math import isclose
    from types import SimpleNamespace

    from app.gold.live_view import _project
    from app.gold.publications import Snapshot

    _, root = gold_api
    snapshot = Snapshot.open(root, require_gold_contract=True)
    horizon = snapshot.rows("quality_scores")
    score = next(r for r in horizon if r["tipo_ventana"] == "HOUR" and r["fin"] <= CUTOFF)
    target = max(CUTOFF, score["fin"])
    calls = []
    original = snapshot.rows

    def record(name, **kwargs):
        calls.append((name, kwargs))
        return original(name, **kwargs)

    monkeypatch.delenv("FENIX_LIVE_T0", raising=False)
    now = (target + timedelta(minutes=5)).replace(tzinfo=UTC)
    result = _project(
        SimpleNamespace(
            snapshot=SimpleNamespace(
                rows=record, manifest=snapshot.manifest, publication_id=snapshot.publication_id
            ),
            max_rows=10000,
            record_scan=lambda *_: None,
        ),
        target,
        now,
    )
    results = [r for hour in result.hours[:-1] for r in hour.results if r.result_id]
    assert results and result.hours[-1].results == []
    queries = [kwargs for name, kwargs in calls if name == "quality_score_components"]
    assert len(queries) == 1
    ids = sorted(r.result_id for r in results)
    assert queries[0]["file_pruning_predicate"] == [("id_resultado", "in", ids)]
    components = original("quality_score_components")
    for row in results:
        source = [c for c in components if c["id_resultado"] == row.result_id]
        assert row.contributions == {c["componente"]: c["contribucion"] for c in source}
        assert any(c["contribucion"] != c["score"] for c in source)
        assert isclose(sum(row.contributions.values()), row.total_penalty, abs_tol=0.0001)


def test_withdrawal_updates_current_delta_and_hides_pending_score_on_old_pin(
    gold_api, calculation, monkeypatch
):
    from collections import Counter
    from datetime import UTC, timedelta
    from types import SimpleNamespace

    from app.domain.errors import ApplicationError
    from app.gold.common import canonical
    from app.gold.live_archive import archive, archived_result
    from app.gold.live_hours import AR_COVERAGE_PENDING
    from app.gold.pipeline import SCORE_TABLES
    from app.gold.publications import Publisher, Snapshot
    from app.infrastructure.cache import LruCache

    _, root = gold_api
    source, _ = calculation
    original = Snapshot.open(root, require_gold_contract=True)
    scores = original.rows("quality_scores")
    target = max((r for r in scores if r["tipo_ventana"] == "HOUR"), key=lambda r: r["fin"])
    identity = target["id_resultado"]
    ready = next(r for r in original.rows("publication_scope_status") if r["scope_id"] == identity)
    pending = {**ready, "estado": "WAITING_DEPENDENCY", "reason_code": AR_COVERAGE_PENDING}

    class Withdrawal(dict):
        spilled = frozenset()
        withdrawn_scores = (identity,)

    rows = Withdrawal(
        {**{table: [] for table in SCORE_TABLES}, "publication_scope_status": [pending]}
    )
    publisher = Publisher(root, environment="test", plant="AV")
    options = dict(config_id="withdrawal", cutoff=CUTOFF.isoformat(), coverage=[], fact_bounds=())
    publisher.publish(source, rows, **options)
    current = Snapshot.open(root, require_gold_contract=True)
    for table in SCORE_TABLES:
        assert Counter(map(canonical, current.rows(table))) == Counter(
            canonical(r) for r in original.rows(table) if r["id_resultado"] != identity
        )
    assert pending in current.rows("publication_scope_status")
    assert publisher.publish(source, rows, **options)["status"] == "UNCHANGED"

    t0 = target["inicio"] - timedelta(hours=1)
    monkeypatch.setattr("app.gold.live_archive.live_epoch", lambda: t0)
    monkeypatch.setattr("app.gold.live_view.live_epoch", lambda: t0)
    settings = SimpleNamespace(
        resolved_gold_root=root,
        gold_query_max_rows=100000,
        gold_query_total_rows=300000,
        gold_live_timezone="Europe/Madrid",
    )
    moment = (target["fin"] + timedelta(hours=7)).replace(tzinfo=UTC)
    with pytest.raises(ApplicationError, match="pendiente de cobertura AR"):
        archived_result(settings, original.publication_id, identity, moment=moment)
    old_page = archive(settings, LruCache(4), moment=moment, publication_id=original.publication_id)
    results = [
        result
        for shift in old_page["shifts"]
        for hour in shift["hours"]
        for result in hour["results"]
    ]
    assert all(row["result_id"] != identity for row in results)
    assert any(
        row["reason"] == AR_COVERAGE_PENDING and row["total_penalty"] is None for row in results
    )


def test_archive_uses_existing_delta_pins_preserves_t0_and_applies_exact_age(
    gold_api, monkeypatch, calculation
):
    from copy import deepcopy
    from datetime import UTC, timedelta
    from types import SimpleNamespace

    from app.gold.live_archive import archive, archived_result
    from app.gold.publications import Snapshot
    from app.infrastructure.cache import LruCache

    _, root = gold_api
    settings = SimpleNamespace(
        resolved_gold_root=root,
        gold_query_max_rows=100000,
        gold_query_total_rows=300000,
        gold_live_timezone="Europe/Madrid",
    )
    snapshot = Snapshot.open(root, require_gold_contract=True)
    scores = snapshot.rows("quality_scores")
    score = max((r for r in scores if r["tipo_ventana"] == "HOUR"), key=lambda r: r["fin"])
    cutoff = score["fin"]
    t0 = cutoff.replace(hour=10, minute=20)
    monkeypatch.setattr("app.gold.live_archive.live_epoch", lambda: t0)
    monkeypatch.setattr("app.gold.live_view.live_epoch", lambda: t0)
    moment = (cutoff + timedelta(hours=7)).replace(tzinfo=UTC)  # Industrial UTC+1 in November.
    cache = LruCache(4)
    before = archive(settings, cache, moment=moment - timedelta(seconds=1))
    visible = archive(settings, cache, moment=moment, publication_id=snapshot.publication_id)
    assert before["eligible_until"] == cutoff - timedelta(hours=1)
    assert visible["eligible_until"] == cutoff
    assert visible["publication"] == snapshot.publication_id
    assert visible["shifts"][0]["partial_t0"]
    assert all(hour["end"] <= cutoff for shift in visible["shifts"] for hour in shift["hours"])
    assert visible["shifts"][0]["hours"][0]["state"] == "before_t0"
    rows = [
        r
        for shift in visible["shifts"]
        for h in shift["hours"]
        for r in h["results"]
        if r["result_id"]
    ]
    row = next(r for r in rows if r["result_id"] == score["id_resultado"])
    assert row["total_penalty"] == score["total_penalty"]
    result = archived_result(settings, visible["publication"], row["result_id"], moment=moment)
    assert result["score"] == score
    assert result["configuration"]["score"] and result["configuration"]["catalogs"]
    assert result["details"]["quality_score_components"]
    assert result["coverage"]
    assert not result["missing"]
    assert archive(settings, cache, moment=moment) == visible

    source, original_config = calculation
    revised_config = deepcopy(original_config)
    revised_config["weights"]["dar"] += 1
    run(
        source.root,
        root,
        bundle=bundle(),
        config=revised_config,
        cutoff=CUTOFF,
        environment="test",
        plant="AV",
        recalculate_closed_ids={score["id_produccion"]},
        recalculation_reason="Synthetic revision for testing the archived publication pins",
    )
    revised = archive(settings, cache, moment=moment)
    assert revised["publication"] != visible["publication"]
    assert revised["previous_publication"] == visible["publication"]
    new_detail = archived_result(settings, revised["publication"], row["result_id"], moment=moment)
    assert new_detail["score"]["config_id"] != score["config_id"]
    assert (
        archived_result(settings, visible["publication"], row["result_id"], moment=moment) == result
    )
