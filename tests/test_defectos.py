import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import defectos
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import ConfigurationError, IngestaError, IntegrityError
from ingesta_delta_lake.config import load_config
from ingesta_delta_lake.contracts import DEFECT_SCHEMA
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.delta import DeltaStore
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot, discover, parse_name


@pytest.fixture
def config(config):
    return replace(config, family="SIP_DEFECTO", encoding="cp1252")


@pytest.fixture
def source(config):
    path = config.input_root / "AV21" / "SIP_DEFECTO.csv"
    path.write_text("1;400;0400:Columpio;Columpio;0;\n", encoding=config.encoding)
    return path


def parsed(tmp_path, text, encoding="cp1252"):
    data = text.encode("cp1252")
    path = tmp_path / "snapshot.csv"
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
    return defectos.parse([snapshot], encoding)


def rows(config):
    return DeltaTable(str(config.table_path)).to_pyarrow_table().to_pylist()


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


@pytest.mark.parametrize(
    "marker", ["NO USAR", " no usar ", "99501:NO USAR", "99501: NO USAR", "99501 : no usar"]
)
@pytest.mark.parametrize("column", [2, 3])
def test_marker_in_either_description(tmp_path, marker, column):
    fields = ["407", "551", "Descripción", "Corta", "0"]
    fields[column] = marker
    result = parsed(tmp_path, ";".join(fields) + ";\n")
    assert not result.pairs and not result.issues
    assert result.excluded_rows == 1 and len(result.exclusions) == 1


@pytest.mark.parametrize(
    "text", ["NO USAR ESTE", "Texto NO USAR", "NO USAR:OTRO", "ABC:NO USAR", "NO  USAR"]
)
def test_marker_is_not_a_substring_rule(tmp_path, text):
    result = parsed(tmp_path, f"1;0;{text};Corta;0;\n")
    assert len(result.pairs) == 1 and not result.exclusions


def test_filter_before_uniqueness_and_no_text_merging(config, source):
    source.write_text(
        "407;551;99501:NO USAR;NO USAR;0;\n"
        "238;551;551: Gfa espessura 0.5mm;Espessura;0;\n"
        "408;552;99552:NO USAR;NO USAR;0;\n"
        "239;552;552: Gfa tensões;Tensões;0;\n"
        "21;41;41: Calibre;Cal.interior boca;0;\n"
        "22;42;42: Calibre;Cal.interior boca;0;\n",
        encoding=config.encoding,
    )
    result = run(config)
    assert result["status"] == "APPLIED" and result["inserted_this_attempt"] == 4
    assert result["excluded_entries"] == result["excluded_rows"] == 2
    assert {r["id_defecto_fenix"] for r in rows(config)} == {238, 239, 21, 22}
    assert DeltaTable(str(config.table_path)).to_pyarrow_table().schema == DEFECT_SCHEMA
    assert query(config, "SELECT COUNT(*) FROM exclusions") == [(2,)]
    assert query(config, "SELECT COUNT(*) FROM issues") == [(0,)]


