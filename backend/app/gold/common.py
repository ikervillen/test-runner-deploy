"""Small deterministic identifiers and durable local filesystem operations."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


class IntegrityError(ValueError):
    """An input or commit cannot be proved safe to consume."""


class PendingBasis(IntegrityError):
    """Expected incomplete data, distinguished from an invalid accepted invariant."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=temporal,
    ).encode("utf-8")


def temporal(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Unsupported canonical type: {type(value).__name__}")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def live_epoch():
    if not os.environ.get("FENIX_LIVE_T0"):
        return None
    from ingesta_delta_lake.live_epoch import epoch

    return epoch()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def within(root: Path, relative: str) -> Path:
    """Reject absolute paths, symlink escapes and cross-platform traversal."""
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise IntegrityError("Only portable relative paths are accepted")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise IntegrityError("Path must remain inside the configured store")
    result = (root / path).resolve()
    if not result.is_relative_to(root.resolve()):
        raise IntegrityError("Path escapes the configured store")
    return result


def read_json(path: Path, *, max_bytes: int = 4_000_000) -> dict:
    if path.stat().st_size > max_bytes:
        raise IntegrityError("JSON metadata budget exceeded")
    with path.open("rb") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise IntegrityError("Expected a JSON object")
    return value


def atomic_json(path: Path, value: dict) -> None:
    """Flush a file then replace within its directory; no cross-volume moves."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    fd, temporary = tempfile.mkstemp(prefix=".gold-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        # Preserve interrupted material for inspection under the no-cleanup policy.
        raise


def immutable_json(path: Path, value: dict, *, max_bytes: int = 4_000_000) -> None:
    if path.exists():
        if read_json(path, max_bytes=max_bytes) != json.loads(canonical(value)):
            raise IntegrityError("An immutable artifact already has different contents")
        return
    atomic_json(path, value)
