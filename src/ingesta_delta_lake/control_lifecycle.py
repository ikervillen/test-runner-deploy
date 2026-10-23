"""Bounded control retention; checkpoints and unresolved evidence never expire."""

import json
import os
import time
from datetime import UTC, datetime, timedelta

from .common import IntegrityError, canonical, now
from .control_archive import Archive, read

LIMIT = 128


def days(name, default):
    value = int(os.environ.get("INGESTA_CONTROL_" + name + "_DAYS", default))
    if value < 1:
        raise IntegrityError("Control retention must be at least one day: " + name)
    return value


def root(control, kind):
    return control.config.control_root / "audit" / control.config.family / control.control_id / kind


def archived_receipt(control, batch_id, document):
    # Archive chunks are bounded and fully checked before returning a record.
    values = list(read(root(control, "receipts"), document["$receipt_archive"]))
    matches = [v["receipt"] for v in values if v["batch_id"] == batch_id]
    if len(matches) != 1:
        raise IntegrityError("Archived receipt is missing or duplicated")
    return matches[0]


def maintain(control, attempt, result):
    db = control.db
    if result["status"] in {"NO_INPUTS", "NO_CHANGE"} and not result.get("new_entries", 0):
        # Polling is an observation, not a new business operation. Keep its latest
        # value and counters rather than one unbounded history per empty poll.
        with db:
            db.execute(
                "INSERT INTO settings VALUES('last_poll',?) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value",
                (canonical({"at": now(), "status": result["status"]}),),
            )
            if not db.execute("SELECT 1 FROM receipts WHERE attempt_id=?", (attempt,)).fetchone():
                db.execute("DELETE FROM attempts WHERE attempt_id=?", (attempt,))
    if time.time() - float(control.setting("last_control_retention") or "0") < 3600:
        return
    clock = datetime.now(UTC)
    from .compact_control import clean_orphans

    clean_orphans(control)
    cutoff = (clock - timedelta(days=days("RECEIPT", 90))).isoformat()
    receipts = db.execute(
        "SELECT batch_id,payload_json FROM compact_receipts "
        "WHERE julianday(verified_at)<julianday(?) "
        "AND json_extract(payload_json,'$.\"$receipt_archive\"') IS NULL LIMIT ?",
        (cutoff, LIMIT),
    ).fetchall()
    if receipts:
        archive = Archive(root(control, "receipts"), "receipts")
        try:
            for batch, value in receipts:
                archive.append({"batch_id": batch, "receipt": json.loads(value)})
            reference = canonical({"$receipt_archive": archive.finish()})
            with db:
                for batch, _ in receipts:
                    db.execute(
                        "UPDATE batches SET prepared_json=? WHERE batch_id=?", (reference, batch)
                    )
                    db.execute(
                        "UPDATE compact_receipts SET payload_json=? WHERE batch_id=?",
                        (reference, batch),
                    )
        except BaseException:
            archive.abort()
            raise
    cutoff = (clock - timedelta(days=days("ATTEMPT", 30))).isoformat()
    attempts = db.execute(
        "SELECT * FROM attempts a WHERE julianday(finished_at)<julianday(?) AND status!='RUNNING' "
        "AND NOT EXISTS(SELECT 1 FROM receipts r WHERE r.attempt_id=a.attempt_id) "
        "AND NOT EXISTS(SELECT 1 FROM batches b WHERE b.batch_id=a.batch_id "
        "AND b.state IN ('PREPARED','WRITING','UNKNOWN','BLOCKED')) LIMIT ?",
        (cutoff, LIMIT),
    ).fetchall()
    if attempts:
        archive = Archive(root(control, "attempts"), "attempts")
        try:
            for value in attempts:
                archive.append(list(value))
            receipt = archive.finish()
            newest = max(datetime.fromisoformat(value[3]) for value in attempts)
            receipt["expires_at"] = (newest + timedelta(days=days("ARCHIVE", 365))).isoformat()
            receipt["batch_ids"] = sorted({value[1] for value in attempts if value[1]})
            with db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS attempt_archives("
                    "file TEXT PRIMARY KEY,receipt TEXT NOT NULL)"
                )
                db.execute(
                    "INSERT OR IGNORE INTO attempt_archives VALUES(?,?)",
                    (receipt["file"], canonical(receipt)),
                )
                db.executemany(
                    "DELETE FROM attempts WHERE attempt_id=?", [(r[0],) for r in attempts]
                )
        except BaseException:
            archive.abort()
            raise
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempt_archives'"
    ).fetchone():
        for filename, value in db.execute(
            "SELECT file,receipt FROM attempt_archives LIMIT ?", (LIMIT,)
        ).fetchall():
            receipt = json.loads(value)
            if (
                not receipt.get("expires_at")
                or datetime.fromisoformat(receipt["expires_at"]) > clock
            ):
                continue
            batch_ids = receipt.get("batch_ids", [])
            if any(
                db.execute(
                    "SELECT 1 FROM issue_evidence e JOIN issues i USING(issue_id) "
                    "WHERE e.batch_id=? AND i.status!='RESOLVED' LIMIT 1",
                    (batch,),
                ).fetchone()
                for batch in batch_ids
            ):
                continue
            path = root(control, "attempts") / filename
            if (
                path.name != filename
                or path.resolve().parent != root(control, "attempts").resolve()
                or path.is_symlink()
            ):
                raise IntegrityError("Expired archive escapes its control")
            if path.exists():
                list(read(root(control, "attempts"), receipt))
                path.unlink()
            with db:
                db.execute("DELETE FROM attempt_archives WHERE file=?", (filename,))
    with db:
        db.execute(
            "INSERT INTO settings VALUES('last_control_retention',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(time.time()),),
        )
    # PASSIVE never waits for readers or removes an active WAL by hand.
    db.execute("PRAGMA wal_checkpoint(PASSIVE)")
