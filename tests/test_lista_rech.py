import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import lista_rech as rech
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import ConfigurationError, IngestaError, IntegrityError
from ingesta_delta_lake.rejections_service import inspection_bases
from ingesta_delta_lake.rejections_storage import RejectionsControl, RejectionsStore
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot


@pytest.fixture
def config(config):
    return replace(config, family="LISTA_RECH", timestamp_policy="local_naive_test_v1")


def line(
    date="14/10/2026 0:00:00",
    mold=0,
    branch=1,
    machine=13,
    inspected=96,
    rejected=12,
    automatic=0,
    cavity="",
    automatic_date="",
):
    return (
        ";".join(
            map(
                str,
                [
                    date,
                    mold,
                    branch,
                    machine,
                    inspected,
                    rejected,
                    automatic,
                    cavity,
                    automatic_date,
                ],
            )
        )
        + ";\n"
    )


@pytest.fixture
def source(config):
    path = config.input_root / "AV21" / "LISTA_RECH.csv"
    path.write_text(line(), encoding="utf-8")
    return path


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def rows(config):
    control_id = query(config, "SELECT value FROM settings WHERE key='control_id'")[0][0]
    return RejectionsStore(config, control_id).read()[1]


def files(config):
    return {
        str(p.relative_to(config.table_path)): p.read_bytes()
        for p in config.table_path.rglob("*")
        if p.is_file()
    }


def parsed(tmp_path, text, encoding="utf-8-sig"):
    p = tmp_path / "snapshot.csv"
    data = text.encode("utf-8") if isinstance(text, str) else text
    p.write_bytes(data)
    snapshot = Snapshot(
        "AV21",
        str(p),
        hashlib.sha256(data).hexdigest(),
        str(p),
        len(data),
        0,
        "2026-10-16T10:00:00Z",
        None,
    )
    return rech.parse([snapshot], encoding)


def test_schema_all_nine_fields_order_nulls_and_no_replay_write(config, source, monkeypatch):
    result = run(config)
    assert result["published_rows"] == 1 and result["complete"]
    table = DeltaTable(str(config.table_path)).to_pyarrow_table()
    assert table.schema == rech.SCHEMA and table.column_names == [*rech.FIELDS, "origen_fenix"]
    assert table.to_pylist()[0]["CAVIDAD"] is None and table.to_pylist()[0]["FECHA_R"] is None
    original = files(config)
    monkeypatch.setattr(
        RejectionsStore, "insert", lambda *_: pytest.fail("Escritura sin novedades")
    )
    source.write_text(
        line(date="14/10/2026 00:00:00", cavity="  ", automatic_date=" "), encoding="utf-8"
    )
    assert run(config)["status"] == "NO_CHANGE" and files(config) == original
    assert query(config, "SELECT state,COUNT(*) FROM observation_intents GROUP BY state") == [
        ("CONFIRMED", 1)
    ]


def test_zero_rows_and_automatic_greater_than_inspected_are_preserved(config, source):
    source.write_text(
        line(inspected=0, rejected=0)
        + line(mold=1, cavity="8F", inspected=18, rejected=0)
        + line(
            mold=3,
            cavity="2D",
            inspected=5,
            rejected=0,
            automatic=13,
            automatic_date="14/10/2026 02:32:45",
        )
        + line(mold=4, inspected=1, rejected=2, automatic=3),
        encoding="utf-8",
    )
    result = run(config)
    assert result["complete"] and result["published_rows"] == 4
    assert result["resolved_inspection_total"] == 24
    data = {r["MOLDE"]: r for r in rows(config).values()}
    assert data[3]["INSPECC"] == 5 and data[3]["RECH_AUTO"] == 13
    assert data[3]["FECHA_R"] == "2026-10-14T02:32:45"
    assert data[4]["FECHA_R"] is None and data[4]["RECH_AUTO"] == 3


