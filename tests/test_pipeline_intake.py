from contextlib import closing
from dataclasses import replace

import pytest

from ingesta_delta_lake import pipeline_intake
from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.pipeline_intake import Intake, discover_lanes
from ingesta_delta_lake.pipeline_policy import PipelinePolicy
from ingesta_delta_lake.service import run


@pytest.fixture
def policy():
    return PipelinePolicy(
        timezone="Europe/Madrid",
        window_seconds=600,
        retention_seconds=28800,
        latency_seconds=300,
        poll_seconds=5,
        history_batch_files=2,
        history_turn_seconds=10,
        stable_wait_seconds=0.01,
        capture_attempts=1,
        max_file_bytes=1024**2,
        archive_batch_rows=10,
    )


def seed(config):
    folder = config.sources[0][1]
    for name, row in (
        ("16112026_080000_SIP_MODELO.csv", "1;3081/360;1;\n"),
        ("17112026_164000_SIP_MODELO.csv", "2;3082/360;1;\n"),
        ("20260101_SIP_MODELO.csv", "3;3083/360;1;\n"),
        ("20261117_SIP_MODELO.csv", "4;3084/360;1;\n"),
    ):
        (folder / name).write_text(row, encoding="utf-8")


def test_live_selects_latest_clock_and_latest_daily_snapshot_before_opening_files(config, policy):
    seed(config)
    lanes, window, errors = discover_lanes(config.sources, policy)
    assert not errors
    assert window.start.isoformat() == "2026-11-17T16:40:00"
    assert {c.path.name for c in lanes["live"]} == {
        "17112026_164000_SIP_MODELO.csv",
        "20261117_SIP_MODELO.csv",
    }
    assert {c.path.name for c in lanes["history"]} == {
        "16112026_080000_SIP_MODELO.csv",
        "20260101_SIP_MODELO.csv",
    }


def test_live_never_opens_history_and_silver_consumes_bronze_when_fenix_is_unavailable(
    config, policy, tmp_path, monkeypatch
):
    seed(config)
    lanes, window, _ = discover_lanes(config.sources, policy)
    with closing(Intake(tmp_path / "intake.sqlite", lane="live", identity="test-live")) as intake:
        original = pipeline_intake.capture
        opened = []

        def bounded(cfg, origin, path, stamp):
            assert path.name in {c.path.name for c in lanes["live"]}
            opened.append(path.name)
            return original(cfg, origin, path, stamp)

        monkeypatch.setattr("ingesta_delta_lake.pipeline_intake.capture", bounded)
        assert intake.capture_turn(config, policy, lanes["live"])["captured"] == 2
        ids = intake.pending("SIP_MODELO", 10, window=window)
        selected = replace(config, intake_path=intake.path, intake_ids=ids)
        monkeypatch.setattr(
            "ingesta_delta_lake.sources.discover",
            lambda *_: pytest.fail("Silver rediscovered Fenix"),
        )
        for path in config.sources[0][1].glob("*.csv"):
            path.unlink()
        result = run(selected)
        assert result["status"] == "APPLIED" and result["new_entries"] == 2
        intake.acknowledge(ids, result, published_at="2026-11-17T17:00:00+00:00")
        assert not intake.pending("SIP_MODELO", 10)
        assert len(opened) == 2


def test_history_has_own_checkpoint_and_bounds_each_discovery_turn(config, policy, tmp_path):
    seed(config)
    lanes, _, _ = discover_lanes(config.sources, policy)
    with closing(Intake(tmp_path / "history.sqlite", lane="history", identity="history")) as intake:
        result = intake.capture_turn(
            config, replace(policy, history_batch_files=1), lanes["history"]
        )
        assert result["captured"] == 1
        first = intake.setting("cursor")
        intake.capture_turn(config, replace(policy, history_batch_files=1), lanes["history"])
        assert intake.setting("cursor") != first
        assert len(intake.pending("SIP_MODELO", 10)) == 2
    with pytest.raises(IntegrityError, match="different lane"):
        Intake(tmp_path / "history.sqlite", lane="live", identity="live")


def test_capture_failure_retains_file_and_reason_without_acknowledging_it(config, policy, tmp_path):
    seed(config)
    lanes, _, _ = discover_lanes(config.sources, policy)
    with closing(Intake(tmp_path / "live.sqlite", lane="live", identity="live")) as intake:
        result = intake.capture_turn(config, replace(policy, max_file_bytes=1), lanes["live"])
        assert result["failed"] == 2 and not intake.pending("SIP_MODELO", 10)
        assert intake.db.execute("SELECT COUNT(*) FROM failures").fetchone()[0] == 2


def test_replay_keeps_handled_state_across_restart(config, policy, tmp_path):
    seed(config)
    lanes, _, _ = discover_lanes(config.sources, policy)
    path = tmp_path / "live.sqlite"
    with closing(Intake(path, lane="live", identity="live")) as intake:
        intake.capture_turn(config, policy, lanes["live"])
        ids = intake.pending("SIP_MODELO", 10)
        intake.acknowledge(
            ids, {"status": "APPLIED", "delta_version": 0}, published_at="2026-11-17T17:00:00+00:00"
        )
    with closing(Intake(path, lane="live", identity="live")) as intake:
        result = intake.capture_turn(config, policy, lanes["live"])
        assert result["captured"] == 0 and result["unchanged"] == 2
        assert not intake.pending("SIP_MODELO", 10)


def test_selected_evidence_must_match_hash(config, policy, tmp_path):
    seed(config)
    lanes, _, _ = discover_lanes(config.sources, policy)
    with closing(Intake(tmp_path / "live.sqlite", lane="live", identity="live")) as intake:
        intake.capture_turn(config, policy, lanes["live"])
        ids = intake.pending("SIP_MODELO", 10)
        next(config.bronze_root.rglob("*.csv")).write_text("tampered")
        with pytest.raises(IntegrityError, match="hash differs"):
            run(replace(config, intake_path=intake.path, intake_ids=ids))
