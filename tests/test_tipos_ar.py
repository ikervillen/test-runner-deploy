import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import tipos_ar
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import CaptureError, IngestaError, IntegrityError
from ingesta_delta_lake.config import load_config
from ingesta_delta_lake.contracts import AR_SCHEMA
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.delta import DeltaStore
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot, discover, parse_name


@pytest.fixture
def config(config):
    return replace(config, family="SIP_DEFECTO_AR", encoding="cp1252")


@pytest.fixture
def source(config):
    path = config.input_root / "AV21" / "SIP_DEFECTO_AR.csv"
    # ID distinto de código: LISTA_AR.TIPO interpretará 2, nunca 41.
    path.write_text("41;2;Aviso ZC;2;\n", encoding=config.encoding)
    return path


def parsed(tmp_path, text, encoding="cp1252"):
    path = tmp_path / "snapshot.csv"
    data = text.encode("cp1252")
    path.write_bytes(data)
    snap = Snapshot(
        "AV21",
        str(path),
        hashlib.sha256(data).hexdigest(),
        str(path),
        len(data),
        0,
        "2026-10-16T09:20:00Z",
        None,
    )
    return tipos_ar.parse([snap], encoding)


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


def test_schema_names_and_grouping_are_preserved_without_semantic_labels(config, source):
    source.write_text(
        "41;2;Aviso ZC;2;\n42;5;Rechazo molde NP;3;\n"
        "43;6;Rechazo Trampilla;2;\n44;4;Anotación ZF;4;\n",
        encoding=config.encoding,
    )
    result = run(config)
    assert result["inserted_this_attempt"] == 4 and result["issues"] == 0
    table = DeltaTable(str(config.table_path)).to_pyarrow_table()
    assert table.schema == AR_SCHEMA
    by_code = {r["codigo_tipo_ar"]: r for r in table.to_pylist()}
    assert by_code[2] == {
        "id_tipo_ar_fenix": 41,
        "codigo_tipo_ar": 2,
        "descripcion": "Aviso ZC",
        "codigo_agrupacion": 2,
    }
    assert by_code[5]["descripcion"] == "Rechazo molde NP"
    assert by_code[4]["descripcion"] == "Anotación ZF"
    assert by_code[2]["codigo_agrupacion"] == by_code[6]["codigo_agrupacion"] == 2
    assert query(config, "SELECT record_id FROM row_intents ORDER BY record_id") == [
        (41,),
        (42,),
        (43,),
        (44,),
    ]


def test_replay_renames_and_equivalent_whitespace_never_write(config, source, monkeypatch):
    run(config)
    files = delta_files(config)
    first_intent = query(config, "SELECT after_json FROM row_intents")
    monkeypatch.setattr(DeltaStore, "insert", lambda *_: pytest.fail("Escritura sin novedades"))
    replay = run(config)
    assert replay["new_entries"] == 0 and replay["matched_entries"] == 1
    source.write_text("+0041;02; Aviso ZC ; +02 ;\n", encoding=config.encoding)
    assert run(config)["status"] == "NO_CHANGE"
    source.rename(source.with_name("16102026_92028_SIP_DEFECTO_AR.csv"))
    assert run(config)["status"] == "NO_CHANGE"
    assert delta_files(config) == files
    assert query(config, "SELECT after_json FROM row_intents") == first_intent
    assert query(config, "SELECT COUNT(*) FROM receipts") == [(4,)]


@pytest.mark.parametrize(
    ("index", "value", "field"),
    [
        (0, "42", "id_tipo_ar_fenix"),
        (1, "3", "codigo_tipo_ar"),
        (2, "Nueva descripción", "descripcion"),
        (3, "99", "codigo_agrupacion"),
    ],
)
def test_each_changed_business_field_is_retained_for_review(config, source, index, value, field):
    run(config)
    accepted, files = rows(config), delta_files(config)
    fields = ["41", "2", "Aviso ZC", "2"]
    fields[index] = value
    source.write_text(";".join(fields) + ";\n90;90;Independiente;2;\n", encoding=config.encoding)
    result = run(config)
    assert result["status"] == "BLOCKED" and result["conflict_entries"] == 1
    assert result["new_entries"] == 1 and result["inserted_this_attempt"] == 0
    assert rows(config) == accepted and delta_files(config) == files
    issue = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert issue["accepted"] == accepted
    assert issue["changed_fields"] == {"41": [field]}
    assert issue["evidence"][0]["raw"] == [*fields, ""]
    assert run(config)["status"] == "BLOCKED"
    assert query(config, "SELECT COUNT(*) FROM issues") == [(1,)]
    assert query(config, "SELECT COUNT(*) FROM issue_evidence") == [(1,)]


