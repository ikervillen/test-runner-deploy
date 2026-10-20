"""Explicit maintenance for a provably unwritten initial MIA intent.

Run with the stopped release's image and its exact mounts/identity. Never handles
WRITING/UNKNOWN or an existing destination. The original JSON is archived in
bounded chunks before a small, atomic SQLite disposition replaces it.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack, closing
from dataclasses import replace
from pathlib import Path

import portalocker

from ingesta_delta_lake.common import IntegrityError, canonical, now


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive(db, rowid, root, batch_id):
    root.mkdir(parents=True, exist_ok=True)
    target = root / (batch_id + ".prepared.json")
    descriptor = hashlib.sha256()
    size = 0
    fd, temporary = tempfile.mkstemp(prefix=".archive-", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            with db.blobopen("batches", "prepared_json", rowid, readonly=True) as blob:
                if shutil.disk_usage(root).free < 2 * len(blob) + 256 * 1024 * 1024:
                    raise IntegrityError("Insufficient free space for archive and recovery WAL")
                while block := blob.read(1024 * 1024):
                    stream.write(block)
                    descriptor.update(block)
                    size += len(block)
            stream.flush()
            os.fsync(stream.fileno())
        sha = descriptor.hexdigest()
        if target.exists():
            if target.stat().st_size != size or file_hash(target) != sha:
                raise IntegrityError("Existing recovery archive differs from the original intent")
        else:
            os.link(temporary, target)
            if os.name != "nt":
                directory = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        if file_hash(target) != sha:
            raise IntegrityError("Recovery archive verification failed")
        return {"path": str(target), "sha256": sha, "bytes": size}
    finally:
        Path(temporary).unlink(missing_ok=True)


def recover_initial(config):
    config.validate()
    if config.family not in {"LISTA_RECH", "LISTA_DEFECTOS"}:
        raise IntegrityError("Maintenance is restricted to initial MIA ingestion")
    if not config.control_path.is_file():
        raise IntegrityError("The existing control must be present")
    with ExitStack() as stack:
        for lock in (".runtime.lock", ".writer.lock"):
            stack.enter_context(
                portalocker.Lock(str(config.silver_root / lock), mode="a", timeout=0)
            )
        db = stack.enter_context(closing(sqlite3.connect(config.control_path, timeout=5)))
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA cache_size=-8192")
        db.execute("BEGIN IMMEDIATE")
        try:
            binding = canonical(
                {
                    "plant": config.plant,
                    "silver_root": str(config.silver_root),
                    "bronze_root": str(config.bronze_root),
                    "contract": config.contract.name,
                }
            )
            values = dict(db.execute("SELECT key,value FROM settings"))
            if values.get("binding") != binding or not values.get("control_id"):
                raise IntegrityError("Recovery scope does not match the control")
            # Any destination content is ambiguous, including orphan Parquet files.
            if config.table_path.exists() and any(config.table_path.iterdir()):
                raise IntegrityError("Destination is not empty; reconcile with the owner release")
            if values.get("last_version", "None") != "None":
                raise IntegrityError("Control has already confirmed a Delta version")
            pending = db.execute(
                "SELECT rowid,batch_id,operation_id,state,delta_version FROM batches "
                "WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
            ).fetchall()
            if any(
                state != "PREPARED" or version is not None for _, _, _, state, version in pending
            ):
                raise IntegrityError("Only PREPARED, never WRITING/UNKNOWN, may be requeued")
            if db.execute(
                "SELECT COUNT(*) FROM observation_intents "
                "WHERE (state!='PREPARED' AND state!='ABORTED_UNWRITTEN') "
                "OR delta_version IS NOT NULL"
            ).fetchone()[0]:
                raise IntegrityError("Some row intents may have reached Delta")
            # Receipts constitute the durable replay queue, even if input files disappear.
            receipts = 0
            for (text,) in db.execute("SELECT descriptor_json FROM receipts"):
                receipt = json.loads(text)
                path = Path(receipt["bronze_path"]).resolve()
                if (
                    not path.is_relative_to(config.bronze_root)
                    or file_hash(path) != receipt["sha256"]
                ):
                    raise IntegrityError("Original Bronze evidence missing or altered")
                receipts += 1
            if pending and not receipts:
                raise IntegrityError("No durable input receipts for replay")
            db.execute(
                "CREATE TABLE IF NOT EXISTS maintenance_events("
                "batch_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )
            events = []
            for rowid, batch_id, operation, _, _ in pending:
                evidence = archive(db, rowid, config.control_root / "recovery", batch_id)
                event = {
                    "action": "REQUEUE_UNWRITTEN_INITIAL_MIA_V1",
                    "at": now(),
                    "batch_id": batch_id,
                    "operation_id": operation,
                    "control_id": values["control_id"],
                    "archive": evidence,
                    "verified_receipts": receipts,
                    "destination": "ABSENT",
                    "previous_state": "PREPARED",
                }
                # Replaces the large TEXT with a verified pointer; keeps all row evidence.
                db.execute(
                    "UPDATE batches SET state='ABORTED_UNWRITTEN',prepared_json=? "
                    "WHERE batch_id=? AND state='PREPARED'",
                    (canonical({"archived_unwritten_intent": evidence}), batch_id),
                )
                db.execute(
                    "UPDATE observation_intents SET state='ABORTED_UNWRITTEN' WHERE operation_id=?",
                    (operation,),
                )
                db.execute(
                    "INSERT INTO maintenance_events VALUES(?,?)", (batch_id, canonical(event))
                )
                events.append(event)
            if events:
                db.execute(
                    "INSERT INTO settings VALUES('requeue_generation',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(int(values.get("requeue_generation", "0")) + 1),),
                )
                db.execute(
                    "UPDATE attempts SET status='INTERRUPTED_UNWRITTEN',finished_at=? "
                    "WHERE status='RUNNING'",
                    (now(),),
                )
            db.commit()
        except BaseException:
            db.rollback()
            raise
        checkpoint = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint[0] != 0:
            raise IntegrityError("Recovery recorded, but WAL checkpoint must be retried")
        return {"status": "REQUEUED" if events else "NO_PENDING", "events": events}


def main():
    from ingesta_delta_lake.runtime import marker, settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True, choices=["LISTA_RECH", "LISTA_DEFECTOS"])
    args = parser.parse_args()
    marker()
    _, _, base = settings()
    print(canonical(recover_initial(replace(base, family=args.family))))


if __name__ == "__main__":
    main()
