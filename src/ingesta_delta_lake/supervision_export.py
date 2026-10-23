"""Optional bounded projection, published by the sole writer after its commit.

No second connection to live WAL files, business-table changes or Delta writes.
An incomplete export never replaces the last complete observation.
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path

from .common import canonical, digest, now

MAX_BYTES = 16_000_000
MAX_PAYLOAD = 256_000
MAX_ISSUES = 20_000
MAX_EVIDENCE = 20
INTERVAL = 60
TIMEOUT = 3


def _payload(text):
    if len(text.encode()) > MAX_PAYLOAD:
        raise ValueError("Supervision payload budget exceeded")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Supervision payload must be an object")
    return value


def _publication(control, config):
    version = control.setting("last_version")
    if version is None or version == "None":
        return None
    version = int(version)
    path = config.table_path / "_delta_log" / f"{version:020d}.json"
    operation = None
    with path.open("rb") as stream:
        for line in stream:
            if len(line) > MAX_BYTES:
                raise ValueError("Delta metadata budget exceeded")
            info = json.loads(line).get("commitInfo")
            if info:
                if info.get("ingesta_control_id") != control.control_id:
                    raise ValueError("Delta control identity differs")
                operation = info.get("ingesta_operation_id")
                break
    # The operation_id UNIQUE index avoids scanning large batches/prepared_json.
    row = control.db.execute(
        "SELECT confirmed_at,delta_version FROM batches WHERE operation_id=?", (operation,)
    ).fetchone()
    if not operation or not row or not row[0] or row[1] != version:
        raise ValueError("Delta publication is not confirmed")
    return {"delta_version": version, "operation_id": operation, "confirmed_at": row[0]}


def export(control, config, result):
    """Best effort operational output; failure cannot change an ingestion outcome."""
    if os.environ.get("INGESTA_SUPERVISION_EXPORT") != "1":
        return
    directory = config.control_root / "supervision"
    path = directory / (config.control_path.name + ".json")
    temporary = directory / ("." + uuid.uuid4().hex + ".tmp")
    try:
        if path.exists() and time.time() - path.stat().st_mtime < INTERVAL:
            return
        deadline = time.monotonic() + TIMEOUT
        control.db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            control.db.execute("BEGIN")
            observed_at = now()
            issues, size = [], 0
            for row in control.db.execute(
                "SELECT issue_id,reason,status,first_seen,"
                "substr(payload_json,1,?),length(CAST(payload_json AS BLOB)) "
                "FROM issues ORDER BY issue_id LIMIT ?",
                (MAX_PAYLOAD, MAX_ISSUES + 1),
            ):
                if len(issues) >= MAX_ISSUES or row[5] > MAX_PAYLOAD:
                    raise ValueError("Supervision issue budget exceeded")
                size += row[5]
                evidence = []
                count = control.db.execute(
                    "SELECT count(*) FROM issue_evidence WHERE issue_id=?", (row[0],)
                ).fetchone()[0]
                for ref in control.db.execute(
                    "SELECT batch_id,evidence_hash,substr(payload_json,1,?),"
                    "length(CAST(payload_json AS BLOB)) FROM issue_evidence "
                    "WHERE issue_id=? ORDER BY batch_id,evidence_hash LIMIT ?",
                    (MAX_PAYLOAD, row[0], MAX_EVIDENCE),
                ):
                    if ref[3] > MAX_PAYLOAD:
                        raise ValueError("Supervision evidence budget exceeded")
                    size += ref[3]
                    evidence.append(
                        {"batch_id": ref[0], "hash": ref[1], "payload": _payload(ref[2])}
                    )
                if size > MAX_BYTES or time.monotonic() > deadline:
                    raise ValueError("Supervision observation budget exceeded")
                payload = _payload(row[4])
                from .review_adapters import offer

                payload["resolution"] = offer(config, payload)
                review_root = os.environ.get("INGESTA_REVIEW_ROOT")
                payload["resolution"]["writer_enabled"] = bool(
                    review_root
                    and (Path(review_root) / "commands").is_dir()
                    and (Path(review_root) / "results").is_dir()
                )
                if len(canonical(payload).encode("utf-8")) > MAX_PAYLOAD:
                    raise ValueError("Supervision comparison payload budget exceeded")
                issues.append(
                    {
                        "source_id": row[0],
                        "reason": row[1],
                        "source_status": row[2],
                        "first_seen": row[3],
                        "payload": payload,
                        "evidence": evidence,
                        "evidence_count": count,
                        "evidence_truncated": count > len(evidence),
                    }
                )
            value = {
                "schema_version": 1,
                "complete": True,
                "environment": os.environ.get("INGESTA_CHANNEL", config.environment),
                "plant": config.plant,
                "family": config.family,
                "control_id": control.control_id,
                "observed_at": observed_at,
                "issues": issues,
                "stats": {
                    "last_attempt": {"finished_at": observed_at, "status": result["status"]},
                    "control_storage": {
                        "payload_format": 3 if control.compact else 2,
                        "retry_state": json.loads(control.setting("retry_state") or "null"),
                        "bytes": config.control_path.stat().st_size,
                        "wal_bytes": (
                            config.control_path.with_name(config.control_path.name + "-wal")
                            .stat()
                            .st_size
                            if config.control_path.with_name(
                                config.control_path.name + "-wal"
                            ).exists()
                            else 0
                        ),
                        "page_count": control.db.execute("PRAGMA page_count").fetchone()[0],
                        "free_pages": control.db.execute("PRAGMA freelist_count").fetchone()[0],
                    },
                },
                "publication": _publication(control, config),
            }
        finally:
            control.db.rollback()  # Only the read transaction; business commit already finished.
            control.db.set_progress_handler(None, 0)
        value["sha256"] = digest(value)
        encoded = canonical(value).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError("Supervision file budget exceeded")
        directory.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception as exc:
        # No payloads or database paths in application logs; preserve prior good file.
        logging.getLogger(__name__).warning(
            "Supervision export unavailable: family=%s error=%s", config.family, type(exc).__name__
        )
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # A cleanup failure must not change the committed ingestion outcome.
