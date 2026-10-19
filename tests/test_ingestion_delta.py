import json
import runpy
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import portalocker
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from ingesta_delta_lake import service
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import IngestaError, IntegrityError
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.delta import SCHEMA, DeltaStore
from ingesta_delta_lake.service import run


def rows(config):
    return DeltaTable(str(config.table_path)).to_pyarrow_table().to_pylist()


def query(config, sql):
    with sqlite3.connect(config.control_root / "control.sqlite") as db:
        return db.execute(sql).fetchall()


def test_initial_load_replay_and_audit(config, source):
    first = run(config)
    assert first["status"] == "APPLIED" and first["inserted_this_attempt"] == 1
    table = DeltaTable(str(config.table_path))
    assert table.to_pyarrow_table().schema == SCHEMA
    files = sorted(p.relative_to(config.silver_root) for p in config.silver_root.rglob("*"))
    again = run(config)
    assert again["status"] == "NO_CHANGE" and again["inserted_this_attempt"] == 0
    assert again["delta_version"] == first["delta_version"] == 0
    assert sorted(p.relative_to(config.silver_root) for p in config.silver_root.rglob("*")) == files
    assert query(config, "SELECT state, COUNT(*) FROM row_intents GROUP BY state") == [
        ("CONFIRMED", 1)
    ]
    assert query(config, "SELECT COUNT(*) FROM attempts") == [(2,)]
    assert query(config, "SELECT COUNT(*) FROM receipts") == [(2,)]


def test_catalog_is_shared_between_origins(config, source):
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "16102026_92028_SIP_MODELO.csv").write_bytes(source.read_bytes())
    result = run(replace(config, sources=(*config.sources, ("AV22", other))))
    assert result["source_files"] == 2 and result["inserted_this_attempt"] == 1
    assert len(rows(config)) == 1


def test_both_delivery_modes_and_missing_rows(config, source):
    run(config)
    source.write_text("800;0001/02A;1;\n", encoding="utf-8")
    changed = run(config)
    assert changed["inserted_this_attempt"] == 1 and changed["delta_version"] == 1
    # Un nombre nuevo con el mismo contenido no crea otra versión Delta.
    (source.parent / "16102026_92028_SIP_MODELO.csv").write_bytes(source.read_bytes())
    renamed = run(config)
    assert renamed["status"] == "NO_CHANGE" and renamed["delta_version"] == 1
    assert len(rows(config)) == 2  # La ausencia del ID 754 en la nueva entrega no lo borra.


def test_versioned_file_can_also_be_modified(config, source):
    dated = source.with_name("16102026_91011_SIP_MODELO.csv")
    source.rename(dated)
    run(config)
    dated.write_text("754;3081/360;1;\n800;CODE;1;\n", encoding="utf-8")
    result = run(config)
    assert result["inserted_this_attempt"] == 1 and len(rows(config)) == 2


def test_ignored_state_never_writes(config, source):
    run(config)
    source.write_text("754;3081/360;2;\n", encoding="utf-8")
    result = run(config)
    assert result["status"] == "NO_CHANGE" and result["delta_version"] == 0
    assert query(config, "SELECT COUNT(*) FROM row_intents") == [(1,)]


def test_conflict_blocks_whole_unit_and_keeps_candidate(config, source):
    run(config)
    source.write_text("754;OTHER;1;\n800;NEW;1;\n", encoding="utf-8")
    assert run(config)["status"] == "BLOCKED"
    assert rows(config) == [{"id_modelo_fenix": 754, "codigo_modelo_botella": "3081/360"}]
    assert DeltaTable(str(config.table_path)).version() == 0
    issue = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert issue["accepted_code"] == "3081/360"
    assert issue["evidence"][0]["raw"] == ["754", "OTHER", "1", ""]
    assert run(config)["status"] == "BLOCKED"
    assert query(config, "SELECT COUNT(*) FROM issues") == [(1,)]
    assert query(config, "SELECT COUNT(*) FROM issue_evidence") == [(1,)]


def test_invalid_initial_file_has_no_delta_side_effect(config, source):
    source.write_text("754;3081/360;1;\n1.5;BAD;1;\n", encoding="utf-8")
    result = run(config)
    assert result["status"] == "BLOCKED"
    assert not config.table_path.exists()
    assert query(config, "SELECT COUNT(*) FROM row_intents") == [(0,)]
    assert query(config, "SELECT reason FROM issues") == [("INVALID_ID",)]


def test_no_inputs_does_not_create_empty_table(config):
    assert run(config)["status"] == "NO_INPUTS"
    assert not config.table_path.exists()


def test_empty_valid_file_does_not_create_empty_delta(config, source):
    source.write_bytes(b"")
    result = run(config)
    assert result["status"] == "NO_CHANGE" and result["delta_version"] is None
    assert not config.table_path.exists()


def test_recover_commit_after_client_failure_without_rewrite(config, source, monkeypatch):
    original = DeltaStore.insert

    def commit_then_fail(self, *args):
        original(self, *args)
        raise OSError("connection lost after commit")

    with monkeypatch.context() as m:
        m.setattr(DeltaStore, "insert", commit_then_fail)
        with pytest.raises(IngestaError):
            run(config)
    assert DeltaTable(str(config.table_path)).version() == 0
    assert query(config, "SELECT state FROM batches") == [("UNKNOWN",)]

    def must_not_write(*_):
        pytest.fail("La recuperación ha intentado repetir un commit confirmado")

    monkeypatch.setattr(DeltaStore, "insert", must_not_write)
    recovered = run(config)
    assert recovered["inserted_this_attempt"] == 0
    assert recovered["recovered_operations"][0]["confirmed_rows"] == 1
    assert not recovered["recovered_operations"][0]["wrote_this_attempt"]
    assert query(config, "SELECT state FROM batches") == [("COMPLETED",)]
    assert query(config, "SELECT state,COUNT(*) FROM row_intents GROUP BY state") == [
        ("CONFIRMED", 1)
    ]


