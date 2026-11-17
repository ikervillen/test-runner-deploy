from contextlib import closing
from datetime import datetime, timedelta

import pytest
from deltalake import DeltaTable
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake import pipeline_archive
from ingesta_delta_lake.pipeline_archive import Archive
from ingesta_delta_lake.pipeline_intake import Intake
from ingesta_delta_lake.pipeline_publications import SilverSnapshot
from ingesta_delta_lake.pipeline_runtime import PipelineWorker
from ingesta_delta_lake.pipeline_view import ArchiveSnapshot, SilverView


def published(config, pipelines, body="2;3082/360;1;\n"):
    (config.sources[0][1] / "01012020_120000_SIP_MODELO.csv").write_text(body)
    worker = PipelineWorker(pipelines, "live", config)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=(("SIP_MODELO",),))
    return SilverSnapshot.open(pipelines.live)


def due(snapshot):
    return datetime.fromisoformat(
        snapshot.manifest["tables"]["SIP_MODELO"]["confirmed_at"]
    ) + timedelta(hours=8)


def test_archive_copies_then_confirms_without_mutating_live(config, pipeline_config):
    snapshot = published(config, pipeline_config)
    live_version = snapshot.table("SIP_MODELO").version()
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        assert (
            archive.copy(
                snapshot,
                "SIP_MODELO",
                policy=pipeline_config.policy,
                technical_now=due(snapshot) - timedelta(seconds=1),
            )["status"]
            == "NOT_DUE"
        )
        result = archive.copy(
            snapshot, "SIP_MODELO", policy=pipeline_config.policy, technical_now=due(snapshot)
        )
        assert result["status"] == "ARCHIVED" and result["rows"] == 1
        assert result["receipt"]["source_publication"] == snapshot.manifest["publication_id"]
        assert (
            archive.copy(
                snapshot, "SIP_MODELO", policy=pipeline_config.policy, technical_now=due(snapshot)
            )["status"]
            == "UNCHANGED"
        )
    assert snapshot.table("SIP_MODELO").version() == live_version
    combined = SilverView(live=snapshot, archive=ArchiveSnapshot(pipeline_config.history)).rows(
        "SIP_MODELO", max_rows=10, batch_size=2
    )
    assert len(combined.rows) == 1 and not combined.conflicts


def test_lost_archive_commit_ack_recovers_without_duplicates(config, pipeline_config, monkeypatch):
    snapshot = published(config, pipeline_config)
    original = pipeline_archive.write_deltalake

    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("lost archive commit acknowledgement")

    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        with monkeypatch.context() as patch:
            patch.setattr(pipeline_archive, "write_deltalake", lost_ack)
            with pytest.raises(OSError, match="lost archive"):
                archive.copy(
                    snapshot,
                    "SIP_MODELO",
                    policy=pipeline_config.policy,
                    technical_now=due(snapshot),
                )
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        archive.copy(
            snapshot, "SIP_MODELO", policy=pipeline_config.policy, technical_now=due(snapshot)
        )
        table = DeltaTable(str(archive.target("SIP_MODELO")))
        assert table.count() == 1 and table.version() == 0
        assert (
            archive.db.execute("SELECT COUNT(*) FROM jobs WHERE state='PREPARED'").fetchone()[0]
            == 0
        )


def test_archive_resumes_bounded_batches(config, pipeline_config):
    from dataclasses import replace

    snapshot = published(config, pipeline_config, "2;3082/360;1;\n3;3083/360;1;\n")
    policy = replace(pipeline_config.policy, archive_batch_rows=1)
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        for _ in range(3):
            archive.copy(snapshot, "SIP_MODELO", policy=policy, technical_now=due(snapshot))
        table = DeltaTable(str(archive.target("SIP_MODELO")))
        assert table.count() == 2 and table.version() == 1


def test_independent_conflicting_dictionary_rows_are_exposed_for_review(config, pipeline_config):
    from ingesta_delta_lake.common import IntegrityError
    from ingesta_delta_lake.pipeline_rows import reconcile

    candidates = [
        {
            "row": {"id_modelo_fenix": 2, "codigo_modelo_botella": "3082/360"},
            "source_table": "live",
            "source_version": 0,
        },
        {
            "row": {"id_modelo_fenix": 2, "codigo_modelo_botella": "OTHER"},
            "source_table": "history",
            "source_version": 100,
        },
    ]
    with pytest.raises(IntegrityError, match="CROSS_LANE_CONFLICT"):
        reconcile("SIP_MODELO", candidates)


def test_published_closure_extends_open_ar_but_never_reopens_it():
    from ingesta_delta_lake.pipeline_rows import reconcile

    opened = {"COD_AR": 1, "FECHA_ON": "2026-11-17T10:00:00", "FECHA_OFF": None}
    closed = {**opened, "FECHA_OFF": "2026-11-17T10:10:00"}
    versions = [
        {"row": row, "source_table": lane, "source_version": 0}
        for row, lane in ((opened, "live"), (closed, "history"))
    ]
    assert reconcile("LISTA_AR", versions) == closed


