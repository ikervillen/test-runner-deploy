from contextlib import closing
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest

from ingesta_delta_lake import pipeline_intake
from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.pipeline_intake import Intake, discover_lanes
from ingesta_delta_lake.pipeline_policy import PipelinePolicy
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot


@pytest.fixture
def policy():
    return PipelinePolicy(
        timezone="Europe/Madrid",
        window_seconds=600,
        retention_seconds=28800,
        live_retry_period_seconds=3600,
        history_review_retry_seconds=3600,
        latency_seconds=300,
        poll_seconds=5,
        live_batch_files=32,
        live_turn_seconds=5,
        live_workers=2,
        history_workers=1,
        arrow_threads=1,
        history_batch_files=2,
        history_turn_seconds=10,
        stable_wait_seconds=0.01,
        capture_attempts=1,
        max_file_bytes=1024**2,
        archive_publications_per_turn=256,
        read_batch_rows=8192,
        read_max_rows=1000000,
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


def test_fresh_block_rotates_across_five_origins_before_known_payloads(
    config, policy, tmp_path, monkeypatch
):
    origins = ("AV21", "AV22", "AV23", "AV31", "AV32")
    sources = tuple((origin, config.input_root / origin) for origin in origins)
    cfg = replace(config, sources=sources)
    for _, folder in sources:
        folder.mkdir(exist_ok=True)
        for minute in range(5):
            (folder / f"18112026_110{minute}00_SIP_MODELO.csv").write_text("1;OLD;1;\n")
    checkpoint = tmp_path / "live-intake.sqlite"
    lanes, window, _ = discover_lanes(cfg.sources, policy)
    with closing(Intake(checkpoint, lane="live", identity="live")) as intake:
        assert intake.capture_live(cfg, policy, lanes["live"], window=window)["captured"] == 25

    for _, folder in sources:
        for second in ("00", "01"):
            (folder / f"18112026_1110{second}_SIP_MODELO.csv").write_text("2;NEW;1;\n")
    lanes, window, _ = discover_lanes(cfg.sources, policy)
    opened = []
    capture = pipeline_intake.capture

    def observe(*args, **kwargs):
        opened.append((args[1], args[2].name))
        return capture(*args, **kwargs)

    monkeypatch.setattr(pipeline_intake, "capture", observe)
    for _ in origins:
        with closing(Intake(checkpoint, lane="live", identity="live")) as intake:
            result = intake.capture_live(
                cfg,
                replace(policy, live_batch_files=1),
                lanes["live"] + lanes["history"],
                window=window,
            )
            assert result["captured"] == 1 and result["audited"] == 0
    assert [origin for origin, _ in opened] == list(origins)
    assert all("_1110" in name for _, name in opened)


def test_idle_audit_detects_content_change_with_unchanged_size_and_mtime(config, policy, tmp_path):
    import os

    path = config.sources[0][1] / "18112026_111000_SIP_MODELO.csv"
    path.write_text("1;OLD;1;\n")
    lanes, window, _ = discover_lanes(config.sources, policy)
    with closing(Intake(tmp_path / "audit.sqlite", lane="live", identity="live")) as intake:
        intake.capture_live(config, policy, lanes["live"], window=window)
        stamps = path.stat()
        path.write_text("1;NEW;1;\n")
        os.utime(path, ns=(stamps.st_atime_ns, stamps.st_mtime_ns))
        result = intake.capture_live(config, policy, lanes["live"], window=window)
        assert result["captured"] == 1 and result["audited"] == 1
        assert len(list(config.bronze_root.rglob("*.csv"))) == 2


def test_ar_partial_lot_advances_only_certified_deliveries_and_retries_once(policy, tmp_path):
    from ingesta_delta_lake.common import canonical

    clock = "2026-11-27T08:00:00"
    now = datetime.fromisoformat("2026-11-27T07:01:00+00:00").astimezone(UTC)
    with closing(Intake(tmp_path / "ar.sqlite", lane="live", identity="ar")) as intake:
        intake.set("initial_frontier", clock)
        intake.set("lot_policy", canonical(asdict(policy)))
        accepted, waiting = [
            intake.remember(
                "LISTA_AR",
                Snapshot(
                    origin,
                    f"/{origin}.csv",
                    "a" * 64,
                    "/bronze/file.csv",
                    65536,
                    1,
                    now.isoformat(),
                    clock,
                ),
            )
            for origin in ("AV21", "AV22")
        ]
        intake.set("published_capture_sequence", "2")
        result = {"status": "PARTIAL", "complete": False, "confirmed_input_ids": [accepted]}
        intake.lots.record((accepted, waiting), result, None, now, policy)
        intake.acknowledge((accepted, waiting), result, published_at=now.isoformat())
        states = dict(intake.db.execute("SELECT input_id,state FROM inputs"))
        assert states == {accepted: "HANDLED", waiting: "LOT_WAIT"}
        assert intake.db.execute(
            "SELECT receipt_id FROM inputs JOIN receipts ON inputs.result=receipts.receipt_id "
            "WHERE input_id=?",
            (accepted,),
        ).fetchone()
        assert intake.release_ar_lots("dict-v1", 1) == 1
        intake.lots.record((waiting,), result, None, now, policy)
        assert intake.release_ar_lots("dict-v1", 1) == 0
        assert intake.release_ar_lots("dict-v2", 1) == 1
