import gzip
import hashlib
import json
import tracemalloc
from pathlib import Path

import pytest

from ingesta_delta_lake.association_storage import artifact, read_artifact, verify_artifacts
from ingesta_delta_lake.common import IntegrityError, canonical, digest


def test_streamed_artifact_has_exact_canonical_payload_and_deterministic_identity(config):
    value = {"texto": "Mañana · Σ", "rows": [{"b": None, "a": [2, 0.25, True]}] * 1200}
    descriptor = artifact(config, value)
    assert descriptor == artifact(config, value)
    raw = gzip.decompress(Path(descriptor["path"]).read_bytes())
    assert raw == canonical(value).encode("utf-8")
    assert descriptor["payload_hash"] == digest(value)
    assert read_artifact(config, descriptor) == value
    assert not list(Path(descriptor["path"]).parent.glob("*.tmp"))


def test_old_gzip_artifact_remains_readable(config):
    value = {"old": ["Aiala", 1]}
    raw = canonical(value).encode("utf-8")
    data = gzip.compress(raw, mtime=0)
    config.control_root.mkdir()
    path = config.control_root / "old.json.gz"
    path.write_bytes(data)
    descriptor = {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "payload_hash": hashlib.sha256(raw).hexdigest(),
    }
    assert read_artifact(config, descriptor) == value


def test_verification_never_deserializes_or_reads_entire_file(config, monkeypatch):
    descriptor = artifact(config, {"rows": list(range(20000))})

    def forbidden(*_, **__):
        raise AssertionError("Whole artifact materialized")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(json, "loads", forbidden)
    monkeypatch.setattr(json, "load", forbidden)
    verify_artifacts(config, {"artifacts": {"bundle": descriptor}})


def test_streamed_serialization_memory_is_independent_of_whole_payload(config):
    payload = [{"i": i, "text": "x" * 250} for i in range(12000)]
    tracemalloc.start()
    try:
        descriptor = artifact(config, payload)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024**2
    assert read_artifact(config, descriptor) == payload


def test_existing_corruption_or_wrong_payload_hash_is_never_accepted(config):
    descriptor = artifact(config, {"a": 1})
    with pytest.raises(IntegrityError, match="Payload"):
        verify_artifacts(
            config, {"artifacts": {"bundle": {**descriptor, "payload_hash": "0" * 64}}}
        )
    Path(descriptor["path"]).write_bytes(b"corrupt")
    with pytest.raises(IntegrityError, match="inmutable"):
        artifact(config, {"a": 1})


def test_failure_before_publication_removes_partial_temp(config, monkeypatch):
    import os

    def fail(*_):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(OSError, match="disk failure"):
        artifact(config, {"rows": [1, 2]})
    assert not list((config.control_root / "association-artifacts").iterdir())
