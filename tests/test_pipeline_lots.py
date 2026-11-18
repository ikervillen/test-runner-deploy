"""Global ownership, durable single retry and priority recovery use real captures/Delta."""

import json
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from deltalake import DeltaTable
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.pipeline_intake import Intake
from ingesta_delta_lake.pipeline_lots import read_manifest
from ingesta_delta_lake.pipeline_runtime import PipelineWorker
from ingesta_delta_lake.workers import run_phase


@pytest.fixture
def clock(monkeypatch):
    # Source 09:20 in Europe/Madrid; technical clocks are UTC.
    class Clock(datetime):
        value = datetime(2026, 11, 18, 8, 25, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz) if tz else cls.value.replace(tzinfo=None)

    monkeypatch.setattr("ingesta_delta_lake.pipeline_runtime.datetime", Clock)
    return Clock


def worker_pair(config, pipelines):
    workers = tuple(PipelineWorker(pipelines, lane, config) for lane in ("live", "history"))
    for worker in workers:
        worker.prepare()
    return workers


def open_intake(worker):
    return closing(Intake(worker.source.intake_path, lane=worker.lane, identity=worker.identity))


def test_frontier_is_global_frozen_and_history_never_claims_live_blocks(
    config, pipeline_config, clock, tmp_path
):
    other = config.input_root / "AV22"
    other.mkdir(parents=True)
    config = replace(config, sources=(*config.sources, ("AV22", other.resolve())))
    (config.sources[0][1] / "18112026_092000_SIP_MODELO.csv").write_text("2;MODEL;1;\n")
    (other / "18112026_091000_SIP_MODELO.csv").write_text("1;OLD;1;\n")
    live, history = worker_pair(config, pipeline_config)
    with open_intake(history) as intake:
        history.capture(intake)
        assert not intake.pending("SIP_MODELO", 10)  # Wait for the authoritative boundary.
    with open_intake(live) as intake:
        live.capture(intake)
        assert intake.setting("initial_frontier") == "2026-11-18T09:20:00"
    (other / "18112026_093000_SIP_MODELO.csv").write_text("3;NEW;1;\n")
    with open_intake(history) as intake:
        history.capture(intake)
        ids = intake.pending("SIP_MODELO", 10)
        assert len(ids) == 1
        stamp = intake.db.execute(
            "SELECT producer_time FROM inputs WHERE input_id=?", ids
        ).fetchone()[0]
        assert stamp == "2026-11-18T09:10:00"
    with open_intake(live) as intake:
        live.capture(intake)
        assert intake.setting("initial_frontier") == "2026-11-18T09:20:00"
        assert len(intake.pending("SIP_MODELO", 10)) == 2


def test_one_retry_at_source_hour_end_survives_restart_then_waits_for_eight_hours(
    config, pipeline_config, clock
):
    source = config.sources[0][1] / "18112026_092000_SIP_MODELO.csv"
    source.write_text("2;MODEL;1;\n")
    live, _history = worker_pair(config, pipeline_config)
    with open_intake(live) as intake:
        window, _ = live.capture(intake)
        cfg = live.selected_config(intake, "SIP_MODELO", window)
        intake.lots.claim(cfg.intake_ids, live.policy, clock.value)
        live.record(intake, cfg, {"status": "FAILED", "error": "injected"}, 0)
        assert not intake.pending("SIP_MODELO", 10)
        clock.value = datetime(2026, 11, 18, 8, 59, 59, tzinfo=UTC)
        live.capture(intake)
        assert not intake.pending("SIP_MODELO", 10)
    live = PipelineWorker(pipeline_config, "live", config)
    with open_intake(live) as intake:
        clock.value += timedelta(seconds=1)
        window, _ = live.capture(intake)
        retried = live.selected_config(intake, "SIP_MODELO", window)
        assert retried.intake_ids == cfg.intake_ids
        live.record(intake, retried, {"status": "FAILED", "error": "again"}, 0)
        assert not intake.pending("SIP_MODELO", 10)
        clock.value += timedelta(hours=1)
        live.capture(intake)
        assert not intake.pending("SIP_MODELO", 10)
        lot = read_manifest(live.storage, "lots")["lots"][0]
        assert lot["retry_used"] == 1 and lot["state"] == "WAITING_HISTORY_REVIEW"
        assert lot["first_attempt"] == "2026-11-18T08:25:00+00:00"
        clock.value = datetime(2026, 11, 18, 16, 25, tzinfo=UTC)
        live.capture(intake)
        lot = read_manifest(live.storage, "lots")["lots"][0]
        assert lot["state"] == "TRANSFERRED_FOR_REVIEW"
        assert lot["first_publication"] is None


def test_hour_retry_recaptures_corrected_bytes_and_publishes_real_silver(
    config, pipeline_config, clock
):
    source = config.sources[0][1] / "18112026_092000_SIP_MODELO.csv"
    source.write_text("2;OLD;1;\n")
    live, _ = worker_pair(config, pipeline_config)
    with open_intake(live) as intake:
        window, _ = live.capture(intake)
        cfg = live.selected_config(intake, "SIP_MODELO", window)
        intake.lots.claim(cfg.intake_ids, live.policy, clock.value)
        live.record(intake, cfg, {"status": "FAILED"}, 0)
        source.write_text("2;CORRECTED;1;\n")
        live.capture(intake)
        assert not intake.pending("SIP_MODELO", 10)
        clock.value = datetime(2026, 11, 18, 9, tzinfo=UTC)
        result = live.cycle(intake, phases=(("SIP_MODELO",),))
        assert result["SIP_MODELO"]["status"] == "APPLIED"
        table = DeltaTable(str(live.source.table_path))
        assert table.to_pyarrow_table()["codigo_modelo_botella"].to_pylist() == ["CORRECTED"]
        lot = read_manifest(live.storage, "lots")["lots"][0]
        assert lot["state"] == "PUBLISHED"
        assert lot["first_publication"] == clock.value.isoformat()


