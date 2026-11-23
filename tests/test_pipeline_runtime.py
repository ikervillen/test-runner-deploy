import json
import threading
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import portalocker
import pytest
from deltalake import DeltaTable

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.pipeline_intake import Intake
from ingesta_delta_lake.pipeline_policy import STORES, IndependentPipelines
from ingesta_delta_lake.pipeline_runtime import PipelineWorker


@pytest.fixture
def pipeline_config(tmp_path):
    value = json.loads(Path("deployment/pipelines.example.json").read_text())
    value["policy"]["stable_wait_seconds"] = 0.01
    for lane in ("live", "history"):
        value[lane] = {name: str(tmp_path / lane / name) for name in STORES}
    path = tmp_path / "pipelines.json"
    path.write_text(json.dumps(value))
    return IndependentPipelines.load(path)


def test_historical_delta_and_checkpoint_locks_do_not_block_live(config, pipeline_config):
    source = config.sources[0][1]
    (source / "16112026_080000_SIP_MODELO.csv").write_text("1;3081/360;1;\n")
    (source / "17112026_164000_SIP_MODELO.csv").write_text("2;3082/360;1;\n")
    live = PipelineWorker(pipeline_config, "live", config)
    history = PipelineWorker(pipeline_config, "history", config)
    live.prepare()
    history.prepare()
    lock_path = history.source.silver_root / ".writer-AIALA-SIP_MODELO.lock"
    with closing(
        Intake(history.source.intake_path, lane="history", identity=history.identity)
    ) as old:
        old.db.execute("BEGIN EXCLUSIVE")
        with portalocker.Lock(str(lock_path), mode="a", timeout=0):
            with closing(
                Intake(live.source.intake_path, lane="live", identity=live.identity)
            ) as intake:
                began = time.monotonic()
                result = live.cycle(intake, phases=(("SIP_MODELO",),))
                assert time.monotonic() - began < pipeline_config.policy.latency_seconds
                assert result["SIP_MODELO"]["status"] == "APPLIED"
                assert not intake.pending("SIP_MODELO", 10)
        old.db.rollback()
    table = DeltaTable(str(live.source.table_path))
    assert table.to_pyarrow_table()["id_modelo_fenix"].to_pylist() == [2]
    assert not history.source.table_path.exists()


def test_worker_restart_preserves_lane_checkpoint_and_does_not_republish(config, pipeline_config):
    (config.sources[0][1] / "SIP_MODELO.csv").write_text("2;3082/360;1;\n")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    for _ in range(2):
        with closing(
            Intake(worker.source.intake_path, lane="live", identity=worker.identity)
        ) as intake:
            result = worker.cycle(intake, phases=(("SIP_MODELO",),))
            assert result["SIP_MODELO"]["status"] != "FAILED"
        worker = PipelineWorker(pipeline_config, "live", config)
        worker.prepare()
    assert DeltaTable(str(worker.source.table_path)).version() == 0
    assert worker.health()["lane"] == "live"


def test_worker_never_adopts_unmarked_existing_data(config, pipeline_config):
    pipeline_config.live.silver.mkdir(parents=True)
    (pipeline_config.live.silver / "existing.txt").write_text("unowned")
    with pytest.raises(IntegrityError, match="nonempty"):
        PipelineWorker(pipeline_config, "live", config).prepare()


def test_operating_budget_change_does_not_reset_live_identity(config, pipeline_config):
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    adjusted = replace(pipeline_config, policy=replace(pipeline_config.policy, poll_seconds=10))
    restarted = PipelineWorker(adjusted, "live", config)
    assert restarted.identity == worker.identity
    restarted.prepare()


def test_unconfirmed_result_cannot_be_acknowledged(config, pipeline_config):
    (config.sources[0][1] / "SIP_MODELO.csv").write_text("2;3082/360;1;\n")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()

    def failed(base, phase, stop, stop_path, on_result, *args, **kwargs):
        on_result("SIP_MODELO", {"status": "FAILED", "error": "injected"}, 0)

    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=(("SIP_MODELO",),), executor=failed)
        assert intake.pending("SIP_MODELO", 10)


def test_runtime_paths_remain_within_own_lane(config, pipeline_config):
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    assert worker.source.runtime_root.is_relative_to(pipeline_config.live.checkpoint)
    assert not pipeline_config.history.checkpoint.exists()
    assert not pipeline_config.history.control.exists()


@pytest.mark.parametrize(
    "status,state",
    [
        ("PARTIAL", "WAITING_REVIEW"),
        ("BLOCKED", "WAITING_REVIEW"),
        ("RETRY_WAIT", "WAITING_RETRY"),
        ("PAUSED_RETRY_EXHAUSTED", "WAITING_RETRY"),
    ],
)
def test_waiting_work_remains_durable_and_can_be_released(config, pipeline_config, status, state):
    (config.sources[0][1] / "SIP_MODELO.csv").write_text("2;3082/360;1;\n")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        window, _ = worker.capture(intake)
        cfg = worker.selected_config(intake, "SIP_MODELO", window)
        worker.record(intake, cfg, {"status": status}, 0)
        assert not intake.pending("SIP_MODELO", 10)
        assert tuple(intake.db.execute("SELECT state,published_at FROM inputs").fetchone()) == (
            state,
            None,
        )
        intake.release("SIP_MODELO", state)
        assert intake.pending("SIP_MODELO", 10) == cfg.intake_ids


