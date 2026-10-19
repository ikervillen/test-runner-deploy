import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import status
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import IngestaError, IntegrityError
from ingesta_delta_lake.config import load_config
from ingesta_delta_lake.contracts import STATUS_SCHEMA
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.delta import DeltaStore
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot, discover, parse_name


@pytest.fixture
def config(config):
    return replace(config, family="SIP_DEFECTO_STATUS", encoding="cp1252")


@pytest.fixture
def source(config):
    path = config.input_root / "AV21" / "SIP_DEFECTO_STATUS.csv"
    # ID deliberadamente distinto del CODIGO utilizado por LISTA_AR.STATUS.
    path.write_text("11;2;Muestreo IFA;0;MF;1;1;\n", encoding=config.encoding)
    return path


def parsed(tmp_path, text, encoding="cp1252"):
    path = tmp_path / "snapshot.csv"
    data = text.encode("cp1252")
    path.write_bytes(data)
    snapshot = Snapshot(
        "AV21",
        str(path),
        hashlib.sha256(data).hexdigest(),
        str(path),
        len(data),
        0,
        "2026-10-16T09:20:00Z",
        None,
    )
    return status.parse([snapshot], encoding)


def rows(config):
    return DeltaTable(str(config.table_path)).to_pyarrow_table().to_pylist()


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def delta_files(config):
    return {
        str(p.relative_to(config.table_path)): p.read_bytes()
        for p in config.table_path.rglob("*")
        if p.is_file()
    }


def test_six_field_schema_preserves_original_positions_and_labels(config, source):
    source.write_text(
        "11;2;Muestreo IFA;0;MF;1;1;\n"
        "14;4;Delfoz ZC;1;DC;0;0;\n"
        "15;5;Delfoz ZF;1;DF;1;1;\n"
        "18;8;Muestreos ZC;0;MC;0;;\n",
        encoding=config.encoding,
    )
    result = run(config)
    assert result["status"] == "APPLIED" and result["inserted_this_attempt"] == 4
    assert DeltaTable(str(config.table_path)).to_pyarrow_table().schema == STATUS_SCHEMA
    by_code = {r["codigo_status"]: r for r in rows(config)}
    assert by_code[2] == {
        "id_status_fenix": 11,
        "codigo_status": 2,
        "descripcion": "Muestreo IFA",
        "descripcion_corta": "MF",
        "codigo_origen": 0,
        "codigo_zona": 1,
    }
    assert by_code[4]["descripcion"] == "Delfoz ZC"
    assert by_code[5]["descripcion"] == "Delfoz ZF"
    assert by_code[8]["descripcion"] == "Muestreos ZC"
    assert by_code[4]["codigo_origen"] == 1 and by_code[4]["codigo_zona"] == 0
    assert query(config, "SELECT record_id FROM row_intents ORDER BY record_id") == [
        (11,),
        (14,),
        (15,),
        (18,),
    ]


def test_replay_and_activo_changes_never_write_or_change_first_evidence(
    config, source, monkeypatch
):
    run(config)
    initial = delta_files(config)
    first_intent = query(config, "SELECT after_json FROM row_intents")
    monkeypatch.setattr(DeltaStore, "insert", lambda *_: pytest.fail("Escritura sin novedades"))
    replay = run(config)
    assert replay["new_entries"] == 0 and replay["matched_entries"] == 1
    for active in ["", "0", "uninterpreted"]:
        source.write_text(f"11;2;Muestreo IFA;0;MF;1;{active};\n", encoding=config.encoding)
        result = run(config)
        assert result["status"] == "NO_CHANGE" and result["matched_entries"] == 1
        assert result["inserted_this_attempt"] == 0 and result["delta_version"] == 0
    assert delta_files(config) == initial
    assert query(config, "SELECT after_json FROM row_intents") == first_intent
    assert query(config, "SELECT COUNT(*) FROM attempts") == [(5,)]
    assert query(config, "SELECT COUNT(*) FROM receipts") == [(5,)]


def test_text_trim_retains_raw_without_changing_accents_case_or_prefix(tmp_path):
    result = parsed(tmp_path, "11;2;  Muestreo: Cávidad  ; 0 ; MF ; 1 ;1;\n")
    key = next(iter(result.pairs))
    assert key == (11, 2, "Muestreo: Cávidad", "MF", 0, 1)
    assert result.pairs[key][0]["raw"][2] == "  Muestreo: Cávidad  "