def test_replay_measure_changes_and_renames_never_invoke_delta(config, source, monkeypatch):
    run(config)
    original_files = {p: p.read_bytes() for p in config.table_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(DeltaStore, "insert", lambda *_: pytest.fail("Escritura sin novedades"))
    assert run(config)["status"] == "NO_CHANGE"
    source.write_text("1;400;0400:Columpio;Columpio;CHANGED;\n", encoding=config.encoding)
    assert run(config)["status"] == "NO_CHANGE"
    dated = source.with_name("16102026_92028_SIP_DEFECTO.csv")
    source.rename(dated)
    assert run(config)["status"] == "NO_CHANGE"
    assert {
        p: p.read_bytes() for p in config.table_path.rglob("*") if p.is_file()
    } == original_files
    assert query(config, "SELECT COUNT(*) FROM row_intents") == [(1,)]


@pytest.mark.parametrize(
    "candidate",
    [
        "1;401;0400:Columpio;Columpio;0;\n",
        "2;400;0400:Columpio;Columpio;0;\n",
        "1;400;Texto nuevo;Columpio;0;\n",
        "1;400;0400:Columpio;Corta nueva;0;\n",
    ],
)
def test_conflicting_identity_or_description_is_retained(config, source, candidate):
    run(config)
    accepted = rows(config)
    source.write_text(candidate + "900;900;Nuevo;Nuevo;0;\n", encoding=config.encoding)
    assert run(config)["status"] == "BLOCKED"
    assert rows(config) == accepted
    issue = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert issue["accepted"] == accepted
    assert issue["evidence"][0]["raw"] == candidate.strip().split(";")
    assert run(config)["status"] == "BLOCKED"
    assert query(config, "SELECT COUNT(*) FROM issues") == [(1,)]
    assert query(config, "SELECT COUNT(*) FROM issue_evidence") == [(1,)]


@pytest.mark.parametrize(
    "text",
    [
        "1;400;A;B;0;\n1;401;A;B;0;\n",
        "1;400;A;B;0;\n2;400;A;B;0;\n",
        "1;400;A;B;0;\n1;400;C;B;0;\n",
    ],
)
def test_contradictions_inside_initial_snapshot(tmp_path, text):
    inserts, issues = defectos.decide(parsed(tmp_path, text), {})
    assert not inserts and len(issues) == 2
    assert all(i["reason"] == "ASSOCIATION_CONFLICT" for i in issues)


@pytest.mark.parametrize(
    "text",
    [
        "1.0;400;A;B;0;\n",
        "1;400.0;A;B;0;\n",
        ";400;A;B;0;\n",
        "1;;A;B;0;\n",
        "1;400;;B;0;\n",
        "1;400;A; ;0;\n",
        "9223372036854775808;400;A;B;0;\n",
        "1;-9223372036854775809;A;B;0;\n",
        "1;400;A;B;0;EXTRA;\n",
        "1;400;A;B\n",
        "1;" + "9" * 5000 + ";A;B;0;\n",
    ],
)
def test_invalids_preserve_raw_without_loss(tmp_path, text):
    result = parsed(tmp_path, text)
    assert not result.pairs and result.invalid_rows == 1
    assert result.issues[0]["evidence"]["raw"] == text.strip().split(";")


def test_int64_edges_and_description_normalization(tmp_path):
    result = parsed(tmp_path, "-9223372036854775808;+9223372036854775807;  Tensões  ; Córta ;0;\n")
    assert (-(2**63), 2**63 - 1, "Tensões", "Córta") in result.pairs
    ref = next(iter(result.pairs.values()))[0]
    assert ref["raw"][2] == "  Tensões  "


def test_encoding_and_csv_errors_keep_complete_evidence(tmp_path):
    result = parsed(tmp_path, "1;400;Tensões;Corta;0;\n", encoding="utf-8-sig")
    assert result.issues[0]["reason"] == "ENCODING" and result.issues[0]["raw_hex"]
    result = parsed(tmp_path, '1;400;"unclosed;Corta;0;\n')
    assert result.issues[0]["reason"] == "CSV" and result.issues[0]["raw_text"]


def test_exclusions_audited_with_multiplicity_but_no_table(config, source):
    source.write_text("407;551;99501:NO USAR;NO USAR;0;\n" * 5, encoding=config.encoding)
    result = run(config)
    assert result["status"] == "NO_CHANGE" and result["delta_version"] is None
    assert result["excluded_rows"] == 5 and result["excluded_entries"] == 1
    assert result["valid_rows"] == result["unique_pairs"] == 0
    assert not config.table_path.exists()
    record = json.loads(query(config, "SELECT payload_json FROM exclusion_evidence")[0][0])
    assert record["duplicate_count"] == 4 and record["raw"][0] == "407"
    assert run(config)["excluded_rows"] == 5
    assert query(config, "SELECT COUNT(*) FROM exclusions") == [(1,)]
    assert query(config, "SELECT COUNT(*) FROM exclusion_evidence") == [(1,)]


def test_exclusions_are_audited_even_when_an_invalid_blocks_batch(config, source):
    source.write_text("407;551;NO USAR;NO USAR;0;\n1;400;;Corta;0;\n", encoding=config.encoding)
    result = run(config)
    assert result["status"] == "BLOCKED" and result["invalid_rows"] == 1
    assert result["excluded_entries"] == 1 and not config.table_path.exists()
    assert query(config, "SELECT COUNT(*) FROM exclusions") == [(1,)]
    assert query(config, "SELECT reason FROM issues") == [("INVALID_VALUE",)]


def test_new_exclusion_or_missing_rows_do_not_delete_accepted(config, source):
    run(config)
    source.write_text("1;400;NO USAR;Columpio;0;\n2;401;Nueva;Nueva;0;\n", encoding=config.encoding)
    result = run(config)
    assert result["inserted_this_attempt"] == 1 and result["excluded_entries"] == 1
    assert {r["id_defecto_fenix"] for r in rows(config)} == {1, 2}


def test_excluded_rows_still_require_structure_but_not_business_types(tmp_path):
    result = parsed(tmp_path, "NOT_AN_ID;?;NO USAR;Corta;0;\n")
    assert not result.issues and result.excluded_rows == 1
    result = parsed(tmp_path, "NOT_AN_ID;?;NO USAR;Corta;0;EXTRA;\n")
    assert result.issues[0]["reason"] == "STRUCTURE"


def test_cross_origin_consolidation_and_repetition_counts(config, source):
    text = source.read_text(encoding=config.encoding)
    source.write_text(text * 3, encoding=config.encoding)
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "20261014_SIP_DEFECTO.csv").write_bytes(source.read_bytes())
    # Una segunda recepción idéntica del mismo origen no expande las filas parseadas.
    (source.parent / "20261014_SIP_DEFECTO.csv").write_bytes(source.read_bytes())
    result = run(replace(config, sources=(*config.sources, ("AV22", other))))
    assert result["source_files"] == 3 and result["rows_read"] == 6
    assert result["valid_rows"] == 6 and result["unique_pairs"] == 1
    assert result["consolidated_repetitions"] == 5
    assert result["inserted_this_attempt"] == 1


