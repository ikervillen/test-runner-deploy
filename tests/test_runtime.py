import json
import os
import signal
import sqlite3
import time
from pathlib import Path

import portalocker
import pytest

from ingesta_delta_lake import runtime
from ingesta_delta_lake.common import IngestaError, IntegrityError
from ingesta_delta_lake.service import run


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    # Solo sustituye las dos raíces de montaje; se ejercitan Config/Delta reales.
    def mapped(value):
        text = str(value)
        for prefix in ("/input", "/lake"):
            if text == prefix or text.startswith(prefix + "/"):
                return tmp_path / text[1:]
        return Path(value)

    monkeypatch.setattr(runtime, "Path", mapped)
    monkeypatch.setattr(os, "chown", lambda *_: None, raising=False)
    (tmp_path / "input" / "AV32").mkdir(parents=True)
    (tmp_path / "lake").mkdir()
    settings = {
        "plant": "AIALA",
        "sources": {"AV32": "AV32"},
        "encoding": "utf-8",
        "line_by_origin": {"AV32": "AV32"},
        "line_code_by_origin": {"AV32": 32},
    }
    monkeypatch.setenv("INGESTA_CHANNEL", "develop")
    monkeypatch.setenv("INGESTA_INSTANCE", "1" * 32)
    monkeypatch.setenv("INGESTA_ACTIVATION", "a" * 32)
    monkeypatch.setenv("INGESTA_SETTINGS", json.dumps(settings))
    return tmp_path


def test_bootstrap_is_explicit_repeatable_and_does_not_initialize_delta(mounted):
    assert runtime.bootstrap()["status"] == "PREPARED"
    assert runtime.bootstrap()["status"] == "ALREADY_PREPARED"
    assert not list(mounted.rglob("*.sqlite")) and not list(mounted.rglob("_delta_log"))
    assert runtime.check()["status"] == "COMPATIBLE"
    assert all(v is None for v in runtime.check()["versions"].values())


def test_wrong_environment_or_nonempty_unknown_volume_is_never_adopted(mounted, monkeypatch):
    (mounted / "lake" / "unknown.txt").write_text("existing")
    with pytest.raises(IntegrityError, match="no vacío"):
        runtime.bootstrap()
    (mounted / "lake" / "unknown.txt").unlink()
    runtime.bootstrap()
    monkeypatch.setenv("INGESTA_CHANNEL", "main")
    with pytest.raises(IntegrityError, match="otro entorno"):
        runtime.check()


def test_check_validates_accepted_table_without_writes_and_rejects_pending(mounted):
    runtime.bootstrap()
    _, _, config = runtime.settings()
    (config.sources[0][1] / "SIP_MODELO.csv").write_text("1;3081/360;1;\n")
    run(config)
    before = {str(p): p.read_bytes() for p in config.silver_root.rglob("*") if p.is_file()}
    assert runtime.check()["versions"]["SIP_MODELO"] == 0
    assert before == {str(p): p.read_bytes() for p in config.silver_root.rglob("*") if p.is_file()}
    with sqlite3.connect(config.control_path) as db:
        db.execute("UPDATE batches SET state='UNKNOWN'")
    db.close()
    with pytest.raises(IntegrityError, match="Recuperar"):
        runtime.check()
    assert runtime.check(allow_pending=True)["pending"][0]["state"] == "UNKNOWN"


def test_check_rejects_incompatible_existing_binding(mounted):
    runtime.bootstrap()
    _, _, config = runtime.settings()
    (config.sources[0][1] / "SIP_MODELO.csv").write_text("1;3081/360;1;\n")
    run(config)
    with sqlite3.connect(config.control_path) as db:
        db.execute("UPDATE settings SET value='{}' WHERE key='binding'")
    db.close()
    with pytest.raises(IntegrityError, match="incompatibles"):
        runtime.check()


def test_health_activation_and_stale_heartbeat_are_separate_from_quality(mounted):
    runtime.bootstrap()
    token, status, activation = runtime.token_paths()
    runtime.atomic_json(status, {"token": token, "phase": "READY"})
    assert not activation.exists()
    assert runtime.activate()["status"] == "ACTIVATED"
    assert json.loads(activation.read_text()) == {"token": token}
    runtime.atomic_json(status, {"token": token, "phase": "RUNNING"})
    assert runtime.health()["phase"] == "RUNNING"
    os.utime(status, (time.time() - 100, time.time() - 100))
    with pytest.raises(IntegrityError, match="atrasado"):
        runtime.health()


def test_sigterm_finishes_current_unit_and_never_starts_next_one(mounted, monkeypatch):
    from ingesta_delta_lake import service

    runtime.bootstrap()
    token, _, activation = runtime.token_paths()
    runtime.atomic_json(activation, {"token": token})
    callbacks, processed = {}, []
    monkeypatch.setattr(
        signal, "signal", lambda number, callback: callbacks.update({number: callback})
    )

    def unit(config, **kwargs):
        assert kwargs == {"runtime_owner": True}
        processed.append(config.family)
        callbacks[signal.SIGTERM]()
        return {"status": "NO_CHANGE"}

    monkeypatch.setattr(service, "run", unit)
    assert runtime.worker(10)["status"] == "STOPPED"
    assert processed == ["SIP_MODELO"]
    assert len(list((mounted / "lake/logs").glob("*.jsonl"))) == 1
    with pytest.raises(IntegrityError, match="no preparado"):
        runtime.health()


def test_manual_ingest_cannot_overlap_whole_worker(config, source):
    config.silver_root.mkdir()
    with portalocker.Lock(str(config.silver_root / ".runtime.lock"), mode="a", timeout=0):
        with pytest.raises(IngestaError, match="otro escritor"):
            run(config)
        # El worker que ya posee ese bloqueo sigue usando el bloqueo por unidad.
        assert run(config, runtime_owner=True)["status"] == "APPLIED"


def test_image_selftest_runs_real_delta_recovery_without_test_dependencies():
    from ingesta_delta_lake.runtime_selftest import self_test

    assert self_test()["status"] == "PASSED"


def test_runtime_cli_describe_and_report_failure(mounted, capsys):
    assert runtime.main(["describe"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert len(value["contracts"]) == 13
    assert runtime.main(["check"]) == 3
    assert json.loads(capsys.readouterr().err)["status"] == "FAILED"


def test_preflight_never_ignores_wal_and_owner_reads_it_before_recovery(tmp_path):
    path = tmp_path / "source.sqlite"
    with sqlite3.connect(path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE sample(value TEXT)")
        writer.execute("INSERT INTO sample VALUES('confirmed')")
        writer.commit()
        files = {str(p): p.read_bytes() for p in tmp_path.iterdir() if not p.name.endswith("-shm")}
        with pytest.raises(IntegrityError, match="Recuperar WAL"):
            with runtime.control_read(path):
                pytest.fail("No debe leer una versión que omita WAL")
        with runtime.control_read(path, allow_pending=True) as reader:
            assert reader.execute("SELECT value FROM sample").fetchone()[0] == "confirmed"
        assert files == {
            str(p): p.read_bytes() for p in tmp_path.iterdir() if not p.name.endswith("-shm")
        }


@pytest.mark.parametrize(
    "key,value",
    [
        ("INGESTA_CHANNEL", "development"),
        ("INGESTA_INSTANCE", "short"),
        ("INGESTA_SETTINGS", '{"plant":"AIALA"}'),
    ],
)
def test_runtime_settings_reject_invalid_or_unknown_input(mounted, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(IngestaError):
        runtime.settings()