def test_successful_live_lot_is_not_transformed_again_when_frontier_advances(
    config, pipeline_config, clock
):
    root = config.sources[0][1]
    (root / "18112026_092000_SIP_MODELO.csv").write_text("2;MODEL;1;\n")
    live, history = worker_pair(config, pipeline_config)
    with open_intake(live) as intake:
        live.cycle(intake, phases=(("SIP_MODELO",),))
        clock.value += timedelta(minutes=10)
        (root / "18112026_093000_SIP_MODELO.csv").write_text("3;NEW;1;\n")
        live.capture(intake)
    with open_intake(history) as intake:
        history.capture(intake)
        assert not intake.pending("SIP_MODELO", 10)
        assert not history.source.table_path.exists()


def test_history_retries_transferred_lot_before_backfill_and_reads_new_fenix_bytes(
    config, pipeline_config, clock
):
    root = config.sources[0][1]
    source = root / "18112026_092000_SIP_MODELO.csv"
    source.write_text("2;BAD;1;\n")
    (root / "18102026_000000_SIP_MODELO.csv").write_text("1;HISTORY;1;\n")
    live, history = worker_pair(config, pipeline_config)
    with open_intake(live) as intake:
        window, _ = live.capture(intake)
        cfg = live.selected_config(intake, "SIP_MODELO", window)
        intake.lots.claim(cfg.intake_ids, live.policy, clock.value)
        live.record(intake, cfg, {"status": "FAILED"}, 0)
        clock.value += timedelta(hours=8)
        live.capture(intake)
    source.write_text("2;RECOVERED;1;\n")
    with open_intake(history) as intake:
        window, _ = history.capture(intake)
        cfg = history.selected_config(intake, "SIP_MODELO", window)
        assert cfg.scheduled_lot_attempt
        assert (
            intake.db.execute(
                "SELECT producer_time FROM inputs WHERE input_id=?", (cfg.intake_ids[0],)
            ).fetchone()[0]
            == "2026-11-18T09:20:00"
        )
        result, seconds = history.transform(cfg, run_phase)
        history.record(intake, cfg, result, seconds)
        assert result["status"] == "APPLIED"
        assert (
            read_manifest(history.storage, "lot-reviews")["reviews"][0]["state"]
            == "ARCHIVED_CONFIRMED"
        )


def test_tampered_frontier_fails_closed(config, pipeline_config, clock):
    from ingesta_delta_lake.common import IntegrityError

    (config.sources[0][1] / "18112026_092000_SIP_MODELO.csv").write_text("2;MODEL;1;\n")
    live, history = worker_pair(config, pipeline_config)
    with open_intake(live) as intake:
        live.capture(intake)
    pointer = json.loads((live.storage.exchange / "lots/current.json").read_text())
    path = live.storage.exchange / "lots" / (pointer["publication_id"] + ".json")
    path.write_text("{}")
    with open_intake(history) as intake, pytest.raises(IntegrityError, match="changed"):
        history.capture(intake)


def test_success_archives_results_after_publication_retention_without_csv_reprocessing(
    config, pipeline_config, clock
):
    (config.sources[0][1] / "18112026_092000_SIP_MODELO.csv").write_text("2;MODEL;1;\n")
    live, history = worker_pair(config, pipeline_config)
    with open_intake(live) as intake:
        live.cycle(intake, phases=(("SIP_MODELO",),))
    clock.value += timedelta(hours=8)
    with open_intake(history) as intake:
        history.cycle(intake, phases=())
        assert not intake.pending("SIP_MODELO", 10)
    target = history.storage.silver / "archive" / "sip_modelo"
    assert DeltaTable(str(target)).count() == 1
    assert not history.source.table_path.exists()
    with open_intake(live) as intake:
        live.capture(intake)
        assert intake.db.execute("SELECT state FROM lots").fetchone()[0] == "ARCHIVED_CONFIRMED"
        assert read_manifest(live.storage, "lots")["lots"] == []


def test_corrupt_optional_history_does_not_block_new_live_capture(config, pipeline_config, clock):
    (config.sources[0][1] / "18112026_092000_SIP_MODELO.csv").write_text("2;MODEL;1;\n")
    live, history = worker_pair(config, pipeline_config)
    root = history.storage.exchange / "lot-reviews"
    root.mkdir()
    (root / "current.json").write_text('{"publication_id": "bad"}')
    with open_intake(live) as intake:
        result = live.cycle(intake, phases=(("SIP_MODELO",),))
        assert result["SIP_MODELO"]["status"] == "APPLIED"


def test_running_live_unit_is_not_transferred_mid_transaction(config, pipeline_config, clock):
    (config.sources[0][1] / "18112026_092000_SIP_MODELO.csv").write_text("2;MODEL;1;\n")
    live, _ = worker_pair(config, pipeline_config)
    with open_intake(live) as intake:
        window, _ = live.capture(intake)
        cfg = live.selected_config(intake, "SIP_MODELO", window)
        intake.lots.claim(cfg.intake_ids, live.policy, clock.value)
        clock.value += timedelta(hours=8)
        live.capture(intake, active_ids=cfg.intake_ids)
        assert intake.db.execute("SELECT state FROM lots").fetchone()[0] == "PROCESSING"
        assert intake.pending("SIP_MODELO", 10)
