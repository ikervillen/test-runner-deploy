import json
from dataclasses import replace

import pytest

from ingesta_delta_lake import supervision_export as module
from ingesta_delta_lake.common import digest
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.service import run


def test_optional_export_is_committed_and_replay_safe(config, source, monkeypatch):
    run(config)
    directory = config.control_root / "supervision"
    assert not directory.exists()
    monkeypatch.setenv("INGESTA_SUPERVISION_EXPORT", "1")
    monkeypatch.setenv("INGESTA_CHANNEL", "develop")
    run(config)
    path = directory / "control.sqlite.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["sha256"] == digest({k: v for k, v in value.items() if k != "sha256"})
    assert value["environment"] == "develop"
    assert value["publication"]["delta_version"] == 0
    assert value["issues"] == [] and value["complete"]
    stamp = path.stat().st_mtime_ns
    run(config)
    assert path.stat().st_mtime_ns == stamp
    source.write_text("754;DIFFERENT;1;\n", encoding="utf-8")
    monkeypatch.setattr(module, "INTERVAL", 0)
    run(config)
    conflict = json.loads(path.read_text(encoding="utf-8"))
    assert conflict["issues"] and conflict["publication"] == value["publication"]
    assert len(list((config.table_path / "_delta_log").glob("*.json"))) == 1


@pytest.mark.parametrize("failure", ["budget", "replace", "publication", "cleanup"])
def test_failed_export_preserves_file_and_ingestion(config, source, monkeypatch, failure):
    monkeypatch.setenv("INGESTA_SUPERVISION_EXPORT", "1")
    run(config)
    path = config.control_root / "supervision/control.sqlite.json"
    prior = path.read_bytes()
    monkeypatch.setattr(module, "INTERVAL", 0)
    if failure == "budget":
        monkeypatch.setattr(module, "MAX_BYTES", 1)
    elif failure == "replace":
        monkeypatch.setattr(module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError()))
    elif failure == "publication":
        monkeypatch.setattr(module, "_publication", lambda *_: (_ for _ in ()).throw(ValueError()))
    else:
        monkeypatch.setattr(module, "MAX_BYTES", 1)
        monkeypatch.setattr(type(path), "unlink", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert run(config)["status"] != "FAILED"
    assert path.read_bytes() == prior


def test_no_publication_and_bounded_evidence(config, monkeypatch):
    monkeypatch.setenv("INGESTA_SUPERVISION_EXPORT", "1")
    config.control_root.mkdir()
    control = Control(config)
    try:
        control.db.execute(
            "INSERT INTO issues VALUES('a','CONFLICT','{}','2026-10-22','PENDING_REVIEW')"
        )
        control.db.executemany(
            "INSERT INTO issue_evidence VALUES('a',?,?,?)",
            [(str(i), str(i), "{}") for i in range(3)],
        )
        control.db.commit()
        monkeypatch.setattr(module, "MAX_EVIDENCE", 1)
        control.finish_attempt(control.start_attempt(), {"status": "NO_CHANGES"})
        path = config.control_root / "supervision/control.sqlite.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        assert value["publication"] is None
        assert value["issues"][0]["evidence_count"] == 3
        assert value["issues"][0]["evidence_truncated"]
        monkeypatch.setattr(module, "INTERVAL", 0)
        monkeypatch.setattr(module, "MAX_ISSUES", 0)
        module.export(control, replace(config), {"status": "NO_CHANGES"})
        assert json.loads(path.read_text(encoding="utf-8")) == value
        assert not control.db.in_transaction
    finally:
        control.close()
