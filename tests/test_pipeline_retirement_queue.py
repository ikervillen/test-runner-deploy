import json
import threading
import time
from contextlib import closing
from dataclasses import asdict, replace

from test_pipeline_retirement import prepared
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.control import Control
from ingesta_delta_lake.delta import DeltaStore
from ingesta_delta_lake.pipeline_intake import Intake
from ingesta_delta_lake.pipeline_retirement_queue import RetirementQueue
from ingesta_delta_lake.pipeline_runtime import PipelineWorker
from ingesta_delta_lake.pipeline_view import ArchiveSnapshot
from ingesta_delta_lake.workers import decode_config, run_phase


def test_retirement_queue_is_durable_and_executes_in_its_own_process(config, pipeline_config):
    cfg, head, _ = prepared(config, pipeline_config)
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.refresh_archive()
    policy = replace(pipeline_config.policy, window_seconds=1, retention_seconds=1)
    with closing(Intake(cfg.intake_path, lane="live", identity=worker.identity)) as intake:
        queue = RetirementQueue(intake, pipeline_config.history)
        assert queue.discover(ArchiveSnapshot(pipeline_config.history), policy=policy) == 1
        assert queue.next_config(cfg, policy=policy, unavailable={cfg.family}) is None
        job = queue.next_config(cfg, policy=policy, unavailable=set())
        assert job.retirement_job["receipt"] == head
        assert decode_config(json.dumps(asdict(job), default=str)) == job
        time.sleep(1.1)
        events = []
        run_phase(
            cfg,
            (cfg.family,),
            threading.Event(),
            cfg.runtime_root / "worker.stop",
            lambda *event: events.append(event),
            limit=1,
            configs={cfg.family: job},
        )
        result = events[0][1]
        assert result["status"] == "RETIRED", result
        queue.acknowledge({**result, "publication_error": "injected"})
        assert queue.next_config(cfg, policy=policy, unavailable=set()) is not None
        worker.record(intake, job, result, events[0][2])
    with closing(Intake(cfg.intake_path, lane="live", identity=worker.identity)) as intake:
        queue = RetirementQueue(intake, pipeline_config.history)
        assert queue.discover(ArchiveSnapshot(pipeline_config.history), policy=policy) == 0
        assert queue.next_config(cfg, policy=policy, unavailable=set()) is None
    with closing(Control(cfg)) as control:
        assert DeltaStore(cfg, control.control_id).table().count() == 0


def test_live_maintenance_never_takes_an_active_or_fresh_family(config, pipeline_config):
    cfg, _, _ = prepared(config, pipeline_config)
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.refresh_archive()
    with closing(Intake(cfg.intake_path, lane="live", identity=worker.identity)) as intake:
        assert worker.maintenance_config(intake, {}, {cfg.family}, set()) is None
        job = worker.maintenance_config(intake, {}, set(), set())
        assert job is not None
        assert worker.maintenance_config(intake, {cfg.family: (None, job)}, set(), set()) is None
        source = config.sources[0][1] / "01012020_120000_SIP_MODELO.csv"
        source.write_text("2;3082/360;1;\n3;3083/360;1;\n")
        worker.capture(intake)
        assert worker.maintenance_config(intake, {}, set(), set()) is None
