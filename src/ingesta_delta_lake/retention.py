"""Lossless control-payload compression; identities and pending transactions stay intact.

SQLite keeps its existing schema. Large JSON values become checksummed BLOBs only
in confirmed batches/intents. They remain fully recoverable by ``unpack``. The
current inspection index, queues, issues and evidence are never pruned.
"""

import hashlib
import json
import zlib

from .common import IntegrityError

MAGIC = b"FENIXJSON1\x00"
MIN_BYTES = 8192
MAX_BYTES = 256 * 1024 * 1024


def pack(value):
    if not isinstance(value, str):
        return value
    raw = value.encode("utf-8")
    if len(raw) < MIN_BYTES or len(raw) > MAX_BYTES:
        return value
    encoded = MAGIC + hashlib.sha256(raw).digest() + zlib.compress(raw, level=6)
    return encoded if len(encoded) < len(raw) else value


def unpack(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes) or not value.startswith(MAGIC):
        raise IntegrityError("Unknown compact control payload format")
    offset = len(MAGIC)
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(value[offset + 32 :], MAX_BYTES + 1)
        if (
            len(raw) > MAX_BYTES
            or not decoder.eof
            or decoder.unused_data
            or hashlib.sha256(raw).digest() != value[offset : offset + 32]
        ):
            raise ValueError("Invalid compressed evidence")
        return raw.decode("utf-8")
    except (ValueError, zlib.error, UnicodeError) as exc:
        raise IntegrityError("Corrupt compact control payload") from exc


def payload(value):
    return json.loads(unpack(value))


def compact_batch(db, batch_id):
    """Bound work to one completed batch, after its business commit and queue ack."""
    batch = db.execute(
        "SELECT operation_id,prepared_json FROM batches WHERE batch_id=? "
        "AND confirmed_at IS NOT NULL AND state IN ('COMPLETED','PARTIAL')",
        (batch_id,),
    ).fetchone()
    if not batch:
        return
    with db:
        db.execute(
            "UPDATE batches SET prepared_json=? WHERE batch_id=?", (pack(batch[1]), batch_id)
        )
        # Intent keys, business hashes, states and versions remain queryable. Their
        # redundant large JSON is compressed, never deleted or replaced by a digest.
        tables = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_intents'"
            )
        ]
        for table in tables:
            columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
            if not {"operation_id", "state"} <= columns:
                continue
            fields = sorted(c for c in columns if c.endswith("_json"))
            for field in fields:
                for row in db.execute(
                    f'SELECT rowid,"{field}" FROM "{table}" WHERE operation_id=? '
                    f"AND state='CONFIRMED' AND typeof(\"{field}\")='text' "
                    f'AND length("{field}")>=?',
                    (batch[0], MIN_BYTES),
                ).fetchall():
                    db.execute(
                        f'UPDATE "{table}" SET "{field}"=? WHERE rowid=?', (pack(row[1]), row[0])
                    )
