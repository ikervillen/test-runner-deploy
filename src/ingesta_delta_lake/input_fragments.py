"""Deterministic CSV-record fragments of immutable large MIA captures."""

import codecs
import csv
import hashlib
import io
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from .common import IntegrityError, canonical
from .compact_control import sync_directory

TARGET_BYTES = 2 * 1024 * 1024


def fragments(snapshot, encoding, *, target_bytes=TARGET_BYTES):
    source = Path(snapshot.bronze_path)
    with source.open("rb") as raw:
        if hashlib.file_digest(raw, "sha256").hexdigest() != snapshot.sha256:
            raise IntegrityError("Large input capture checksum differs")
    buffer = bytearray()
    encoder = codecs.getincrementalencoder(encoding)()
    destination = source.parent.parent / "fragments" / snapshot.sha256
    destination.mkdir(parents=True, exist_ok=True)

    def publish(data):
        sha = hashlib.sha256(data).hexdigest()
        path = destination / (sha + ".csv")
        fd, temporary = tempfile.mkstemp(prefix=".fragment-", dir=destination)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != sha:
                        raise IntegrityError("Existing input fragment checksum differs")
            else:
                os.replace(temporary, path)
                sync_directory(path.parent)
            return replace(snapshot, sha256=sha, bronze_path=str(path), size=len(data))
        finally:
            Path(temporary).unlink(missing_ok=True)

    with source.open(encoding=encoding, newline="") as stream:
        for record in csv.reader(stream, delimiter=";"):
            text = io.StringIO(newline="")
            csv.writer(text, delimiter=";", lineterminator="\n").writerow(record)
            data = encoder.encode(text.getvalue())
            if len(data) > target_bytes:
                raise IntegrityError("One CSV record exceeds the fragment budget")
            if buffer and len(buffer) + len(data) > target_bytes:
                buffer.extend(encoder.encode("", final=True))
                yield publish(buffer)
                buffer = bytearray()
                encoder = codecs.getincrementalencoder(encoding)()
                data = encoder.encode(text.getvalue())
            buffer.extend(data)
    if buffer:
        buffer.extend(encoder.encode("", final=True))
        yield publish(buffer)


def expand(control, captures, *, max_file_bytes):
    """Publish the complete fragment graph atomically; parent ack waits for children."""
    db = control.db
    parents = {
        (r[0], r[1]) for r in db.execute("SELECT DISTINCT origin,parent_sha FROM input_fragments")
    }
    additions = {}
    for key, snapshot in list(captures.items()):
        if snapshot.size <= max_file_bytes or key in parents:
            continue
        parts = list(
            fragments(
                snapshot, control.config.encoding, target_bytes=min(TARGET_BYTES, max_file_bytes)
            )
        )
        if not parts:
            raise IntegrityError("Large input produced no CSV records")
        with db:
            for part in parts:
                db.execute(
                    "INSERT OR IGNORE INTO input_versions VALUES(?,?,?)",
                    (part.origin, part.sha256, canonical(part.descriptor())),
                )
                db.execute(
                    "INSERT OR IGNORE INTO input_fragments VALUES(?,?,?)",
                    (snapshot.origin, snapshot.sha256, part.sha256),
                )
                additions[(part.origin, part.sha256)] = part
        parents.add(key)
    captures.update(additions)
    return parents
