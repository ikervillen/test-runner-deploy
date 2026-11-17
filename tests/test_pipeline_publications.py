import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.pipeline_intake import Intake
from ingesta_delta_lake.pipeline_publications import SilverSnapshot, confirmation_age
from ingesta_delta_lake.pipeline_runtime import PipelineWorker


def test_reader_uses_confirmed_delta_without_opening_active_sqlite(
    config, pipeline_config, monkeypatch
):
    (config.sources[0][1] / "SIP_MODELO.csv").write_text("2;3082/360;1;\n")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=(("SIP_MODELO",),))
    snapshot = SilverSnapshot.open(pipeline_config.live)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("Reader opened SQLite"))
    projection = snapshot.control("SIP_MODELO")
    assert projection["cases"] == []
    assert projection["meta"]["version"] == 0
    assert projection["processing_window"] is None
    rows = [r for b in snapshot.batches("SIP_MODELO", batch_size=10) for r in b.to_pylist()]
    assert rows == [{"id_modelo_fenix": 2, "codigo_modelo_botella": "3082/360"}]


def test_snapshot_stays_pinned_while_owner_publishes_next_version(config, pipeline_config):
    source = config.sources[0][1] / "SIP_MODELO.csv"
    source.write_text("2;3082/360;1;\n")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=(("SIP_MODELO",),))
        first = SilverSnapshot.open(pipeline_config.live)
        source.write_text("2;3082/360;1;\n3;3083/360;1;\n")
        worker.cycle(intake, phases=(("SIP_MODELO",),))
    latest = SilverSnapshot.open(pipeline_config.live)
    assert first.table("SIP_MODELO").count() == 1
    assert latest.table("SIP_MODELO").count() == 2
    assert latest.manifest["parent"] == first.manifest["publication_id"]
    assert (
        SilverSnapshot.open(
            pipeline_config.live, publication_id=first.manifest["publication_id"]
        ).manifest
        == first.manifest
    )


def test_retention_is_based_on_confirmed_silver_not_industrial_time(config, pipeline_config):
    (config.sources[0][1] / "01012020_120000_SIP_MODELO.csv").write_text("2;3082/360;1;\n")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=(("SIP_MODELO",),))
    snapshot = SilverSnapshot.open(pipeline_config.live)
    ref = snapshot.manifest["tables"]["SIP_MODELO"]
    confirmed = datetime.fromisoformat(ref["confirmed_at"])
    assert (
        confirmation_age(ref, confirmed + timedelta(hours=7))
        < pipeline_config.policy.retention_seconds
    )
    assert (
        confirmation_age(ref, confirmed + timedelta(hours=8))
        == pipeline_config.policy.retention_seconds
    )


def test_manifest_tampering_is_rejected(config, pipeline_config):
    with pytest.raises(IntegrityError, match="hash"):
        SilverSnapshot(
            pipeline_config.live,
            {"schema_id": "fenix-silver-snapshot-v1", "publication_id": "a" * 64, "tables": {}},
        )


def test_first_blocked_input_publishes_review_evidence_without_claiming_a_table(
    config, pipeline_config
):
    (config.sources[0][1] / "SIP_MODELO.csv").write_text("2;3082/360;1;\n2;3082/361;1;\n")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        result = worker.cycle(intake, phases=(("SIP_MODELO",),))
        assert result["SIP_MODELO"]["status"] == "BLOCKED"
    snapshot = SilverSnapshot.open(worker.storage)
    assert "SIP_MODELO" not in snapshot.manifest["tables"]
    projection = snapshot.control("SIP_MODELO")
    assert projection["version"] is None and projection["cases"]
    assert projection["meta"]["quality_hash"]


def test_association_artifacts_are_published_without_exposing_private_controls(
    config, pipeline_config
):
    from dataclasses import replace
    from types import SimpleNamespace

    from ingesta_delta_lake.association_cas import read_blocks
    from ingesta_delta_lake.association_storage import artifact

    cfg = replace(config, publication_root=pipeline_config.live.exchange)
    descriptor = artifact(cfg, {"approved": "decision"})
    assert str(pipeline_config.live.exchange) in descriptor["path"]
    consumer = SimpleNamespace(control_root=pipeline_config.live.exchange / "artifacts")
    assert b'"approved":"decision"' in b"".join(read_blocks(consumer, descriptor))
    assert not config.control_root.exists()