def test_unknown_numeric_origin_zone_are_preserved_and_text_is_not_a_filter(config, source):
    source.write_text("11;2;NO USAR;42;NO USAR;99;;\n", encoding=config.encoding)
    assert run(config)["inserted_this_attempt"] == 1
    assert rows(config)[0]["codigo_origen"] == 42
    assert rows(config)[0]["codigo_zona"] == 99
    assert query(config, "SELECT COUNT(*) FROM exclusions") == [(0,)]


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (0, ""),
        (0, "11.0"),
        (1, ""),
        (1, "2.0"),
        (2, " "),
        (3, ""),
        (3, "machine"),
        (3, "1.5"),
        (4, " "),
        (5, ""),
        (5, "ZF"),
        (5, "1.0"),
        (0, "9223372036854775808"),
        (1, "-9223372036854775809"),
        (3, "9223372036854775808"),
        (5, "9" * 5000),
    ],
)
def test_invalid_business_fields_keep_raw(tmp_path, index, value):
    fields = ["11", "2", "Muestreo IFA", "0", "MF", "1", "1"]
    fields[index] = value
    result = parsed(tmp_path, ";".join(fields) + ";\n")
    assert result.invalid_rows == 1 and not result.pairs
    assert result.issues[0]["reason"] == "INVALID_VALUE"
    assert result.issues[0]["evidence"]["raw"] == [*fields, ""]


@pytest.mark.parametrize(
    "text",
    [
        "11;2;Muestreo IFA;0;MF;1\n",
        "11;2;Muestreo IFA;0;MF;1;1;EXTRA;\n",
        "11;2;Muestreo IFA;0;MF;1;1;;\n",
    ],
)
def test_schema_drift_is_not_automatically_accepted(tmp_path, text):
    result = parsed(tmp_path, text)
    assert result.issues[0]["reason"] == "STRUCTURE" and not result.pairs


def test_int64_edges_and_optional_final_separator(tmp_path):
    result = parsed(
        tmp_path,
        "-9223372036854775808;+9223372036854775807;Texto;+00042;Corta;-9223372036854775808;\n",
    )
    assert not result.issues
    assert next(iter(result.pairs)) == (-(2**63), 2**63 - 1, "Texto", "Corta", 42, -(2**63))


@pytest.mark.parametrize(
    ("index", "value", "field"),
    [
        (0, "12", "id_status_fenix"),
        (1, "3", "codigo_status"),
        (2, "Muestreo cambiado", "descripcion"),
        (3, "1", "codigo_origen"),
        (4, "XX", "descripcion_corta"),
        (5, "2", "codigo_zona"),
    ],
)
def test_changed_fields_are_audited_without_overwrite(config, source, index, value, field):
    run(config)
    accepted = rows(config)
    initial_files = delta_files(config)
    fields = ["11", "2", "Muestreo IFA", "0", "MF", "1", "1"]
    fields[index] = value
    source.write_text(";".join(fields) + ";\n99;99;Nueva;0;N;0;1;\n", encoding=config.encoding)
    result = run(config)
    assert result["status"] == "BLOCKED" and result["conflict_entries"] == 1
    assert result["inserted_this_attempt"] == 0 and result["new_entries"] == 1
    assert rows(config) == accepted and delta_files(config) == initial_files
    issue = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert issue["accepted"] == accepted
    assert issue["changed_fields"] == {"11": [field]}
    assert issue["evidence"][0]["raw"] == [*fields, ""]
    assert run(config)["status"] == "BLOCKED"
    assert query(config, "SELECT COUNT(*) FROM issues") == [(1,)]
    assert query(config, "SELECT COUNT(*) FROM issue_evidence") == [(1,)]


@pytest.mark.parametrize(
    "second", ["11;3;Otra;0;O;1;1;", "12;2;Otra;0;O;1;1;", "11;2;Muestreo IFA;0;MF;2;1;"]
)
def test_conflicts_within_initial_batch_never_pick_a_winner(config, source, second):
    source.write_text(
        source.read_text(encoding=config.encoding) + second + "\n", encoding=config.encoding
    )
    result = run(config)
    assert result["status"] == "BLOCKED" and result["conflict_entries"] == 2
    assert not config.table_path.exists()
    assert query(config, "SELECT COUNT(*) FROM issues") == [(2,)]


def test_same_description_does_not_merge_codes_and_absence_does_not_delete(config, source):
    run(config)
    source.write_text("12;3;Muestreo IFA;0;MF;1;1;\n", encoding=config.encoding)
    assert run(config)["inserted_this_attempt"] == 1
    assert {r["codigo_status"] for r in rows(config)} == {2, 3}


def test_repeated_origins_and_names_have_separate_receipts(config, source):
    original = source.read_bytes()
    source.write_bytes(original * 3)
    (source.parent / "20261014_SIP_DEFECTO_STATUS.csv").write_bytes(source.read_bytes())
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "16102026_92028_SIP_DEFECTO_STATUS.csv").write_bytes(source.read_bytes())
    scoped = replace(config, sources=(*config.sources, ("AV22", other)))
    result = run(scoped)
    assert result["source_files"] == 3 and result["rows_read"] == 6
    assert result["unique_pairs"] == 1 and result["consolidated_repetitions"] == 5
    assert result["inserted_this_attempt"] == 1
    intent = json.loads(query(config, "SELECT after_json FROM row_intents")[0][0])
    assert len(intent["evidence"]) == 2
    assert all(e["duplicate_count"] == 2 for e in intent["evidence"])