def test_retry_uses_prepared_evidence_even_if_source_is_overwritten(config, source, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(DeltaStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before commit")))
        with pytest.raises(IngestaError):
            run(config)
    source.write_text("800;NEW;1;\n", encoding="utf-8")
    result = run(config)
    assert result["inserted_this_attempt"] == 2  # Recuperación antigua + revisión nueva.
    assert len(rows(config)) == 2
    assert result["delta_version"] == 1
    assert result["recovered_operations"][0]["wrote_this_attempt"]


def test_sqlite_ack_failure_is_reconciled(config, source, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(
            Control, "confirm", lambda *_: (_ for _ in ()).throw(sqlite3.OperationalError("ack"))
        )
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["delta_version"] == 0 and result["inserted_this_attempt"] == 0
    assert query(config, "SELECT state FROM row_intents") == [("CONFIRMED",)]


def test_altered_prepared_evidence_blocks_recovery(config, source, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(DeltaStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before")))
        with pytest.raises(IngestaError):
            run(config)
    prepared = json.loads(query(config, "SELECT prepared_json FROM batches")[0][0])
    Path(prepared["snapshots"][0]["bronze_path"]).write_bytes(b"changed")
    with pytest.raises(IntegrityError):
        run(config)
    assert not config.table_path.exists()
    assert query(config, "SELECT state FROM batches") == [("UNKNOWN",)]


def test_external_delta_commit_is_not_adopted(config, source):
    run(config)
    write_deltalake(
        str(config.table_path),
        pa.Table.from_pylist(
            [{"id_modelo_fenix": 800, "codigo_modelo_botella": "FOREIGN"}], schema=SCHEMA
        ),
        mode="append",
    )
    with pytest.raises(IntegrityError):
        run(config)
    assert DeltaTable(str(config.table_path)).version() == 1


def test_second_control_cannot_take_over_existing_table(config, source):
    run(config)
    with pytest.raises(IntegrityError):
        run(replace(config, control_root=config.control_root.with_name("other-control")))
    assert DeltaTable(str(config.table_path)).version() == 0


def test_control_binding_is_not_reused_for_another_plant(config, source):
    run(config)
    with pytest.raises(IntegrityError):
        run(replace(config, plant="OTHER"))


def test_single_writer_lock(config, source):
    config.silver_root.mkdir(parents=True)
    with portalocker.Lock(str(config.silver_root / ".writer.lock"), mode="a", timeout=0):
        with pytest.raises(IngestaError, match="otro escritor"):
            run(config)
    assert not config.table_path.exists()


def test_cli_requires_valid_configuration(tmp_path, capsys):
    assert main(["ingest", "--config", str(tmp_path / "missing.toml")]) == 3
    assert json.loads(capsys.readouterr().err)["status"] == "FAILED"


def test_cli_success_and_entrypoint(config, source, tmp_path, capsys, monkeypatch):
    values = {"plant": config.plant, "environment": "test"}
    values.update(
        {
            k: str(getattr(config, k))
            for k in ("input_root", "bronze_root", "silver_root", "control_root")
        }
    )
    conf = tmp_path / "config.toml"
    conf.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
        + '\n[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert main(["ingest", "--config", str(conf)]) == 0
    assert json.loads(capsys.readouterr().out)["inserted_this_attempt"] == 1
    monkeypatch.setattr(sys, "argv", ["ingesta_delta_lake", "ingest", "--config", str(conf)])
    with pytest.raises(SystemExit) as ended:
        runpy.run_module("ingesta_delta_lake", run_name="__main__")
    assert ended.value.code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NO_CHANGE"


def test_pending_operation_cannot_switch_transformer_version(config, source, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(DeltaStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before")))
        with pytest.raises(IngestaError):
            run(config)
    with monkeypatch.context() as m:
        m.setattr(service, "__version__", "different-release")
        with pytest.raises(IntegrityError, match="versión"):
            run(config)
    assert not config.table_path.exists()
    assert run(config)["delta_version"] == 0


def test_unmanaged_existing_directory_is_not_converted(config, source):
    config.table_path.mkdir(parents=True)
    unknown = config.table_path / "unmanaged.txt"
    unknown.write_text("existing data", encoding="utf-8")
    with pytest.raises(IntegrityError):
        run(config)
    assert unknown.read_text() == "existing data"
    assert not (config.table_path / "_delta_log").exists()


def test_legacy_model_prepared_payload_and_control_remain_compatible(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(DeltaStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before")))
        with pytest.raises(IngestaError):
            run(config)
    # La entrega 01 guardaba estos mismos datos sin stats/decision_stats agrupados.
    with sqlite3.connect(config.control_path) as db:
        prepared = json.loads(db.execute("SELECT prepared_json FROM batches").fetchone()[0])
        prepared.pop("stats")
        prepared.pop("decision_stats")
        prepared.pop("new_entries", None)
        db.execute("UPDATE batches SET prepared_json=?", (json.dumps(prepared),))
    result = run(config)
    assert result["delta_version"] == 0 and result["inserted_this_attempt"] == 1
    assert run(config)["status"] == "NO_CHANGE"