@pytest.mark.parametrize(
    "field,value", [("RECH", 13), ("RECH_AUTO", 1), ("FECHA_R", "14/10/2026 00:01:00")]
)
def test_all_non_key_changes_preserve_candidate_without_overwriting(config, source, field, value):
    run(config)
    accepted = next(iter(rows(config).values()))
    raw = line().rstrip("\n").split(";")
    raw[rech.FIELDS.index(field)] = str(value)
    source.write_text(";".join(raw) + "\n" + line(machine=99), encoding="utf-8")
    result = run(config)
    assert result["status"] == "PARTIAL" and not result["complete"]
    assert result["inserted_this_attempt"] == 1
    assert rows(config)[rech.key(accepted)] == accepted
    case = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert case["reason"] == "ROW_CONFLICT" and field in case["changed_fields"]
    assert case["candidate"][field] != accepted[field] and case["accepted"][0] == accepted
    assert all(f in case["candidate"] for f in rech.FIELDS) and case["evidence"][0]["raw"]
    assert run(config)["inserted_this_attempt"] == 0
    assert query(config, "SELECT COUNT(*) FROM issues") == [(1,)]
    assert not inspection_bases(config)["complete"]


def test_equivalent_automatic_dates_are_no_change_even_if_earlier_than_main_date(config, source):
    source.write_text(line(automatic=13, automatic_date="13/10/2026 2:32:45"), encoding="utf-8")
    run(config)
    before = files(config)
    source.write_text(
        line(date="14/10/2026 00:00:00", automatic=13, automatic_date="13/10/2026 02:32:45"),
        encoding="utf-8",
    )
    assert run(config)["status"] == "NO_CHANGE" and files(config) == before


def test_revision_of_inspected_never_creates_96_plus_97(config, source):
    run(config)
    before = files(config)
    source.write_text(line(inspected=97), encoding="utf-8")
    result = run(config)
    assert result["counter_revisions"] == 1 and result["inserted_this_attempt"] == 0
    assert result["resolved_inspection_total"] == 0 and files(config) == before
    assert [r["INSPECC"] for r in rows(config).values()] == [96]
    case = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert case["candidate"]["INSPECC"] == 97 and case["accepted"][0]["INSPECC"] == 96


@pytest.mark.parametrize(
    "alternative",
    [line(rejected=13), line(inspected=97), line(automatic_date="14/10/2026 01:00:00")],
)
def test_ambiguous_first_input_has_no_winner_and_omitting_variant_does_not_resolve(
    config, source, alternative
):
    source.write_text(line() * 3 + alternative, encoding="utf-8")
    assert run(config)["status"] == "BLOCKED" and not config.table_path.exists()
    source.write_text(line(), encoding="utf-8")
    assert run(config)["status"] == "BLOCKED" and not config.table_path.exists()
    source.write_text(line(inspected=98), encoding="utf-8")
    assert run(config)["status"] == "BLOCKED" and not config.table_path.exists()


def test_equal_counts_are_independent_between_all_context_dimensions(config, source):
    source.write_text(
        line()
        + line(machine=12)
        + line(branch=2)
        + line(mold=1)
        + line(cavity="8F")
        + line(cavity="99Z")
        + line(cavity="null")
        + line(date="14/10/2026 0:10:00"),
        encoding="utf-8",
    )
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "LISTA_RECH.csv").write_text(line(), encoding="utf-8")
    selected = replace(config, sources=(*config.sources, ("AV22", other)))
    result = run(selected)
    assert result["published_rows"] == result["resolved_groups"] == 9
    assert result["resolved_inspection_total"] == 9 * 96
    assert {r["CAVIDAD"] for r in rows(selected).values()} == {None, "8F", "99Z", "null"}
    assert inspection_bases(selected, "AV22")["resolved_inspection_total"] == 96


def test_repeated_files_names_and_rows_audit_receipts_without_duplicates(config, source):
    source.write_bytes(source.read_bytes() * 5)
    renamed = source.with_name("16102026_92028_LISTA_RECH.csv")
    renamed.write_bytes(source.read_bytes())
    first = run(config)
    assert first["source_files"] == 2 and first["rows_read"] == 5
    assert first["consolidated_repetitions"] == 4 and first["published_rows"] == 1
    before = files(config)
    assert run(config)["status"] == "NO_CHANGE" and files(config) == before
    assert query(config, "SELECT COUNT(*) FROM receipts") == [(4,)]


