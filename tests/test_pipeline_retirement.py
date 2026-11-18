from contextlib import closing
from dataclasses import replace
from datetime import timedelta

import pytest
from deltalake.table import TableMerger
from test_pipeline_archive import due, published
from test_pipeline_read import context
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.delta import DeltaStore
from ingesta_delta_lake.pipeline_archive import Archive
from ingesta_delta_lake.pipeline_retirement import journal_path, recover, retire
from ingesta_delta_lake.pipeline_runtime import PipelineWorker
from ingesta_delta_lake.pipeline_view import ArchiveSnapshot
from ingesta_delta_lake.service import run


def prepared(config, pipelines):
    snap = published(config, pipelines)
    with closing(Archive(pipelines.history, identity="history")) as archive:
        archive.copy(snap, "SIP_MODELO", policy=pipelines.policy, technical_now=due(snap))
        head = archive.receipt_head()
    manifest = ArchiveSnapshot(pipelines.history).manifest
    worker = PipelineWorker(pipelines, "live", config)
    cfg = context(
        worker,
        pipelines.history.exchange / "archive" / (manifest["publication_id"] + ".json"),
        pipelines,
    )
    return cfg, head, due(snap)


def test_retirement_keeps_accepted_catalog_and_never_modifies_history(config, pipeline_config):
    cfg, head, instant = prepared(config, pipeline_config)
    archive = ArchiveSnapshot(pipeline_config.history).manifest
    result = retire(
        cfg,
        pipeline_config.live,
        pipeline_config.history,
        head,
        policy=pipeline_config.policy,
        technical_now=instant,
    )
    assert result["retired_rows"] == 1 and result["delta_version"] == 1
    with closing(Control(cfg)) as control:
        store = DeltaStore(cfg, control.control_id)
        assert store.table().count() == 0
        assert store.read()[1] == {2: "3082/360"}
        assert control.setting("last_version") == "1" and not control.pending()
    assert ArchiveSnapshot(pipeline_config.history).manifest == archive
    assert not journal_path(cfg).exists()
    run(replace(cfg, intake_path=None, intake_ids=()))
    with closing(Control(cfg)) as control:
        assert DeltaStore(cfg, control.control_id).table().count() == 0


@pytest.mark.parametrize("when", ["before", "after"])
def test_retirement_crash_recovers_exactly_once(config, pipeline_config, monkeypatch, when):
    cfg, head, instant = prepared(config, pipeline_config)
    original = TableMerger.execute

    def interrupted(self):
        if when == "after":
            original(self)
        raise OSError("retirement lost response")

    with monkeypatch.context() as patch:
        patch.setattr(TableMerger, "execute", interrupted)
        with pytest.raises(OSError):
            retire(
                cfg,
                pipeline_config.live,
                pipeline_config.history,
                head,
                policy=pipeline_config.policy,
                technical_now=instant,
            )
    assert journal_path(cfg).exists()
    # Ordinary ingestion reconciles the maintenance journal before processing new work.
    run(replace(cfg, intake_path=None, intake_ids=()))
    with closing(Control(cfg)) as control:
        assert not journal_path(cfg).exists()
        assert DeltaStore(cfg, control.control_id).table().version() == 1
        assert DeltaStore(cfg, control.control_id).read()[1] == {2: "3082/360"}
        assert recover(cfg, control) is None


def test_retirement_waits_for_technical_retention(config, pipeline_config):
    cfg, head, instant = prepared(config, pipeline_config)
    result = retire(
        cfg,
        pipeline_config.live,
        pipeline_config.history,
        head,
        policy=pipeline_config.policy,
        technical_now=instant - timedelta(seconds=1),
    )
    assert result["status"] == "RETIREMENT_NOT_DUE"
    with closing(Control(cfg)) as control:
        assert DeltaStore(cfg, control.control_id).table().count() == 1
    with pytest.raises(IntegrityError, match="live owner"):
        retire(
            replace(cfg, pipeline_lane="history"),
            pipeline_config.live,
            pipeline_config.history,
            head,
            policy=pipeline_config.policy,
            technical_now=instant,
        )


