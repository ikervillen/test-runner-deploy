import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import portalocker
import pytest
from deltalake import DeltaTable

from deployment import recovery
from ingesta_delta_lake import defects_service
from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.service import run


@pytest.fixture(params=["LISTA_RECH", "LISTA_DEFECTOS"])
def pending(config, request, monkeypatch):
    cfg = replace(config, family=request.param, timestamp_policy="local_naive_test_v1")
    path = cfg.input_root / "AV21" / (cfg.family + ".csv")
    fields = (
        ["14/10/2026 07:30:00", 0, 1, 13, 96, 12, 0, "", ""]
        if cfg.family == "LISTA_RECH"
        else ["14/10/2026 07:30:00", 0, 1, 13, 2, 96, 12, *([0] * 10), ""]
    )
    path.write_text(";".join(map(str, fields)) + ";\n")

    def interrupt(*_):
        raise OSError("Interrupted before entering WRITING")

    with monkeypatch.context() as patch:
        patch.setattr(defects_service, "_apply", interrupt)
        with pytest.raises(OSError):
            run(cfg)
    return cfg, path


def query(cfg, sql, args=()):
    with sqlite3.connect(cfg.control_path) as db:
        return db.execute(sql, args).fetchall()


def test_requeue_preserves_exact_payload_and_intents_and_replays_missing_input(pending):
    cfg, path = pending
    original = query(cfg, "SELECT prepared_json FROM batches")[0][0]
    intents = query(cfg, "SELECT after_json FROM observation_intents")
    path.unlink()  # Source rotation must not remove a captured, unconsumed version.
    result = recovery.recover_initial(cfg)
    assert result["status"] == "REQUEUED"
    event = result["events"][0]
    archive = Path(event["archive"]["path"])
    assert archive.read_bytes() == original.encode("utf-8")
    assert recovery.file_hash(archive) == event["archive"]["sha256"]
    assert query(cfg, "SELECT after_json FROM observation_intents") == intents
    assert query(cfg, "SELECT state FROM batches") == [("ABORTED_UNWRITTEN",)]
    assert query(cfg, "SELECT state FROM observation_intents") == [("ABORTED_UNWRITTEN",)]
    assert not cfg.table_path.exists()
    assert recovery.recover_initial(cfg)["status"] == "NO_PENDING"
    result = run(cfg)
    assert result["processed_versions"] == 1 and result["published_rows"] == 1
    assert DeltaTable(str(cfg.table_path)).version() == 0
    assert query(cfg, "SELECT COUNT(*) FROM maintenance_events") == [(1,)]
    assert run(cfg)["processed_versions"] == 0


@pytest.mark.parametrize("state", ["WRITING", "UNKNOWN"])
def test_uncertain_commit_is_never_cancelled(pending, state):
    cfg, _ = pending
    query(cfg, "UPDATE batches SET state=?", (state,))
    before = query(cfg, "SELECT prepared_json FROM batches")
    with pytest.raises(IntegrityError, match="WRITING/UNKNOWN"):
        recovery.recover_initial(cfg)
    assert query(cfg, "SELECT prepared_json FROM batches") == before
    assert not (cfg.control_root / "recovery").exists()


@pytest.mark.parametrize("condition", ["destination", "binding", "intent", "bronze", "receipts"])
def test_missing_proof_fails_without_changing_pending_state(pending, condition):
    cfg, _ = pending
    if condition == "destination":
        cfg.table_path.mkdir(parents=True)
        (cfg.table_path / "orphan.parquet").write_bytes(b"unexplained")
    elif condition == "binding":
        query(cfg, "UPDATE settings SET value='{}' WHERE key='binding'")
    elif condition == "intent":
        query(cfg, "UPDATE observation_intents SET state='CONFIRMED'")
    elif condition == "bronze":
        next(cfg.bronze_root.rglob("*.csv")).write_bytes(b"changed")
    else:
        query(cfg, "DELETE FROM receipts")
    with pytest.raises(IntegrityError):
        recovery.recover_initial(cfg)
    assert query(cfg, "SELECT state FROM batches") == [("PREPARED",)]


def test_recovery_requires_exclusive_worker_lock(pending):
    cfg, _ = pending
    with portalocker.Lock(str(cfg.silver_root / ".runtime.lock"), mode="a", timeout=0):
        with pytest.raises(portalocker.exceptions.LockException):
            recovery.recover_initial(cfg)
    assert query(cfg, "SELECT state FROM batches") == [("PREPARED",)]


def test_interruption_after_archive_before_commit_is_repeatable(pending, monkeypatch):
    cfg, _ = pending
    original = recovery.archive

    def fail(*args):
        original(*args)
        raise OSError("Interrupted after fsync, before SQLite commit")

    with monkeypatch.context() as patch:
        patch.setattr(recovery, "archive", fail)
        with pytest.raises(OSError):
            recovery.recover_initial(cfg)
    assert query(cfg, "SELECT state FROM batches") == [("PREPARED",)]
    assert recovery.recover_initial(cfg)["status"] == "REQUEUED"


def test_wrong_existing_archive_is_not_overwritten(pending):
    cfg, _ = pending
    batch_id = query(cfg, "SELECT batch_id FROM batches")[0][0]
    folder = cfg.control_root / "recovery"
    folder.mkdir()
    path = folder / (batch_id + ".prepared.json")
    path.write_text("do not overwrite")
    with pytest.raises(IntegrityError, match="differs"):
        recovery.recover_initial(cfg)
    assert path.read_text() == "do not overwrite"
    assert query(cfg, "SELECT state FROM batches") == [("PREPARED",)]


def test_blob_archive_does_not_load_full_json_into_python(pending):
    cfg, _ = pending
    payload = json.dumps({"evidence": "x" * (16 * 1024 * 1024)})
    query(cfg, "UPDATE batches SET prepared_json=?", (payload,))
    del payload
    import tracemalloc

    tracemalloc.start()
    try:
        result = recovery.recover_initial(cfg)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["status"] == "REQUEUED" and peak < 5 * 1024 * 1024