def test_modified_fixed_name_and_new_dated_files_append_without_deleting_history(config, source):
    run(config)
    source.write_text(line(date="14/10/2026 0:10:00"), encoding="utf-8")
    assert run(config)["published_rows"] == 2
    source.rename(source.with_name("16102026_92028_LISTA_RECH.csv"))
    assert run(config)["status"] == "NO_CHANGE"


@pytest.mark.parametrize(
    "index,value",
    [
        (1, -1),
        (1, "1.5"),
        (4, -1),
        (4, ""),
        (4, "96.0"),
        (5, -1),
        (6, -1),
        (3, ""),
        (2, "1.0"),
        (4, "9223372036854775808"),
        (0, "29/02/2026 0:00:00"),
        (8, "14/31/2026 0:00:00"),
    ],
)
def test_invalid_numbers_and_dates_retain_original_evidence(tmp_path, index, value):
    raw = line().rstrip("\n").split(";")
    raw[index] = str(value)
    result = parsed(tmp_path, ";".join(raw) + "\n")
    assert result.invalid_rows == 1 and not result.entries
    assert result.issues[0]["reason"] == "INVALID_VALUE"
    assert result.issues[0]["evidence"]["raw"] == raw
    assert result.issues[0]["column"] == rech.FIELDS[index]


@pytest.mark.parametrize(
    "text", ["1;2;3\n", line().rstrip("\n") + "EXTRA;\n", '"UNCLOSED\n', b"\xff"]
)
def test_structure_csv_and_encoding_errors_are_explicit(tmp_path, text):
    result = parsed(tmp_path, text)
    assert result.issues and not result.entries
    assert result.issues[0]["reason"] in {"STRUCTURE", "CSV", "ENCODING"}


def test_trailing_separator_optional_and_codes_are_not_invented_enumerations(tmp_path):
    result = parsed(tmp_path, line(machine=-9, branch=-2).rstrip("\n")[:-1] + "\n")
    assert not result.issues and len(result.entries) == 1


def test_invalid_variant_holds_its_context_and_preserves_independent_rows(config, source):
    source.write_text(line() + line(automatic_date="BAD") + line(machine=99), encoding="utf-8")
    result = run(config)
    assert result["status"] == "PARTIAL" and result["invalid_rows"] == 1
    assert result["published_rows"] == 1 and next(iter(rows(config).values()))["COD_MAQ_ZF"] == 99
    source.write_text(line() + line(machine=99), encoding="utf-8")
    assert run(config)["inserted_this_attempt"] == 0


