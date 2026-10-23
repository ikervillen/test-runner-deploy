"""Format 3: recoverable preparation by reference, compact verified receipts.

Only the owning writer calls these functions. Artifact publication precedes the
SQLite reference; a crash can leave an orphan, never a reference to partial data.
"""

import gzip
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

from .common import IntegrityError, canonical, now

FORMAT = 3
WORK_KEY = "$control_work"
RECEIPT_KEY = "$control_receipt"
MAX_DOCUMENT = 256 * 1024 * 1024


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def publish(root, document):
    """Stream canonical JSON to a checksummed compressed immutable artifact."""
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".prepare-", dir=root)
    fingerprint, size = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as encoded:
                for token in json.JSONEncoder(
                    ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).iterencode(document):
                    data = token.encode("utf-8")
                    size += len(data)
                    if size > MAX_DOCUMENT:
                        raise IntegrityError("Prepared artifact exceeds recovery budget")
                    fingerprint.update(data)
                    encoded.write(data)
            raw.flush()
            os.fsync(raw.fileno())
        sha = fingerprint.hexdigest()
        path = root / (sha + ".json.gz")
        if path.exists():
            load(root, {WORK_KEY: sha})
        else:
            os.replace(temporary, path)
            sync_directory(root)
        return {WORK_KEY: sha, "format": FORMAT, "bytes": size}
    finally:
        Path(temporary).unlink(missing_ok=True)


def artifact(root, reference):
    sha = reference.get(WORK_KEY)
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise IntegrityError("Invalid prepared artifact identity")
    path = root / (sha + ".json.gz")
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise IntegrityError("Prepared artifact escapes its control")
    return path


def load(root, reference):
    path = artifact(root, reference)
    try:
        with gzip.open(path, "rb") as stream:
            data = stream.read(MAX_DOCUMENT + 1)
        if len(data) > MAX_DOCUMENT or hashlib.sha256(data).hexdigest() != reference[WORK_KEY]:
            raise IntegrityError("Prepared artifact checksum/budget differs")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise IntegrityError("Prepared artifact is not an object")
        return result
    except (OSError, EOFError, ValueError) as exc:
        raise IntegrityError("Prepared artifact missing or corrupt") from exc