def test_mia_index_keeps_archived_inspections_when_fresh_data_arrives(config, pipeline_config):
    from datetime import datetime

    from test_lista_defectos import line

    from ingesta_delta_lake.defects_control import DefectsControl
    from ingesta_delta_lake.defects_storage import DefectsStore
    from ingesta_delta_lake.pipeline_intake import Intake
    from ingesta_delta_lake.pipeline_publications import SilverSnapshot

    cfg = replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")
    source = config.sources[0][1] / "17112026_164000_LISTA_DEFECTOS.csv"
    source.write_text(line())
    worker = PipelineWorker(pipeline_config, "live", cfg)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=((cfg.family,),))
        snapshot = SilverSnapshot.open(worker.storage)
        instant = datetime.fromisoformat(
            snapshot.manifest["tables"][cfg.family]["confirmed_at"]
        ) + timedelta(hours=8)
        with closing(Archive(pipeline_config.history, identity="history")) as archive:
            archive.copy(snapshot, cfg.family, policy=pipeline_config.policy, technical_now=instant)
            head = archive.receipt_head()
        worker.refresh_archive()
        selected = worker.selected_config(intake, cfg.family, None)
        result = retire(
            selected,
            worker.storage,
            pipeline_config.history,
            head,
            policy=pipeline_config.policy,
            technical_now=instant,
        )
        assert result["retired_rows"] == 1
        source.write_text(line() + line(date="14/10/2026 0:01:00"))
        result = worker.cycle(intake, phases=((cfg.family,),))[cfg.family]
        assert result["status"] == "APPLIED", result
    with closing(DefectsControl(selected)) as control:
        assert DefectsStore(selected, control.control_id).table().count() == 1
        assert len(DefectsStore(selected, control.control_id).read()[1]) == 2


def test_old_copy_proof_cannot_retire_a_newly_republished_equal_value(config, pipeline_config):
    from datetime import datetime

    from test_cont_zc import line

    from ingesta_delta_lake.pipeline_intake import Intake
    from ingesta_delta_lake.pipeline_publications import SilverSnapshot
    from ingesta_delta_lake.zc_storage import ZcControl, ZcStore

    cfg = replace(config, family="CONT_ZC", timestamp_policy="local_naive_test_v1")
    (config.sources[0][1] / "17112026_164000_CONT_ZC.csv").write_text(line())
    worker = PipelineWorker(pipeline_config, "live", cfg)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=((cfg.family,),))
    snapshot = SilverSnapshot.open(worker.storage)
    instant = datetime.fromisoformat(
        snapshot.manifest["tables"][cfg.family]["confirmed_at"]
    ) + timedelta(hours=8)
    with closing(Archive(pipeline_config.history, identity="history")) as archive:
        archive.copy(snapshot, cfg.family, policy=pipeline_config.policy, technical_now=instant)
        head = archive.receipt_head()
    worker.refresh_archive()
    selected = replace(worker.source, **worker.archive_context)
    with closing(ZcControl(selected)) as control:
        store = ZcStore(selected, control.control_id)
        for version, value in enumerate([1700, 1614]):
            row = next(iter(store.read()[1].values()))
            store.insert([{**row, "CONT_ZC_1": value}], f"revision-{version}", version)
        with control.db:
            control.db.execute("UPDATE settings SET value='2' WHERE key='last_version'")
    result = retire(
        selected,
        worker.storage,
        pipeline_config.history,
        head,
        policy=pipeline_config.policy,
        technical_now=instant,
    )
    assert result["status"] == "RETIREMENT_UNCHANGED"
    with closing(ZcControl(selected)) as control:
        assert ZcStore(selected, control.control_id).table().count() == 1


def test_retired_reconciliation_preserves_the_callers_transaction():
    import sqlite3
    from types import SimpleNamespace

    import pyarrow as pa

    from ingesta_delta_lake.pipeline_retired import initialize, synchronize
    from ingesta_delta_lake.pipeline_rows import business_key

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
    control = SimpleNamespace(
        db=db,
        config=SimpleNamespace(family="SIP_MODELO", read_max_rows=10),
        setting=lambda key: db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[
            0
        ],
    )
    initialize(control)
    row = {"id_modelo_fenix": 2, "codigo_modelo_botella": "MODEL"}
    db.execute("INSERT INTO settings VALUES('retirement_index_version','0')")
    db.execute("INSERT INTO retired_current VALUES(?,?)", (business_key("SIP_MODELO", row), "hash"))
    db.commit()
    events = pa.Table.from_pylist([{**row, "_commit_version": 1, "_change_type": "insert"}])
    table = SimpleNamespace(version=lambda: 1, history=lambda: [], load_cdf=lambda *_: events)
    with pytest.raises(RuntimeError, match="outer failure"), db:
        db.execute("INSERT INTO settings VALUES('outer','pending')")
        synchronize(control, table)
        assert db.in_transaction
        raise RuntimeError("outer failure")
    assert db.execute("SELECT value FROM settings WHERE key='outer'").fetchone() is None
    assert db.execute("SELECT count(*) FROM retired_current").fetchone()[0] == 1
    db.close()