def test_archived_authorized_key_change_never_resurrects_old_inspection(config, pipeline_config):
    from dataclasses import replace

    from test_lista_defectos import line
    from test_review_execution import command

    from ingesta_delta_lake.defects_control import DefectsControl
    from ingesta_delta_lake.pipeline_publications import publish_family
    from ingesta_delta_lake.review_adapters import REPLACE
    from ingesta_delta_lake.reviews import apply_command

    config = replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    source = config.sources[0][1] / "17112026_164000_LISTA_DEFECTOS.csv"
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        for inspected in (96, 97):
            source.write_text(line(inspected=inspected))
            worker.cycle(intake, phases=((config.family,),))
    with closing(DefectsControl(worker.source)) as control:
        old_snapshot = SilverSnapshot.open(worker.storage)
        old_now = datetime.fromisoformat(
            old_snapshot.manifest["tables"][config.family]["confirmed_at"]
        ) + timedelta(hours=8)
        with closing(Archive(pipeline_config.history, identity="history")) as archive:
            assert (
                archive.copy(
                    old_snapshot,
                    config.family,
                    policy=pipeline_config.policy,
                    technical_now=old_now,
                )["rows"]
                == 1
            )
        apply_command(worker.source, control, command(worker.source, control, REPLACE))
    publish_family(
        worker.source, worker.storage, lane="live", identity=worker.identity, family=config.family
    )
    snapshot = SilverSnapshot.open(worker.storage)
    immediate = SilverView(live=snapshot, archive=ArchiveSnapshot(pipeline_config.history)).rows(
        config.family, max_rows=20, batch_size=1
    )
    assert not immediate.conflicts
    assert len(immediate.rows) == 1 and immediate.rows[0]["INSPECC"] == 97
    technical_now = datetime.fromisoformat(
        snapshot.manifest["tables"][config.family]["confirmed_at"]
    ) + timedelta(hours=8)
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        result = archive.copy(
            snapshot, config.family, policy=pipeline_config.policy, technical_now=technical_now
        )
        assert result["rows"] == 2
    view = SilverView(archive=ArchiveSnapshot(pipeline_config.history))
    result = view.rows(config.family, max_rows=20, batch_size=1)
    assert not result.conflicts
    assert len(result.rows) == 1 and result.rows[0]["INSPECC"] == 97


@pytest.mark.parametrize("failure", ["before_commit", "after_pointer"])
def test_archive_recovery_preserves_prepared_evidence(
    config, pipeline_config, monkeypatch, failure
):
    snapshot = published(config, pipeline_config)
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        original = archive._publish

        def interrupted(*args, **kwargs):
            if failure == "after_pointer":
                original(*args)
            raise OSError("publication interrupted")

        with monkeypatch.context() as patch:
            if failure == "before_commit":
                patch.setattr(pipeline_archive, "write_deltalake", interrupted)
            else:
                patch.setattr(archive, "_publish", interrupted)
            with pytest.raises(OSError):
                archive.copy(
                    snapshot,
                    "SIP_MODELO",
                    policy=pipeline_config.policy,
                    technical_now=due(snapshot),
                )
        archive.recover()
        assert DeltaTable(str(archive.target("SIP_MODELO"))).count() == 1
        assert not list((archive.root / "work").glob("*.parquet"))
    result = SilverView(archive=ArchiveSnapshot(pipeline_config.history)).rows(
        "SIP_MODELO", max_rows=10, batch_size=1
    )
    assert len(result.rows) == 1


def test_query_budget_and_changed_archive_evidence_fail_closed(config, pipeline_config):
    import json

    from ingesta_delta_lake.common import IntegrityError

    snapshot = published(config, pipeline_config)
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        archive.copy(
            snapshot, "SIP_MODELO", policy=pipeline_config.policy, technical_now=due(snapshot)
        )
        with pytest.raises(IntegrityError, match="budget"):
            SilverView(live=snapshot, archive=ArchiveSnapshot(pipeline_config.history)).rows(
                "SIP_MODELO", max_rows=1, batch_size=1
            )
        manifest = json.loads((archive.root / "current.json").read_text())
        manifest["owner"] = "tampered"
        (archive.root / "current.json").write_text(json.dumps(manifest))
        with pytest.raises(IntegrityError, match="hash"):
            ArchiveSnapshot(pipeline_config.history)
        # The pinned immutable snapshot remains readable after pointer corruption.
        pinned = ArchiveSnapshot(
            pipeline_config.history,
            manifest_path=archive.root / (manifest["publication_id"] + ".json"),
        )
        assert len(list(pinned.records("SIP_MODELO", batch_size=1))) == 1


def test_incomplete_copy_does_not_hide_recent_authorized_deletion():
    from ingesta_delta_lake.common import IntegrityError
    from ingesta_delta_lake.pipeline_rows import reconcile

    row = {"id_modelo_fenix": 1, "codigo_modelo_botella": "A"}
    original = {"row": row, "source_table": "one", "source_version": 1}
    deleted = {**original, "source_version": 2, "change_type": "delete"}
    assert reconcile("SIP_MODELO", [original, deleted]) is None
    restored = {**original, "source_version": 3}
    assert reconcile("SIP_MODELO", [original, deleted, restored]) == row
    with pytest.raises(IntegrityError, match="DELETION_CONFLICT"):
        reconcile("SIP_MODELO", [deleted, {**original, "source_table": "another"}])
