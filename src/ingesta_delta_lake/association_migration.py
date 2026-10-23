"""Resumable, lossless conversion of legacy association files while ingestion runs.

Only immutable artifact files change. SQLite, Delta and raw CSV remain untouched.
An original is removed only after its exact payload has a durable replacement
and a durable redirect, so historical receipts keep their existing identities.
"""

import gzip
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import portalocker

from . import association_cas as cas
from .common import IntegrityError, canonical, now


def references(value):
    if isinstance(value, dict):
        if {"path", "sha256", "payload_hash"} <= value.keys():
            yield value
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def descriptors(config):
    """One publication at a time; no business rows or large controls in RAM."""
    result = {}
    with closing(sqlite3.connect(config.control_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        for (raw,) in db.execute("SELECT payload_json FROM publications"):
            for item in references(json.loads(raw)):
                if item.get("format") == cas.FORMAT:
                    continue
                previous = result.setdefault(item["path"], item)
                if previous != item:
                    raise IntegrityError("Contradictory association artifact reference")
        # An unconfirmed publication must remain recoverable as well.
        from .compact_control import WORK_KEY, load
        from .retention import payload

        cid = dict(db.execute("SELECT key,value FROM settings"))["control_id"]
        for (raw,) in db.execute(
            "SELECT prepared_json FROM batches WHERE state IN "
            "('PREPARED','WRITING','UNKNOWN','BLOCKED')"
        ):
            document = payload(raw)
            if WORK_KEY in document:
                document = load(config.control_root / "work" / config.family / cid, document)
            for item in references(document):
                if item.get("format") != cas.FORMAT:
                    prior = result.setdefault(item["path"], item)
                    if prior != item:
                        raise IntegrityError("Conflicting pending artifact reference")
    return result


class HashingReader:
    def __init__(self, stream):
        self.stream, self.hash = stream, hashlib.sha256()

    def read(self, size=-1):
        value = self.stream.read(size)
        self.hash.update(value)
        return value


def convert(config, original, verified):
    root = config.control_root / "association-artifacts"
    path = Path(original["path"])
    if (
        path.parent.resolve() != root.resolve()
        or path.is_symlink()
        or not re.fullmatch("[a-f0-9]{64}\\.json\\.gz", path.name)
        or path.name != original["sha256"] + ".json.gz"
    ):
        raise IntegrityError("Legacy association path is outside the conversion scope")
    redirect = root / "redirects" / (original["sha256"] + ".json")
    if redirect.exists():
        replacement = cas.resolve(config, original)
        if replacement == original:
            raise IntegrityError("Legacy redirect was not selected")
        cas.verify_shared(config, replacement, verified)
        if path.exists():
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != original["sha256"]:
                    raise IntegrityError("Original changed after association conversion")
            path.unlink()
            cas.sync(root)
        return {"status": "ALREADY_CONVERTED", "original": original, "replacement": replacement}
    before = path.stat()
    with path.open("rb") as source:
        reader = HashingReader(source)
        with gzip.GzipFile(fileobj=reader, mode="rb") as unpacked:
            replacement = cas.write(
                root, iter(lambda: unpacked.read(1024 * 1024), b""), verified=verified
            )
    after = path.stat()
    if (
        reader.hash.hexdigest() != original["sha256"]
        or replacement["payload_hash"] != original["payload_hash"]
        or (before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise IntegrityError("Legacy association checksum changed; original retained")
    cas.verify_shared(config, replacement, verified)
    cas.publish(redirect, canonical({"original": original, "replacement": replacement}).encode())
    path.unlink()
    cas.sync(root)
    return {
        "status": "CONVERTED",
        "original": original,
        "replacement": replacement,
        "removed_bytes": before.st_size,
    }


def inventory(root):
    result = {}
    for kind, pattern in (
        ("legacy", "*.json.gz"),
        ("objects", "objects/*/*.gz"),
        ("manifests", "manifests/*.json.gz"),
        ("redirects", "redirects/*.json"),
    ):
        paths = [p for p in root.glob(pattern) if p.is_file() and not p.is_symlink()]
        result[kind] = {"files": len(paths), "bytes": sum(p.stat().st_size for p in paths)}
    return result


def execute(config, *, limit=32):
    """An interrupted file retains its original; confirmed files are never repeated."""
    if config.family != "ASIGNACION_PRODUCCION_RAMAL" or not 1 <= limit <= 2048:
        raise IntegrityError("Invalid association conversion scope or batch size")
    config.validate()
    root = config.control_root / "association-artifacts"
    with portalocker.Lock(str(root / ".migration.lock"), mode="a", timeout=0):
        items = descriptors(config)
        verified = {}
        for path, item in items.items():
            if not Path(path).exists():
                replacement = cas.resolve(config, item)
                if replacement == item:
                    raise IntegrityError("Missing historical artifact without a verified redirect")
                cas.verify_shared(config, replacement, verified)
        pending = [v for p, v in sorted(items.items()) if Path(p).is_file()]
        report = {
            "status": "CONVERTING",
            "before": inventory(root),
            "converted": 0,
            "remaining": len(pending),
            "reference_count": len(items),
        }
        # Prefer the most recent publication for immediate operational recovery.
        for item in sorted(pending, key=lambda v: Path(v["path"]).stat().st_mtime, reverse=True)[
            :limit
        ]:
            event = convert(config, item, verified)
            with (root / "migration.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(canonical({"at": now(), **event}) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            report["converted"] += 1
            report["remaining"] -= 1
            print(
                canonical({"event": "ASSOCIATION_CONVERTED", **report}),
                file=__import__("sys").stderr,
                flush=True,
            )
        report.update(
            status="COMPLETE" if not report["remaining"] else "MORE_PENDING", after=inventory(root)
        )
        return report
