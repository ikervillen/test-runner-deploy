import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from ingesta_delta_lake.common import ConfigurationError
from ingesta_delta_lake.pipeline_policy import (
    STORES,
    IndependentPipelines,
    LaneStorage,
    PipelinePolicy,
)


@pytest.fixture
def pipelines(tmp_path):
    policy = PipelinePolicy(
        timezone="Europe/Madrid",
        window_seconds=600,
        retention_seconds=28800,
        latency_seconds=300,
        poll_seconds=5,
        live_batch_files=32,
        live_turn_seconds=5,
        live_workers=2,
        history_workers=1,
        arrow_threads=1,
        history_batch_files=32,
        history_turn_seconds=5,
        stable_wait_seconds=0.01,
        capture_attempts=3,
        max_file_bytes=64 * 1024**2,
        archive_batch_rows=1000,
    )
    return IndependentPipelines(
        policy,
        **{
            lane: LaneStorage(**{name: tmp_path / lane / name for name in STORES})
            for lane in ("live", "history")
        },
    )


def test_source_window_uses_last_available_fenix_block_not_wall_clock(pipelines):
    latest = datetime(2026, 10, 12, 9, 47, 28)
    window = pipelines.policy.source_window(latest)
    assert window.start == datetime(2026, 10, 12, 9, 40)
    assert window.end == datetime(2026, 10, 12, 9, 50)
    assert window.contains(latest) and not window.contains(window.end)


def test_archive_age_uses_confirmed_publication_technical_clock(pipelines):
    now = datetime(2026, 11, 17, 15, tzinfo=UTC)
    assert pipelines.policy.archive_before(now) == now - timedelta(hours=8)
    with pytest.raises(ConfigurationError, match="aware"):
        pipelines.policy.archive_before(now.replace(tzinfo=None))


@pytest.mark.parametrize(
    "field,value",
    [
        ("latency_seconds", 0),
        ("window_seconds", True),
        ("retention_seconds", 1),
        ("poll_seconds", 300),
        ("history_batch_files", -1),
        ("timezone", "Invalid/Timezone"),
        ("stable_wait_seconds", float("nan")),
    ],
)
def test_operational_policy_rejects_invalid_values(pipelines, field, value):
    with pytest.raises(ConfigurationError):
        replace(pipelines.policy, **{field: value})


def test_live_and_history_cannot_share_a_store_or_mutable_parent(pipelines):
    for path in (pipelines.live.silver, pipelines.live.silver / "nested"):
        with pytest.raises(ConfigurationError, match="share or contain"):
            replace(pipelines, history=replace(pipelines.history, silver=path))


def test_identity_is_stable_across_activation_and_disjoint_between_lanes(pipelines, monkeypatch):
    before = pipelines.identity("live")
    monkeypatch.setenv("INGESTA_ACTIVATION", "another-release")
    assert pipelines.identity("live") == before
    assert before != pipelines.identity("history")


def test_configuration_roundtrip_rejects_unknown_fields_without_creating_stores(
    pipelines, tmp_path
):
    value = {
        "schema_id": "fenix-independent-pipelines-v1",
        "policy": asdict(pipelines.policy),
        **{
            lane: {k: str(v) for k, v in asdict(pipelines.lane(lane)).items()}
            for lane in ("live", "history")
        },
    }
    path = tmp_path / "pipelines.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert IndependentPipelines.load(path) == pipelines
    assert not pipelines.live.control.exists()
    value["history"]["unknown"] = "ignored-dangerous-path"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="fields"):
        IndependentPipelines.load(path)