@pytest.mark.parametrize(
    "second", ["41;3;Otro;2;", "42;2;Otro;2;", "41;2;Distinto;2;", "41;2;Aviso ZC;3;"]
)
def test_initial_conflicts_never_pick_by_order_or_majority(config, source, second):
    source.write_text(
        source.read_text(encoding=config.encoding) * 3 + second + "\n", encoding=config.encoding
    )
    result = run(config)
    assert result["status"] == "BLOCKED" and result["conflict_entries"] == 2
    assert not config.table_path.exists()
    assert query(config, "SELECT COUNT(*) FROM issues") == [(2,)]


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (0, ""),
        (0, "41.0"),
        (0, "9223372036854775808"),
        (1, ""),
        (1, "2.5"),
        (1, "-9223372036854775809"),
        (2, " "),
        (3, ""),
        (3, "Avisos"),
        (3, "2.0"),
        (3, "9" * 5000),
    ],
)
def test_invalid_values_keep_original_row(tmp_path, index, value):
    fields = ["41", "2", "Aviso ZC", "2"]
    fields[index] = value
    result = parsed(tmp_path, ";".join(fields) + ";\n")
    assert not result.pairs and result.invalid_rows == 1
    assert result.issues[0]["reason"] == "INVALID_VALUE"
    assert result.issues[0]["evidence"]["raw"] == [*fields, ""]


@pytest.mark.parametrize(
    "text", ["41;2;Aviso ZC\n", "41;2;Aviso ZC;2;EXTRA;\n", "41;2;Aviso ZC;2;;\n"]
)
def test_structure_change_does_not_evolve_schema(config, source, text):
    source.write_text(text, encoding=config.encoding)
    assert run(config)["status"] == "BLOCKED" and not config.table_path.exists()
    assert query(config, "SELECT reason FROM issues") == [("STRUCTURE",)]


def test_int64_edges_optional_final_separator_and_raw_text(tmp_path):
    result = parsed(tmp_path, "-9223372036854775808;+9223372036854775807;  Anotación: ZC  ;0\n")
    key = (-(2**63), 2**63 - 1, "Anotación: ZC", 0)
    assert not result.issues and key in result.pairs
    assert result.pairs[key][0]["raw"][2] == "  Anotación: ZC  "


def test_shared_text_and_groups_are_not_keys_and_new_groups_are_not_rejected(config, source):
    run(config)
    source.write_text("42;3;Aviso ZC;2;\n43;4;NO USAR;99;\n", encoding=config.encoding)
    result = run(config)
    assert result["inserted_this_attempt"] == 2
    assert {r["codigo_tipo_ar"] for r in rows(config)} == {2, 3, 4}
    assert any(r["codigo_agrupacion"] == 99 for r in rows(config))
    assert query(config, "SELECT COUNT(*) FROM exclusions") == [(0,)]


def test_duplicate_origins_and_revisions_consolidate_without_losing_counts(config, source):
    source.write_bytes(source.read_bytes() * 4)
    (source.parent / "20261014_SIP_DEFECTO_AR.csv").write_bytes(source.read_bytes())
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "16102026_92028_SIP_DEFECTO_AR.csv").write_bytes(source.read_bytes())
    result = run(replace(config, sources=(*config.sources, ("AV22", other))))
    assert result["source_files"] == 3 and result["rows_read"] == 8
    assert result["unique_pairs"] == 1 and result["consolidated_repetitions"] == 7
    assert result["inserted_this_attempt"] == 1
    intent = json.loads(query(config, "SELECT after_json FROM row_intents")[0][0])
    assert len(intent["evidence"]) == 2
    assert all(ref["duplicate_count"] == 3 for ref in intent["evidence"])


