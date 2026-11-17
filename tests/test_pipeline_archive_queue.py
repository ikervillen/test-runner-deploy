from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from deltalake import DeltaTable
from test_pipeline_archive import due, published
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.pipeline_archive import Archive
from ingesta_delta_lake.pipeline_archive_queue import ArchiveQueue
from ingesta_delta_lake.pipeline_intake import Intake
from ingesta_delta_lake.pipeline_runtime import PipelineWorker


def test_queue_finds_due_ancestor_while_latest_publication_is_still_live(config, pipeline_config):
    old = published(config, pipeline_config, "2;3082/360;1;\n3;3083/360;1;\n")
    published(config, pipeline_config, "2;3082/360;1;\n3;3083/360;1;\n4;3084/360;1;\n")
    policy = replace(pipeline_config.policy, archive_batch_rows=1, archive_publications_per_turn=1)
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        queue = ArchiveQueue(archive, pipeline_config.live)
        assert queue.turn(policy=policy, technical_now=due(old))["status"] == "NOT_DUE"
        assert queue.turn(policy=policy, technical_now=due(old))["rows"] == 1
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        queue = ArchiveQueue(archive, pipeline_config.live)
        for _ in range(4):
            queue.turn(policy=policy, technical_now=due(old))
        copied = DeltaTable(str(archive.target("SIP_MODELO"))).to_pyarrow_table().to_pylist()
        assert len(copied) == 2
        assert {r["__archive_source_version"] for r in copied} == {0}
        assert old.table("SIP_MODELO").count() == 2
        assert (
            queue.db.execute("SELECT count(*) FROM archive_queue WHERE complete=1").fetchone()[0]
            == 1
        )


def test_archive_queue_alternates_large_and_small_families(config, pipeline_config):
    published(config, pipeline_config, "2;3082/360;1;\n3;3083/360;1;\n")
    (config.sources[0][1] / "17112026_164000_SIP_DEFECTO.csv").write_text(
        "1;400;0400:Columpio;Columpio;0;\n"
    )
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=(("SIP_DEFECTO",),))
    policy = replace(pipeline_config.policy, archive_batch_rows=1)
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        queue = ArchiveQueue(archive, pipeline_config.live)
        clock = datetime.now(UTC) + timedelta(hours=9)
        first = queue.turn(policy=policy, technical_now=clock)
        second = queue.turn(policy=policy, technical_now=clock)
        assert first["family"] == "SIP_MODELO" and not first["complete"]
        assert second["family"] == "SIP_DEFECTO"


def test_live_restart_keeps_confirmed_archive_reference_when_history_pointer_is_unavailable(
    config, pipeline_config
):
    old = published(config, pipeline_config)
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        ArchiveQueue(archive, pipeline_config.live).turn(
            policy=pipeline_config.policy, technical_now=due(old)
        )
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    worker.refresh_archive()
    pinned = worker.archive_context["archive_manifest"]
    (pipeline_config.history.exchange / "archive" / "current.json").write_text("broken publication")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    worker.refresh_archive()
    assert worker.archive_context["archive_manifest"] == pinned
    source = config.sources[0][1] / "17112026_164000_SIP_MODELO.csv"
    source.write_text("5;3085/360;1;\n")
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        result = worker.cycle(intake, phases=(("SIP_MODELO",),))
    assert result["SIP_MODELO"]["status"] == "APPLIED"


def test_empty_history_queue_does_not_wait_for_live_to_start(config, pipeline_config):
    worker = PipelineWorker(pipeline_config, "history", config)
    worker.run(once=True, phases=(("SIP_MODELO",),))
    assert not pipeline_config.live.checkpoint.exists()
    assert not pipeline_config.live.silver.exists()
