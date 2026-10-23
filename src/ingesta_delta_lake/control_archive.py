"""Bounded immutable JSONL audit archives with publication before acknowledgement."""

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .common import IntegrityError, canonical
from .compact_control import sync_directory
from .retention import unpack


class Archive:
    def __init__(self, root, name):
        root.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=".archive-", dir=root)
        self.temporary, self.root, self.name = Path(path), root, name
        self.raw = os.fdopen(fd, "wb")
        self.stream = gzip.GzipFile(fileobj=self.raw, mode="wb", mtime=0)
        self.fingerprint, self.count = hashlib.sha256(), 0

    def append(self, value):
        data = (canonical(value) + "\n").encode("utf-8")
        self.stream.write(data)
        self.fingerprint.update(data)
        self.count += 1

    def finish(self):
        self.stream.close()
        self.raw.flush()
        os.fsync(self.raw.fileno())
        self.raw.close()
        sha = self.fingerprint.hexdigest()
        target = self.root / (self.name + "-" + sha + ".jsonl.gz")
        actual, count = hashlib.sha256(), 0
        with gzip.open(self.temporary, "rb") as stream:
            for row in stream:
                actual.update(row)
                count += 1
        if count != self.count or actual.hexdigest() != sha:
            raise IntegrityError("Audit archive verification differs")
        os.replace(self.temporary, target)
        sync_directory(self.root)
        return {"file": target.name, "rows": count, "sha256": sha}

    def abort(self):
        self.stream.close()
        self.raw.close()
        self.temporary.unlink(missing_ok=True)


def logical(row):
    return [unpack(value) if isinstance(value, bytes) else value for value in row]


def read(root, receipt):
    name = receipt["file"]
    if Path(name).name != name or not name.endswith(".jsonl.gz"):
        raise IntegrityError("Invalid audit archive reference")
    path = root / name
    if path.resolve().parent != root.resolve() or path.is_symlink():
        raise IntegrityError("Audit archive escapes its control")
    fingerprint, count = hashlib.sha256(), 0
    with gzip.open(path, "rb") as stream:
        for row in stream:
            fingerprint.update(row)
            count += 1
            yield json.loads(row)
    if count != receipt["rows"] or fingerprint.hexdigest() != receipt["sha256"]:
        raise IntegrityError("Audit archive checksum/count differs")