def test_overwrites_and_new_versions_append_without_deleting_history(config, source):
    run(config)
    source.write_text("42;3;Aviso ZF;2;\n", encoding=config.encoding)
    assert run(config)["delta_version"] == 1
    versioned = source.with_name("16102026_92028_SIP_DEFECTO_AR.csv")
    source.rename(versioned)
    assert run(config)["status"] == "NO_CHANGE"
    versioned.write_text("43;5;Rechazo molde NP;3;\n", encoding=config.encoding)
    assert run(config)["delta_version"] == 2 and len(rows(config)) == 3
    assert parse_name(versioned.name, config.family) == "2026-10-16T09:20:28"
    (source.parent / "bad_SIP_DEFECTO_AR.csv").write_bytes(versioned.read_bytes())
    with pytest.raises(CaptureError):
        run(config)


@pytest.mark.parametrize("when", ["before", "after", "ack"])
def test_recovery_confirms_exactly_one_commit(config, source, monkeypatch, when):
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
    assert query(config, "SELECT state,record_id FROM row_intents") == [("CONFIRMED", 41)]
    assert rows(config)[0]["codigo_agrupacion"] == 2
    assert result["new_entries"] == 0 and result["matched_entries"] == 1


def test_pending_revision_survives_source_overwrite(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(DeltaStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before")))
        with pytest.raises(IngestaError):
            run(config)
    source.write_text("42;3;Aviso ZF;2;\n", encoding=config.encoding)
    result = run(config)
    assert result["inserted_this_attempt"] == 2 and result["delta_version"] == 1
    assert {r["codigo_tipo_ar"] for r in rows(config)} == {2, 3}


def test_invalid_row_blocks_otherwise_valid_new_entries(config, source):
    source.write_text(
        source.read_text(encoding=config.encoding) + "42;3;Otra;;\n", encoding=config.encoding
    )
    result = run(config)
    assert result["status"] == "BLOCKED" and result["invalid_rows"] == 1
    assert not config.table_path.exists()
    assert query(config, "SELECT COUNT(*) FROM row_intents") == [(0,)]


def test_encoding_csv_and_altered_bronze_preserve_or_check_evidence(config, source, tmp_path):
    result = parsed(tmp_path, "41;2;Anotación ZF;4;\n", encoding="utf-8-sig")
    assert result.issues[0]["reason"] == "ENCODING" and result.issues[0]["raw_hex"]
    result = parsed(tmp_path, '41;2;"Unclosed;4;\n')
    assert result.issues[0]["reason"] == "CSV" and result.issues[0]["raw_text"]
    run(config)
    snap = Snapshot(**json.loads(query(config, "SELECT descriptor_json FROM receipts")[0][0]))
    Path(snap.bronze_path).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        tipos_ar.parse([snap], config.encoding)


def test_empty_file_audits_without_an_empty_table(config, source):
    source.write_bytes(b"\n")
    assert run(config)["status"] == "NO_CHANGE" and not config.table_path.exists()
    assert query(config, "SELECT COUNT(*) FROM attempts") == [(1,)]


def test_all_four_contracts_have_independent_tables_and_controls(config, source):
    fixtures = {
        "SIP_MODELO": "754;3081/360;1;\n",
        "SIP_DEFECTO": "1;400;Columpio;Columpio;0;\n",
        "SIP_DEFECTO_STATUS": "1;1;Tiempo Real;1;TR;2;1;\n",
    }
    for family, content in fixtures.items():
        (source.parent / f"{family}.csv").write_text(content, encoding="cp1252")
    configs = [replace(config, family=f) for f in [*fixtures, config.family]]
    for selected in configs:
        assert len(discover(selected)) == 1 and run(selected)["inserted_this_attempt"] == 1
    assert len({c.control_path for c in configs}) == 4
    assert len({c.table_path for c in configs}) == 4
    for selected in configs:
        assert run(selected)["delta_version"] == 0 and len(rows(selected)) == 1


def test_config_cli_and_namespace(config, source, tmp_path, capsys):
    values = {"plant": config.plant, "family": config.family, "encoding": config.encoding}
    values.update(
        {
            k: str(getattr(config, k))
            for k in ("input_root", "bronze_root", "silver_root", "control_root")
        }
    )
    file = tmp_path / "tipos_ar.toml"
    file.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
        + '\n[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert load_config(file).control_path.name == "control-sip_defecto_ar.sqlite"
    assert main(["ingest", "--config", str(file)]) == 0
    assert json.loads(capsys.readouterr().out)["contract"] == tipos_ar.CONTRACT
