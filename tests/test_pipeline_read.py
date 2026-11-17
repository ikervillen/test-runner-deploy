from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from deltalake import CommitProperties, DeltaTable
from test_pipeline_archive import due, published
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.common import IngestaError
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.delta import DeltaStore
from ingesta_delta_lake.pipeline_archive import Archive
from ingesta_delta_lake.pipeline_runtime import PipelineWorker
from ingesta_delta_lake.review_adapters import adapter
from ingesta_delta_lake.service import run


def archived(config, pipelines, body="2;3082/360;1;\n"):
    snapshot = published(config, pipelines, body)
    with closing(Archive(pipelines.history, identity="history")) as archive:
        archive.copy(snapshot, "SIP_MODELO", policy=pipelines.policy, technical_now=due(snapshot))
        import json

        identity = json.loads((archive.root / "current.json").read_text())["publication_id"]
        return archive.root / (identity + ".json")


def context(worker, manifest, pipelines):
    return replace(
        worker.source,
        archive_root=pipelines.history.silver,
        archive_manifest=manifest.resolve(),
        pipeline_lane=worker.lane,
        read_max_rows=1000,
        read_batch_rows=1,
    )


def test_owner_can_validate_retired_catalog_without_recreating_it(config, pipeline_config):
    manifest = archived(config, pipeline_config)
    worker = PipelineWorker(pipeline_config, "live", config)
    cfg = context(worker, manifest, pipeline_config)
    with closing(Control(cfg)) as control:
        store = DeltaStore(cfg, control.control_id)
        metadata = {
            k: v for k, v in store.table().history(1)[0].items() if k.startswith("ingesta_")
        }
        metadata.update(
            ingesta_maintenance="archive-retirement", ingesta_operation_id="test-retire"
        )
        store.table().delete(commit_properties=CommitProperties(custom_metadata=metadata))
        assert store.table().count() == 0
        version, accepted = store.read()
        assert version == 1 and accepted == {2: "3082/360"}
        # A proposed correction is already a complete state; validate it once.
        choice = adapter(cfg, control.control_id)
        choice.validate({2: {"id_modelo_fenix": 2, "codigo_modelo_botella": "NEW"}})
        assert store.table().count() == 0


def test_recovery_uses_original_archive_then_returns_to_current_context(
    config, pipeline_config, monkeypatch
):
    worker = PipelineWorker(pipeline_config, "history", config)
    worker.prepare()
    original_manifest = archived(config, pipeline_config)
    cfg = replace(
        context(worker, original_manifest, pipeline_config), intake_path=None, intake_ids=()
    )
    source = config.sources[0][1] / "01012020_120000_SIP_MODELO.csv"
    source.write_text("2;3082/360;1;\n4;3084/360;1;\n")
    original_insert = DeltaStore.insert

    def interrupted(self, *args):
        original_insert(self, *args)
        raise OSError("lost write response")

    with monkeypatch.context() as patch:
        patch.setattr(DeltaStore, "insert", interrupted)
        with pytest.raises(IngestaError):
            run(cfg)
    with closing(Control(cfg)) as control:
        intent = control.prepared(control.pending()[0])
        assert intent["archive_context"]["manifest"] == str(original_manifest)
    latest_manifest = archived(config, pipeline_config, "2;3082/360;1;\n3;3083/360;1;\n")
    restarted = replace(cfg, archive_manifest=latest_manifest)
    run(restarted)
    with closing(Control(restarted)) as control:
        assert not control.pending()
        assert DeltaStore(restarted, control.control_id).read()[1] == {
            2: "3082/360",
            3: "3083/360",
            4: "3084/360",
        }
    assert DeltaTable(str(restarted.table_path)).count() == 1


def test_scoped_mia_reads_reuse_archived_evidence_with_dictionary_decoding(config, pipeline_config):
    import json

    from test_lista_defectos import line

    from ingesta_delta_lake.defects_control import DefectsControl
    from ingesta_delta_lake.defects_storage import DefectsStore
    from ingesta_delta_lake.pipeline_intake import Intake
    from ingesta_delta_lake.pipeline_publications import SilverSnapshot

    config = replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    path = config.sources[0][1] / "17112026_164000_LISTA_DEFECTOS.csv"
    path.write_text(line() + line(date="14/10/2026 0:01:00"))
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=((config.family,),))
    snapshot = SilverSnapshot.open(worker.storage)
    technical_now = datetime.fromisoformat(
        snapshot.manifest["tables"][config.family]["confirmed_at"]
    ) + timedelta(hours=8)
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        archive.copy(
            snapshot, config.family, policy=pipeline_config.policy, technical_now=technical_now
        )
        value = json.loads((archive.root / "current.json").read_text())
        cfg = context(worker, archive.root / (value["publication_id"] + ".json"), pipeline_config)
    with closing(DefectsControl(cfg)) as control:
        store = DefectsStore(cfg, control.control_id)
        metadata = {
            k: v for k, v in store.table().history(1)[0].items() if k.startswith("ingesta_")
        }
        metadata.update(
            ingesta_maintenance="archive-retirement", ingesta_operation_id="test-retire"
        )
        store.table().delete(commit_properties=CommitProperties(custom_metadata=metadata))
        store.set_scope([["AV21", "2026-10-14T00:01:00", "2026-10-14T00:01:00"]])
        rows = list(store.read()[1].values())
        assert len(rows) == 1 and rows[0]["FECHA"] == "2026-10-14T00:01:00"
