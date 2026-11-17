import json
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