def test_retry_queue_respects_native_pause_and_due_time(config, pipeline_config):
    import sqlite3

    (config.sources[0][1] / "SIP_MODELO.csv").write_text("2;3082/360;1;\n")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        window, _ = worker.capture(intake)
        ids = worker.selected_config(intake, "SIP_MODELO", window).intake_ids
        with sqlite3.connect(worker.source.control_path) as db:
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
            for paused, due, expected in [
                (True, 0, False),
                (False, time.time() + 100, False),
                (False, 0, True),
            ]:
                intake.defer(ids, {"status": "RETRY_WAIT"})
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES('retry_state',?)",
                    (json.dumps({"paused": paused, "next_at": due}),),
                )
                db.commit()
                worker.release_retries(intake, set())
                assert bool(intake.pending("SIP_MODELO", 10)) == expected


def test_slow_derivation_does_not_hold_up_next_live_publication(config, pipeline_config):
    from ingesta_delta_lake.workers import run_phase

    source = config.sources[0][1] / "SIP_MODELO.csv"
    source.write_text("2;3082/360;1;\n")
    pipelines = replace(pipeline_config, policy=replace(pipeline_config.policy, poll_seconds=1))
    worker = PipelineWorker(pipelines, "live", config)
    entered, release = threading.Event(), threading.Event()

    def execution(base, phase, stop, stop_path, on_result, *args, **kwargs):
        if phase == ("ASIGNACION_PRODUCCION_RAMAL",):
            entered.set()
            release.wait()
            on_result(phase[0], {"status": "NO_INPUTS"}, 0)
        else:
            run_phase(base, phase, stop, stop_path, on_result, *args, **kwargs)

    errors = []

    def run_worker():
        try:
            worker.run(
                phases=(("SIP_MODELO",), ("ASIGNACION_PRODUCCION_RAMAL",)), executor=execution
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_worker)
    thread.start()
    try:
        assert entered.wait(15)
        source.write_text("2;3082/360;1;\n3;3083/360;1;\n")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if DeltaTable.is_deltatable(str(worker.source.table_path)):
                table = DeltaTable(str(worker.source.table_path))
                if table.count() == 2:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("Next live input waited for the blocked derived job")
        assert not release.is_set()
    finally:
        worker.stop.set()
        release.set()
        thread.join(15)
    assert not thread.is_alive() and not errors


def test_ingestion_does_not_claim_or_modify_the_gold_owners_store(config, pipeline_config):
    pipeline_config.live.gold.mkdir(parents=True)
    marker = pipeline_config.live.gold / "already-owned-by-gold.json"
    marker.write_text("gold owner")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    assert marker.read_text() == "gold owner"
    assert worker.source.runtime_root.exists()


def test_approved_decision_paths_belong_to_their_lane(config, pipeline_config):
    decision = config.control_root / "associations" / "decisions.json"
    source = replace(config, association_decisions=decision)
    live, history = (PipelineWorker(pipeline_config, lane, source) for lane in ("live", "history"))
    assert (
        live.source.association_decisions
        == pipeline_config.live.control / "associations/decisions.json"
    )
    assert (
        history.source.association_decisions
        == pipeline_config.history.control / "associations/decisions.json"
    )
    assert not decision.exists()  # Moving the path never silently imports old decisions.


def test_consumer_validates_source_metadata_without_mounting_fenix(config):
    from ingesta_delta_lake.common import ConfigurationError

    missing = config.input_root.parent / "not-mounted"
    consumer = replace(config, input_root=missing, sources=(("AV21", missing / "AV21"),))
    with pytest.raises(ConfigurationError):
        consumer.validate()
    consumer.validate(require_inputs=False)


def test_live_cont_zc_dispatch_groups_every_ready_origin_in_the_current_cut(
    config, pipeline_config
):
    paths = {"AV21": config.input_root / "AV21"}
    for origin in ("AV22", "AV23"):
        paths[origin] = config.input_root / origin
        paths[origin].mkdir()
    for offset, (origin, directory) in enumerate(paths.items()):
        (directory / f"23112026_17300{offset}_CONT_ZC.csv").write_text(origin)
    worker = PipelineWorker(pipeline_config, "live", replace(config, sources=tuple(paths.items())))
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        window, summary = worker.capture(intake)
        selected = worker.selected_config(intake, "CONT_ZC", window)
        assert summary["captured"] == len(selected.intake_ids) == 3
        assert selected.processing_window == ("2026-11-23T17:30:00", "2026-11-23T17:40:00")

        def executor(base, phase, stop, stop_path, on_result, **kwargs):
            assert kwargs["configs"]["CONT_ZC"].intake_ids == selected.intake_ids
            on_result("CONT_ZC", {"status": "NO_CHANGE"}, 0.1)

        result, _ = worker.transform(selected, executor)
        timings = result["timing_zc"]
        assert (
            timings["oldest_capture_to_dispatch_seconds"]
            >= timings["newest_capture_to_dispatch_seconds"]
            >= 0
        )