def test_both_delivery_modes_and_modified_timestamped_file(config, source):
    run(config)
    source.write_text("12;3;Muestreo IFL;0;MP;1;1;\n", encoding=config.encoding)
    assert run(config)["delta_version"] == 1
    versioned = source.with_name("16102026_92028_SIP_DEFECTO_STATUS.csv")
    source.rename(versioned)
    assert run(config)["status"] == "NO_CHANGE"
    versioned.write_text("13;4;Delfoz ZC;1;DC;0;1;\n", encoding=config.encoding)
    assert run(config)["delta_version"] == 2 and len(rows(config)) == 3
    assert parse_name(versioned.name, config.family) == "2026-10-16T09:20:28"


@pytest.mark.parametrize("when", ["before", "after", "ack"])
def test_failures_reconcile_once_and_preserve_all_six_values(config, source, monkeypatch, when):
    original = DeltaStore.insert

    def fail(self, *args):
        if when == "after":
            original(self, *args)
        raise OSError("Simulated failure")

    with monkeypatch.context() as patch:
        if when == "ack":
            patch.setattr(Control, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        else:
            patch.setattr(DeltaStore, "insert", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["delta_version"] == 0 and len(rows(config)) == 1
    assert result["recovered_operations"][0]["wrote_this_attempt"] == (when == "before")
    assert rows(config)[0]["codigo_status"] == 2 and rows(config)[0]["id_status_fenix"] == 11
    assert query(config, "SELECT state,record_id FROM row_intents") == [("CONFIRMED", 11)]
    assert result["matched_entries"] == 1 and result["new_entries"] == 0


def test_retry_uses_prepared_revision_even_after_source_overwrite(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(DeltaStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before")))
        with pytest.raises(IngestaError):
            run(config)
    source.write_text("12;3;Muestreo IFL;0;MP;1;1;\n", encoding=config.encoding)
    result = run(config)
    assert result["inserted_this_attempt"] == 2 and result["delta_version"] == 1
    assert {r["codigo_status"] for r in rows(config)} == {2, 3}


def test_invalid_first_batch_blocks_all_rows(config, source):
    source.write_text(
        source.read_text(encoding=config.encoding) + "12;3;Otra;;O;1;1;\n", encoding=config.encoding
    )
    result = run(config)
    assert result["status"] == "BLOCKED" and result["invalid_rows"] == 1
    assert not config.table_path.exists()
    assert query(config, "SELECT COUNT(*) FROM row_intents") == [(0,)]


def test_all_three_contracts_coexist_without_reusing_control(config, source):
    (source.parent / "SIP_MODELO.csv").write_text("754;3081/360;1;\n", encoding="utf-8")
    (source.parent / "SIP_DEFECTO.csv").write_text(
        "1;400;Columpio;Columpio;0;\n", encoding="cp1252"
    )
    configs = [replace(config, family=f) for f in ["SIP_MODELO", "SIP_DEFECTO", config.family]]
    for selected in configs:
        assert len(discover(selected)) == 1 and run(selected)["inserted_this_attempt"] == 1
    assert len({c.control_path for c in configs}) == 3
    assert len({c.table_path for c in configs}) == 3
    for selected in configs:
        assert run(selected)["delta_version"] == 0
        assert len(rows(selected)) == 1


def test_encoding_csv_and_changed_bronze_keep_full_evidence(config, source, tmp_path):
    result = parsed(tmp_path, "11;2;Muestreo: Cávidad;0;MF;1;1;\n", encoding="utf-8-sig")
    assert result.issues[0]["reason"] == "ENCODING" and result.issues[0]["raw_hex"]
    result = parsed(tmp_path, '11;2;"Unclosed;0;MF;1;1;\n')
    assert result.issues[0]["reason"] == "CSV" and result.issues[0]["raw_text"]
    run(config)
    snap = Snapshot(**json.loads(query(config, "SELECT descriptor_json FROM receipts")[0][0]))
    Path(snap.bronze_path).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        status.parse([snap], config.encoding)


def test_empty_file_audits_without_creating_delta(config, source):
    source.write_bytes(b"\n")
    assert run(config)["status"] == "NO_CHANGE" and not config.table_path.exists()
    assert query(config, "SELECT COUNT(*) FROM attempts") == [(1,)]


def test_config_cli_and_control_namespace(config, source, tmp_path, capsys):
    values = {"plant": config.plant, "family": config.family, "encoding": config.encoding}
    values.update(
        {
            k: str(getattr(config, k))
            for k in ("input_root", "bronze_root", "silver_root", "control_root")
        }
    )
    file = tmp_path / "status.toml"
    file.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
        + '\n[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert load_config(file).control_path.name == "control-sip_defecto_status.sqlite"
    assert main(["ingest", "--config", str(file)]) == 0
    assert json.loads(capsys.readouterr().out)["contract"] == status.CONTRACT