@pytest.mark.parametrize("when", ["before", "after", "ack"])
def test_recovery_preserves_full_defect_rows(config, source, monkeypatch, when):
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
    assert query(config, "SELECT state FROM row_intents") == [("CONFIRMED",)]


def test_retry_finishes_previous_snapshot_before_new_source(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(DeltaStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before")))
        with pytest.raises(IngestaError):
            run(config)
    source.write_text("2;401;Nueva;Nueva;0;\n", encoding=config.encoding)
    result = run(config)
    assert result["inserted_this_attempt"] == 2 and result["delta_version"] == 1
    assert {r["id_defecto_fenix"] for r in rows(config)} == {1, 2}


def test_replay_counts_current_matches_without_reporting_new_entries(config, source):
    assert run(config)["new_entries"] == 1
    replay = run(config)
    assert replay["matched_entries"] == 1 and replay["new_entries"] == 0
    assert replay["inserted_this_attempt"] == 0


def test_both_contracts_coexist_without_control_migration(config, source):
    model = replace(config, family="SIP_MODELO", encoding="utf-8-sig")
    (source.parent / "SIP_MODELO.csv").write_text("754;3081/360;1;\n", encoding="utf-8")
    run(model)
    first_model_intent = query(model, "SELECT after_json FROM row_intents")
    run(config)
    assert len(discover(config)) == len(discover(model)) == 1
    assert len(rows(model)) == len(rows(config)) == 1
    assert model.control_path != config.control_path
    assert run(model)["delta_version"] == run(config)["delta_version"] == 0
    assert query(model, "SELECT after_json FROM row_intents") == first_model_intent


def test_unknown_family_is_rejected_before_creating_destinations(config):
    with pytest.raises(ConfigurationError, match="Familia no implementada"):
        run(replace(config, family="FUENTE_DESCONOCIDA"))
    assert not config.control_root.exists()


def test_bronze_tampering_is_detected(config, source):
    run(config)
    descriptor = json.loads(query(config, "SELECT descriptor_json FROM receipts")[0][0])
    path = Path(descriptor["bronze_path"])
    path.write_bytes(b"tampered")
    snap = Snapshot(**descriptor)
    with pytest.raises(IntegrityError):
        defectos.parse([snap], config.encoding)


def test_defect_config_cli_and_versioned_names(config, source, tmp_path, capsys):
    values = {"plant": config.plant, "family": config.family, "encoding": config.encoding}
    values.update(
        {
            k: str(getattr(config, k))
            for k in ("input_root", "bronze_root", "silver_root", "control_root")
        }
    )
    file = tmp_path / "defectos.toml"
    file.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
        + '\n[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert load_config(file).family == "SIP_DEFECTO"
    assert main(["ingest", "--config", str(file)]) == 0
    assert json.loads(capsys.readouterr().out)["contract"] == defectos.CONTRACT
    assert parse_name("16102026_92028_SIP_DEFECTO.csv", "SIP_DEFECTO") == "2026-10-16T09:20:28"
