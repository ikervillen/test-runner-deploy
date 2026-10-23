import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest
from deltalake import DeltaTable
from test_lista_rech import line

from ingesta_delta_lake.common import IntegrityError, canonical
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.control_archive import read
from ingesta_delta_lake.maintenance import replace_control
from ingesta_delta_lake.rejections_storage import RejectionsControl
from ingesta_delta_lake.service import run


def test_migration_archives_legacy_and_preserves_identity_and_replay(config, source):
    run(config)
    with closing(Control(config)) as control:
        identity = control.control_id
        originals = [dict(row) for row in control.db.execute("SELECT * FROM batches")]
    before = DeltaTable(str(config.table_path)).version()
    report = replace_control(config, apply=True, reformulate=True)
    assert report["tables"]["format"] == 3 and not report["backup_removed"]
    with closing(Control(config)) as control:
        assert control.control_id == identity and control.compact
        assert not control.db.execute("SELECT 1 FROM row_intents").fetchone()
        root = config.control_root / "audit" / config.family / identity
        archive = json.loads(control.setting("legacy_audit"))["legacy_preparations"]
        archived = list(read(root, archive))
        assert archived[0]["prepared"] == json.loads(originals[0]["prepared_json"])
        assert "$control_receipt" in control.prepared(control.batch(originals[0]["batch_id"]))
    assert run(config)["status"] == "NO_CHANGE"
    assert DeltaTable(str(config.table_path)).version() == before
    source.write_text("754;3081/360;1;\n755;3082/360;1;\n", encoding="utf-8")
    assert run(config)["new_entries"] == 1


def test_pending_material_is_preserved_and_failure_does_not_replace(config, source, monkeypatch):
    run(config)
    with closing(Control(config)) as control:
        data = {"inserts": [{"id_modelo_fenix": 10}]}
        control.prepare("pending", "pending-operation", data)
        control.mark("pending", "UNKNOWN")
    report = replace_control(config, apply=True, reformulate=True)
    assert report["tables"]["pending_artifacts"] == 1
    with closing(Control(config)) as control:
        assert control.prepared(control.pending()[0]) == data
        assert control.pending()[0]["state"] == "UNKNOWN"


def test_mia_migration_proves_historical_groups_and_keeps_current_index(config):
    config = replace(config, family="LISTA_RECH", timestamp_policy="local_naive_test_v1")
    path = config.input_root / "AV21" / "LISTA_RECH.csv"
    path.write_text(line(), encoding="utf-8")
    first = run(config)
    with closing(RejectionsControl(config)) as control:
        batch = control.batch(first["batch_id"])
        for group in control.prepared(batch)["inspection_groups"]:
            control.db.execute(
                "INSERT INTO inspection_groups VALUES(?,?,?)",
                (batch["batch_id"], group["group_id"], canonical(group)),
            )
        control.db.commit()
        original = list(map(tuple, control.db.execute("SELECT * FROM inspection_current")))
    report = replace_control(config, apply=True, reformulate=True)
    assert report["tables"]["audit"]["inspection_groups"]["archived_rows"] == 1
    with closing(RejectionsControl(config)) as control:
        current = list(map(tuple, control.db.execute("SELECT * FROM inspection_current")))
        assert [r[:2] + r[3:] for r in current] == [r[:2] + r[3:] for r in original]
        assert json.loads(current[0][2]) == {"accepted_inspection_values": [96]}
        assert not control.db.execute("SELECT 1 FROM inspection_groups").fetchone()
    path.write_text(line() + line(date="14/10/2026 0:10:00"), encoding="utf-8")
    result = run(config)
    assert result["published_rows"] == result["resolved_groups"] == 2
    assert result["resolved_inspection_total"] == 192
    assert run(config)["new_entries"] == 0


def test_failed_archive_leaves_original_control_unchanged(config, source, monkeypatch):
    from ingesta_delta_lake.control_archive import Archive

    run(config)
    with sqlite3.connect(config.control_path) as db:
        previous = db.execute("SELECT * FROM batches").fetchall()
    monkeypatch.setattr(Archive, "finish", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        replace_control(config, apply=True, reformulate=True)
    with sqlite3.connect(config.control_path) as db:
        assert db.execute("SELECT * FROM batches").fetchall() == previous
        assert not db.execute("SELECT value FROM settings WHERE key='control_format'").fetchone()
    assert not list(config.control_root.glob("*.before-compaction"))


def test_bad_archive_checksum_is_detected(tmp_path):
    from ingesta_delta_lake.control_archive import Archive

    archive = Archive(tmp_path, "test")
    archive.append({"value": 1})
    receipt = archive.finish()
    assert list(read(tmp_path, receipt)) == [{"value": 1}]
    with pytest.raises(IntegrityError):
        list(read(tmp_path, {**receipt, "sha256": "0" * 64}))
    with pytest.raises(IntegrityError):
        list(read(tmp_path, {**receipt, "file": "../outside.jsonl.gz"}))
