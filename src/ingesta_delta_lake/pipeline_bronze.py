"""Bounded readers of a lane's immutable captured-Bronze feed."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .common import IntegrityError, digest
from .pipeline_policy import LANES
from .sources import Snapshot, _read_limited, capture_identity


@dataclass(frozen=True)
class PublishedCapture:
    sequence: int
    input_id: str
    family: str
    snapshot: Snapshot


class BronzeFeed:
    def __init__(self, storage):
        self.storage = storage
        self.root = storage.exchange / "bronze"
        value = json.loads((self.root / "current.json").read_text())
        if value.get("schema_id") != "fenix-bronze-capture-v1" or value.get("lane") not in LANES:
            raise IntegrityError("Invalid Bronze capture feed")
        if type(value.get("sequence")) is not int or value["sequence"] < 1:
            raise IntegrityError("Invalid Bronze publication sequence")
        self.identity, self.lane, self.sequence = (
            value["identity"],
            value["lane"],
            value["sequence"],
        )
        self.last_capture_id = value["capture_id"]

    def captures(self, *, after, limit):
        if type(after) is not int or after < 0 or type(limit) is not int or limit < 1:
            raise IntegrityError("Bronze feed requires an explicit cursor and positive limit")
        for sequence in range(after + 1, min(self.sequence, after + limit) + 1):
            value = json.loads((self.root / "entries" / (str(sequence) + ".json")).read_text())
            payload = {k: v for k, v in value.items() if k != "capture_id"}
            if digest(payload) != value.get("capture_id") or (
                value.get("schema_id") != "fenix-bronze-capture-v1"
                or value.get("identity") != self.identity
                or value.get("lane") != self.lane
                or value.get("sequence") != sequence
                or (sequence == self.sequence and value["capture_id"] != self.last_capture_id)
            ):
                raise IntegrityError("Bronze capture envelope differs from its publication")
            snapshot = Snapshot(**value["snapshot"])
            if capture_identity(value["family"], snapshot) != value["input_id"]:
                raise IntegrityError("Bronze capture identity differs")
            self.path(snapshot)
            yield PublishedCapture(sequence, value["input_id"], value["family"], snapshot)

    def path(self, snapshot):
        path = Path(snapshot.bronze_path)
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.resolve().is_relative_to(self.storage.bronze)
        ):
            raise IntegrityError("Published capture escapes its Bronze store")
        return path

    def read(self, capture, *, max_file_bytes):
        data = _read_limited(self.path(capture.snapshot), max_file_bytes)
        if (
            len(data) != capture.snapshot.size
            or hashlib.sha256(data).hexdigest() != capture.snapshot.sha256
        ):
            raise IntegrityError("Published Bronze bytes differ from their stable capture")
        return data
