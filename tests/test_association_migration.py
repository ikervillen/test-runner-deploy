import gzip
import hashlib
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from ingesta_delta_lake import association_cas as cas
from ingesta_delta_lake.association_migration import execute
from ingesta_delta_lake.association_storage import read_artifact
from ingesta_delta_lake.common import IntegrityError, canonical


def legacy(config, values):
    cfg = replace(
        config,
        family="ASIGNACION_PRODUCCION_RAMAL",
        timestamp_policy="local_naive_test_v1",
        line_by_origin=(("AV21", "AV21"),),
    )
    root = cfg.control_root / "association-artifacts"
    root.mkdir(parents=True)
    descriptors = []
    for value in values:
        raw = canonical(value).encode()
        data = gzip.compress(raw, compresslevel=6, mtime=0)
        sha = hashlib.sha256(data).hexdigest()
        path = root / (sha + ".json.gz")
        path.write_bytes(data)
        descriptors.append(
            {"path": str(path), "sha256": sha, "payload_hash": hashlib.sha256(raw).hexdigest()}
        )
    with closing(sqlite3.connect(cfg.control_path)) as db, db:
        db.executescript(
            "CREATE TABLE publications(no INTEGER,payload_json TEXT);"
            "CREATE TABLE settings(key TEXT,value TEXT);"
            "CREATE TABLE batches(state TEXT,prepared_json TEXT);"
        )
        db.execute("INSERT INTO settings VALUES('control_id','owner')")
        for i, descriptor in enumerate(descriptors):
            db.execute(
                "INSERT INTO publications VALUES(?,?)",
                (i, canonical({"artifacts": {"bundle": descriptor}})),
            )
    return cfg, descriptors


def test_converts_in_bounded_batches_and_preserves_every_historical_reference(config):
    rows = [{"i": i, "hash": hashlib.sha256(str(i).encode()).hexdigest()} for i in range(5000)]
    cfg, descriptors = legacy(config, [rows, rows + [{"i": -1}], rows + [{"i": -2}]])
    original_db = cfg.control_path.read_bytes()
    first = execute(cfg, limit=1)
    assert first["converted"] == 1 and first["remaining"] == 2
    second = execute(cfg, limit=2)
    assert second["status"] == "COMPLETE" and second["after"]["legacy"]["files"] == 0
    assert cfg.control_path.read_bytes() == original_db
    assert read_artifact(cfg, descriptors[0]) == rows
    assert read_artifact(cfg, descriptors[1]) == rows + [{"i": -1}]
    assert read_artifact(cfg, descriptors[2]) == rows + [{"i": -2}]
    replay = execute(cfg)
    assert replay["converted"] == 0 and replay["status"] == "COMPLETE"
    assert sum(v["bytes"] for v in second["after"].values()) < first["before"]["legacy"]["bytes"]


def test_crash_after_redirect_keeps_readable_history_and_resumes_without_reencoding(
    config, monkeypatch
):
    cfg, descriptors = legacy(config, [{"rows": list(range(10000))}])
    original = Path(descriptors[0]["path"])
    unlink = Path.unlink

    def interrupted(path, *args, **kwargs):
        if path == original:
            raise OSError("interrupted before retirement")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        execute(cfg)
    assert original.exists()
    assert read_artifact(cfg, descriptors[0]) == {"rows": list(range(10000))}
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(cas, "write", lambda *_a, **_k: pytest.fail("Already converted"))
    assert execute(cfg)["status"] == "COMPLETE"
    assert not original.exists()


def test_wrong_source_payload_hash_never_removes_original(config):
    cfg, descriptors = legacy(config, [{"a": 1}])
    bad = {**descriptors[0], "payload_hash": "0" * 64}
    with closing(sqlite3.connect(cfg.control_path)) as db, db:
        db.execute("UPDATE publications SET payload_json=?", (canonical({"artifacts": [bad]}),))
    with pytest.raises(IntegrityError, match="checksum"):
        execute(cfg)
    assert Path(descriptors[0]["path"]).exists()
    assert not list((cfg.control_root / "association-artifacts" / "redirects").glob("*"))


def test_missing_old_evidence_cannot_be_reported_as_complete(config):
    cfg, descriptors = legacy(config, [{"a": 1}])
    Path(descriptors[0]["path"]).unlink()
    with pytest.raises(IntegrityError, match="Missing historical"):
        execute(cfg)


def test_conflicting_reference_and_escaped_path_are_rejected(config):
    cfg, descriptors = legacy(config, [{"a": 1}])
    other = {**descriptors[0], "payload_hash": "0" * 64}
    with closing(sqlite3.connect(cfg.control_path)) as db, db:
        db.execute("INSERT INTO publications VALUES(2,?)", (canonical([other]),))
    with pytest.raises(IntegrityError, match="Contradictory"):
        execute(cfg)