@pytest.mark.parametrize("phase", ["before", "after", "ack"])
def test_commit_recovery_confirms_once_without_duplicating_counters(
    config, source, monkeypatch, phase
):
    original = RejectionsStore.insert

    def fail(self, *args):
        if phase == "after":
            original(self, *args)
        raise OSError("Simulated failure")

    with monkeypatch.context() as patch:
        if phase == "ack":
            patch.setattr(
                RejectionsControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack"))
            )
        else:
            patch.setattr(RejectionsStore, "insert", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["delta_version"] == 0 and result["published_rows"] == 1
    assert result["resolved_inspection_total"] == 96
    assert result["recovered_operations"][0]["wrote_this_attempt"] == (phase == "before")
    assert query(config, "SELECT state,COUNT(*) FROM observation_intents GROUP BY state") == [
        ("CONFIRMED", 1)
    ]


def test_pending_snapshot_recovers_even_when_live_source_changes(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            RejectionsStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before"))
        )
        with pytest.raises(IngestaError):
            run(config)
    source.write_text(line(date="14/10/2026 0:10:00"), encoding="utf-8")
    result = run(config)
    assert result["published_rows"] == 2 and result["inserted_this_attempt"] == 2


def test_pending_bronze_tampering_and_unconfirmed_reads_are_blocked(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            RejectionsControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack"))
        )
        with pytest.raises(IngestaError):
            run(config)
    with pytest.raises(IntegrityError, match="Recuperar"):
        inspection_bases(config)
    snap = json.loads(query(config, "SELECT descriptor_json FROM receipts")[0][0])
    Path(snap["bronze_path"]).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        run(config)


def test_bases_read_is_diagnostic_read_only_not_score_denominator_aggregation(config, source):
    assert inspection_bases(config)["status"] == "NO_INPUTS" and not config.control_root.exists()
    source.write_text(
        line() + line(mold=1, cavity="8F", inspected=18, rejected=0), encoding="utf-8"
    )
    run(config)
    before = files(config)
    attempts = query(config, "SELECT COUNT(*) FROM attempts")
    result = inspection_bases(config)
    assert result["denominator_source"] == "LISTA_RECH"
    assert result["resolved_inspection_total"] == 114
    assert result["inspection_metric"] == "inspections_by_context_not_unique_bottles"
    # La métrica legacy toma max por fecha/ramal: no es la suma diagnóstica de contextos.
    assert max(g["INSPECC"] for g in result["groups"]) == 96
    assert files(config) == before and query(config, "SELECT COUNT(*) FROM attempts") == attempts
    with pytest.raises(ConfigurationError):
        replace(config, timestamp_policy=None).validate()


def test_empty_file_no_empty_delta_and_unscoped_error_marks_origin_coverage(config, source):
    source.write_bytes(b"")
    assert run(config)["status"] == "NO_CHANGE" and not config.table_path.exists()
    source.write_text(line(), encoding="utf-8")
    run(config)
    source.write_text("BROKEN;SCHEMA\n", encoding="utf-8")
    result = run(config)
    assert result["status"] == "PARTIAL" and result["unresolved_groups"] == 1
    assert inspection_bases(config)["groups"][0]["INSPECC"] is None


def test_rech_and_defects_separate_controls_and_five_defects_do_not_multiply_base(config, source):
    from ingesta_delta_lake.defects_service import inspection_bases as defect_bases

    selected = replace(config, family="LISTA_DEFECTOS")
    path = source.with_name("LISTA_DEFECTOS.csv")
    path.write_text(
        "".join(
            ";".join(map(str, ["14/10/2026 0:00:00", 0, 1, 13, d, 96, 2, 2, *([0] * 9), ""]))
            + ";\n"
            for d in (15, 17, 37, 38, 39)
        ),
        encoding="utf-8",
    )
    assert run(selected)["published_rows"] == 5
    assert run(config)["published_rows"] == 1
    raw_base = inspection_bases(config)["groups"][0]
    assert raw_base["INSPECC"] == 96 and defect_bases(selected)["groups"][0]["INSPECC"] == 96
    # Consulta por firma única, sin sumar una fila de inspeccionadas por cada defecto.
    lookup = {tuple(g["group"]): g["INSPECC"] for g in inspection_bases(config)["groups"]}
    defects = DeltaTable(str(selected.table_path)).to_pyarrow_table().to_pylist()
    signatures = {
        (
            r["origen_fenix"],
            r["FECHA"].isoformat(timespec="seconds"),
            r["MOLDE"],
            r["LINEA"],
            r["COD_MAQ_ZF"],
            r["CAVIDAD"],
        )
        for r in defects
    }
    assert sum(lookup[s] for s in signatures) == 96
    assert selected.control_path != config.control_path and selected.table_path != config.table_path


def test_cli_ingestion_and_query(config, source, tmp_path, capsys):
    values = {
        "plant": config.plant,
        "family": config.family,
        "timestamp_policy": config.timestamp_policy,
    }
    values.update(
        {
            k: str(getattr(config, k))
            for k in ("input_root", "bronze_root", "silver_root", "control_root")
        }
    )
    p = tmp_path / "rech.toml"
    p.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
        + '\n[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert main(["ingest", "--config", str(p)]) == 0
    assert json.loads(capsys.readouterr().out)["published_rows"] == 1
    assert main(["inspection-bases", "--config", str(p), "--origin", "AV21"]) == 0
    assert json.loads(capsys.readouterr().out)["denominator_source"] == "LISTA_RECH"
