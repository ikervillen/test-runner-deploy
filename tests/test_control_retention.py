import json
import sqlite3
from contextlib import closing

import pytest

from ingesta_delta_lake.common import IntegrityError, canonical, digest
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.maintenance import replace_control
from ingesta_delta_lake.retention import compact_batch, pack, payload, unpack
from ingesta_delta_lake.service import run


def large_payload():
    return {
        "inserts": [
            {
                "id_modelo_fenix": i,
                "codigo_modelo_botella": f"{i}/242",
                "evidence": "repeated verified evidence " * 50,
            }
            for i in range(1000)
        ]
    }


def test_lossless_compression_and_corrupt_checksum():
    raw = canonical(large_payload())
    compressed = pack(raw)
    assert len(compressed) < len(raw) / 20
    assert unpack(compressed) == raw
    assert payload(compressed) == json.loads(raw)
    assert pack('{"small":1}') == '{"small":1}'
    with pytest.raises(IntegrityError):
        unpack(compressed[:-2])
    with pytest.raises(IntegrityError):
        unpack(b"unknown binary")


def test_pending_is_untouched_and_confirmed_keys_are_preserved(config, source):
    run(config)
    with closing(Control(config)) as control:
        value = large_payload()
        value["inserts"][0]["evidence"] *= 10
        control.prepare("pending", "pending-operation", value)
        compact_batch(control.db, "pending")
        assert isinstance(control.batch("pending")["prepared_json"], str)
        control.prepare("done", "confirmed-operation", value)
        control.confirm("done", 0)
        compact_batch(control.db, "done")
        assert isinstance(control.batch("done")["prepared_json"], bytes)
        assert control.prepared(control.batch("done")) == value
        intent = control.db.execute(
            "SELECT after_json FROM row_intents "
            "WHERE operation_id='confirmed-operation' AND id_modelo_fenix=0"
        ).fetchone()[0]
        assert isinstance(intent, bytes) and payload(intent) == value["inserts"][0]
        assert (
            control.db.execute(
                "SELECT count(*) FROM row_intents WHERE operation_id=?", ("confirmed-operation",)
            ).fetchone()[0]
            == 1000
        )
        assert len(control.pending()) == 1


def test_offline_replacement_preserves_identity_pending_queues_and_evidence(config, source):
    run(config)
    with closing(Control(config)) as control:
        identity = control.control_id
        value = large_payload()
        control.prepare("completed", "op-completed", value)
        control.confirm("completed", 0)
        control.prepare("pending", "op-pending", value)
        control.mark("pending", "UNKNOWN")
        control.rejected(
            "issue-batch",
            {},
            [{"reason": "CONFLICT", "evidence": {"raw": [1, 2]}}],
            config.plant,
            create_batch=False,
        )
        before = control.db.execute("SELECT issue_id,payload_json FROM issues").fetchall()
    report = replace_control(config, apply=True)
    assert report["status"] == "COMPACTED" and report["pending"] == 1
    assert report["bytes_after"] < report["bytes_before"] * 0.8
    assert report["tables"]["settings"]["rows"] >= 3
    assert report["backup_removed"] is False
    with closing(Control(config)) as control:
        assert control.control_id == identity
        assert control.prepared(control.batch("completed")) == value
        assert control.prepared(control.pending()[0]) == value
        assert list(map(tuple, before)) == list(
            map(tuple, control.db.execute("SELECT issue_id,payload_json FROM issues"))
        )
        assert control.known_captures()
    with pytest.raises(IntegrityError, match="rollback"):
        replace_control(config, apply=True)


def test_migration_cannot_replace_active_worker(config, source):
    import portalocker

    run(config)
    before = digest(config.control_path.read_bytes().hex())
    with portalocker.Lock(str(config.silver_root / ".runtime.lock"), mode="a", timeout=0):
        with pytest.raises(portalocker.exceptions.LockException):
            replace_control(config, apply=True)
    assert digest(config.control_path.read_bytes().hex()) == before


def test_inventory_is_read_only(config, source):
    run(config)
    assert replace_control(config)["status"] == "INVENTORY"
    with sqlite3.connect(config.control_path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_failed_shadow_copy_leaves_original_usable(config, source, monkeypatch):
    import ingesta_delta_lake.maintenance as maintenance

    run(config)
    with closing(Control(config)) as control:
        identity = control.control_id

    def interrupted(*args):
        raise RuntimeError("Interrupted before replacement")

    monkeypatch.setattr(maintenance, "_copy", interrupted)
    with pytest.raises(RuntimeError, match="Interrupted"):
        replace_control(config, apply=True)
    with closing(Control(config)) as control:
        assert control.control_id == identity
    assert not list(config.control_root.glob("*.compact-*"))
    assert not list(config.control_root.glob("*.before-compaction"))


def test_verified_migration_can_release_heavy_backup(config, source):
    run(config)
    report = replace_control(config, apply=True, remove_backup=True)
    assert report["backup_removed"]
    assert not list(config.control_root.glob("*.before-compaction"))
    assert run(config)["status"] == "NO_CHANGE"


def test_container_maintenance_command_uses_explicit_existing_family(
    config, source, monkeypatch, capsys
):
    from ingesta_delta_lake import runtime

    run(config)
    monkeypatch.setattr(runtime, "marker", lambda: {})
    monkeypatch.setattr(runtime, "settings", lambda: ("develop", "instance", config))
    assert runtime.main(["compact-control", "--family", "SIP_MODELO"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "INVENTORY"
    assert (
        runtime.main(["compact-control", "--family", "SIP_MODELO", "--apply", "--remove-backup"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "COMPACTED"
    assert runtime.main(["compact-control"]) == 3
    assert "requires --family" in capsys.readouterr().err
