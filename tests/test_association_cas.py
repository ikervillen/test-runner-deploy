import gzip
import hashlib
import json
from pathlib import Path

import pytest

from ingesta_delta_lake.association_cas import MAX_BLOCK, blocks, object_path
from ingesta_delta_lake.association_storage import artifact, read_artifact, verify_artifact
from ingesta_delta_lake.common import IntegrityError, canonical


def test_insertion_reuses_historical_blocks_and_keeps_exact_publications(config):
    rows = [{"id": i, "value": hashlib.sha256(str(i).encode()).hexdigest()} for i in range(16000)]
    first = artifact(config, rows)
    root = config.control_root / "association-artifacts"
    original = {p.name for p in (root / "objects").rglob("*.gz")}
    changed = rows[:8000] + [{"id": -1, "value": "new"}] + rows[8000:]
    second = artifact(config, changed)
    after = {p.name for p in (root / "objects").rglob("*.gz")}
    assert len(after - original) < len(original) / 3
    assert read_artifact(config, first) == rows
    assert read_artifact(config, second) == changed
    assert first == artifact(config, rows)


def test_boundaries_are_independent_of_read_sizes_including_large_values():
    raw = canonical(
        {"rows": [{"i": i} for i in range(10000)], "large": "x" * MAX_BLOCK * 2}
    ).encode()
    a = list(blocks([raw]))
    b = list(blocks(raw[i : i + 731] for i in range(0, len(raw), 731)))
    assert a == b
    assert b"".join(a) == raw
    assert all(len(value) <= MAX_BLOCK for value in a)


def test_corrupt_shared_block_is_detected_and_never_silently_replaced(config):
    value = {"rows": list(range(40000))}
    descriptor = artifact(config, value)
    manifest = json.loads(gzip.decompress(Path(descriptor["path"]).read_bytes()))
    path = object_path(config.control_root / "association-artifacts", manifest["chunks"][0])
    path.write_bytes(gzip.compress(b"bad", mtime=0))
    with pytest.raises(IntegrityError, match="block"):
        verify_artifact(config, descriptor)
    with pytest.raises(IntegrityError, match="inmutable"):
        artifact(config, value)


def test_legacy_redirect_preserves_exact_original_descriptor(config):
    from ingesta_delta_lake.association_cas import publish

    value = {"legacy": [1, 2, 3]}
    descriptor = artifact(config, value)
    root = config.control_root / "association-artifacts"
    original = {
        "path": str(root / ("f" * 64 + ".json.gz")),
        "sha256": "f" * 64,
        "payload_hash": descriptor["payload_hash"],
    }
    publish(
        root / "redirects" / (original["sha256"] + ".json"),
        canonical({"original": original, "replacement": descriptor}).encode(),
    )
    assert read_artifact(config, original) == value
    with pytest.raises(IntegrityError, match="identity"):
        read_artifact(config, {**original, "payload_hash": "0" * 64})


def test_unknown_block_id_and_escaped_manifest_rejected(config):
    root = config.control_root / "association-artifacts"
    with pytest.raises(IntegrityError, match="identity"):
        object_path(root, "../escape")
    descriptor = artifact(config, {"x": 1})
    with pytest.raises(IntegrityError, match="escapes"):
        verify_artifact(config, {**descriptor, "path": str(config.control_root / "other")})
