import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import replace

import pytest
from test_lista_rech import line

from ingesta_delta_lake.common import IntegrityError, canonical
from ingesta_delta_lake.maintenance import replace_control
from ingesta_delta_lake.rejections_storage import RejectionsControl
from ingesta_delta_lake.service import run


@contextmanager
def database(config):
    with closing(sqlite3.connect(config.control_path)) as db, db:
        yield db


@pytest.fixture
def mia(config):
    config = replace(config, family="LISTA_RECH", timestamp_policy="local_naive_test_v1")
    source = config.input_root / "AV21" / "LISTA_RECH.csv"
    source.write_text(line(), encoding="utf-8")
    run(config)
    source.write_text(line() + line(date="14/10/2026 0:10:00"), encoding="utf-8")
    run(config)
    return config


def seed_legacy(config):
    with closing(RejectionsControl(config)) as control:
        documents = {}
        for row in control.db.execute("SELECT * FROM batches ORDER BY rowid").fetchall():
            document = control.prepared(dict(row))
            documents[row[0]] = document
            for group in document["inspection_groups"]:
                control.db.execute(
                    "INSERT OR IGNORE INTO inspection_groups VALUES(?,?,?)",
                    (row[0], group["group_id"], canonical(group)),
                )
        control.db.commit()
        identity = control.control_id
        return identity, documents


def test_scoped_commits_do_not_materialize_the_same_audit_twice(mia):
    with closing(RejectionsControl(mia)) as control:
        assert control.db.execute("SELECT count(*) FROM inspection_groups").fetchone()[0] == 0
        assert (
            control.db.execute("SELECT sum(row_count) FROM inspection_current").fetchone()[0] == 2
        )
        assert control.setting("inspection_index_version") == control.setting("last_version")
        assert all(
            control.prepared(dict(row))["inspection_groups"]
            for row in control.db.execute("SELECT * FROM batches")
        )


def test_offline_retention_proves_archive_and_preserves_current_index_and_replay(mia):
    identity, documents = seed_legacy(mia)
    with database(mia) as db:
        before = db.execute("SELECT * FROM inspection_current ORDER BY group_id").fetchall()
        version = db.execute("SELECT value FROM settings WHERE key='last_version'").fetchone()[0]
    report = replace_control(mia, apply=True, remove_backup=True)
    archive = report["tables"]["_inspection_archive"]
    assert archive["archived_rows"] >= 2 and len(archive["batches"]) == len(documents)
    assert archive["coverage"] == {"delta_version": int(version), "published_rows": 2}
    with closing(RejectionsControl(mia)) as control:
        assert control.control_id == identity and control.setting("last_version") == version
        assert control.db.execute("SELECT count(*) FROM inspection_groups").fetchone()[0] == 0
        assert before == list(
            map(tuple, control.db.execute("SELECT * FROM inspection_current ORDER BY group_id"))
        )
        assert {bid: control.prepared(control.batch(bid)) for bid in documents} == documents
    assert run(mia)["inserted_this_attempt"] == 0


def test_disagreement_with_recovery_document_aborts_without_losing_evidence(mia):
    identity, _ = seed_legacy(mia)
    with database(mia) as db:
        db.execute(
            "UPDATE inspection_groups SET payload_json=? WHERE rowid="
            "(SELECT min(rowid) FROM inspection_groups)",
            (json.dumps({"changed": True}),),
        )
    with pytest.raises(IntegrityError, match="differs from its retained batch"):
        replace_control(mia, apply=True, remove_backup=True)
    with closing(RejectionsControl(mia)) as control:
        assert control.control_id == identity
        assert control.db.execute("SELECT count(*) FROM inspection_groups").fetchone()[0] >= 2
    assert not list(mia.control_root.glob("*.compact-*"))
    assert not list(mia.control_root.glob("*.before-compaction"))


def test_pending_batch_prevents_retirement_of_any_materialized_group(mia):
    _, _ = seed_legacy(mia)
    with closing(RejectionsControl(mia)) as control:
        control.prepare("unconfirmed", "op-pending", {"inserts": [], "inspection_groups": []})
    report = replace_control(mia, apply=True, remove_backup=True)
    assert report["pending"] == 1
    assert report["tables"]["_inspection_archive"]["archived_rows"] == 0
    with closing(RejectionsControl(mia)) as control:
        assert control.pending()[0]["operation_id"] == "op-pending"
        assert control.db.execute("SELECT count(*) FROM inspection_groups").fetchone()[0] >= 2


def test_incomplete_current_index_is_never_an_archival_basis(mia):
    seed_legacy(mia)
    with database(mia) as db:
        db.execute("UPDATE inspection_current SET row_count=row_count+1")
    with pytest.raises(IntegrityError, match="does not cover"):
        replace_control(mia, apply=True)


def test_unlinked_historical_evidence_is_retained(mia):
    seed_legacy(mia)
    with database(mia) as db:
        db.execute("INSERT INTO inspection_groups VALUES('unknown','unlinked',?)", ('{"keep":1}',))
    report = replace_control(mia, apply=True, remove_backup=True)
    assert report["tables"]["inspection_groups"]["rows"] == 1
    with database(mia) as db:
        assert db.execute("SELECT * FROM inspection_groups").fetchall() == [
            ("unknown", "unlinked", '{"keep":1}'),
        ]