def summary(document, *, prepared_hash=None):
    """Retain scalar facts and counters, never business rows or group snapshots."""
    result = {
        k: v for k, v in document.items() if v is None or isinstance(v, str | int | float | bool)
    }
    for key in ("stats", "decision_stats"):
        if isinstance(document.get(key), dict):
            result[key] = {
                k: v
                for k, v in document[key].items()
                if v is None or isinstance(v, str | int | float | bool)
            }
    result.update(
        {
            RECEIPT_KEY: FORMAT,
            "prepared_sha256": prepared_hash,
            "insert_count": len(document.get("inserts", [])),
            "update_count": len(document.get("updates", [])),
            "input_versions": [
                {"origin": s["origin"], "sha256": s["sha256"]}
                for s in document.get("snapshots", [])
            ],
        }
    )
    return result


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS compact_receipts(
          batch_id TEXT PRIMARY KEY REFERENCES batches(batch_id),
          verified_at TEXT NOT NULL, payload_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS artifact_cleanup(
          sha256 TEXT PRIMARY KEY, eligible_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS prepared_artifacts(
          batch_id TEXT PRIMARY KEY REFERENCES batches(batch_id), sha256 TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS prepared_artifacts_sha ON prepared_artifacts(sha256);
    """)


def finalize(control, batch_id):
    """After queue acknowledgement, atomically retire confirmed recovery detail."""
    db = control.db
    batch = control.batch(batch_id)
    if not batch or not batch["confirmed_at"] or batch["state"] not in {"COMPLETED", "PARTIAL"}:
        return False
    if db.execute("SELECT 1 FROM compact_receipts WHERE batch_id=?", (batch_id,)).fetchone():
        return False
    document = control.prepared(batch)
    from .retention import payload

    reference = payload(batch["prepared_json"])
    if WORK_KEY not in reference:
        # Legacy documents need the explicit archival migration, not silent pruning.
        return False
    receipt = summary(document, prepared_hash=reference.get(WORK_KEY))
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    with db:
        db.execute(
            "INSERT INTO compact_receipts VALUES(?,?,?)", (batch_id, now(), canonical(receipt))
        )
        db.execute(
            "UPDATE batches SET prepared_json=? WHERE batch_id=?", (canonical(receipt), batch_id)
        )
        for table in tables:
            if table.endswith("_intents"):
                db.execute(
                    f"DELETE FROM \"{table}\" WHERE operation_id=? AND state='CONFIRMED'",
                    (batch["operation_id"],),
                )
        if "inspection_groups" in tables:
            if control.setting("inspection_index_version") != control.setting("last_version"):
                raise IntegrityError("Cannot retire groups before current index confirmation")
            db.execute("DELETE FROM inspection_groups WHERE batch_id=?", (batch_id,))
        db.execute("DELETE FROM prepared_artifacts WHERE batch_id=?", (batch_id,))
        db.execute(
            "INSERT OR IGNORE INTO artifact_cleanup VALUES(?,?)", (reference[WORK_KEY], now())
        )
    return True


def clean(control, *, limit=32):
    """Only explicitly eligible artifacts; interrupted deletion is safe to repeat."""
    removed = 0
    for (sha,) in control.db.execute(
        "SELECT sha256 FROM artifact_cleanup LIMIT ?", (limit,)
    ).fetchall():
        if control.db.execute(
            "SELECT 1 FROM prepared_artifacts WHERE sha256=? LIMIT 1", (sha,)
        ).fetchone():
            continue
        artifact(control.work_root, {WORK_KEY: sha}).unlink(missing_ok=True)
        sync_directory(control.work_root)
        with control.db:
            control.db.execute("DELETE FROM artifact_cleanup WHERE sha256=?", (sha,))
        removed += 1
    return removed


def clean_orphans(control, *, limit=32):
    """Only owner-scoped, unreferenced artifacts after the recovery grace period."""
    cutoff = time.time() - 24 * 3600
    removed = 0
    for path in control.work_root.glob("*.json.gz"):
        if removed >= limit:
            break
        if path.is_symlink() or path.stat().st_mtime >= cutoff:
            continue
        sha = path.name.removesuffix(".json.gz")
        if not re.fullmatch("[a-f0-9]{64}", sha):
            continue
        if control.db.execute("SELECT 1 FROM prepared_artifacts WHERE sha256=?", (sha,)).fetchone():
            continue
        load(control.work_root, {WORK_KEY: sha})  # Refuse unknown/corrupted material.
        artifact(control.work_root, {WORK_KEY: sha}).unlink()
        removed += 1
    if removed:
        sync_directory(control.work_root)
    return removed


def settle(control, attempt, result):
    """Maintenance failure is observable without undoing a verified business commit."""
    try:
        for (batch,) in control.db.execute(
            "SELECT b.batch_id FROM batches b LEFT JOIN compact_receipts r USING(batch_id) "
            "WHERE r.batch_id IS NULL AND b.confirmed_at IS NOT NULL "
            "AND b.state IN ('COMPLETED','PARTIAL') LIMIT 32"
        ).fetchall():
            finalize(control, batch)
        clean(control)
        from .control_lifecycle import maintain

        maintain(control, attempt, result)
        with control.db:
            control.db.execute("DELETE FROM settings WHERE key='control_maintenance_error'")
    except Exception as exc:
        control.db.rollback()
        warning = {"at": now(), "error_type": type(exc).__name__}
        with control.db:
            control.db.execute(
                "INSERT INTO settings VALUES('control_maintenance_error',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (canonical(warning),),
            )
        result["control_maintenance_warning"] = warning
        logging.getLogger(__name__).warning("Control maintenance deferred: %s", type(exc).__name__)
