import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import lista_defectos as defects
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import ConfigurationError, IngestaError, IntegrityError
from ingesta_delta_lake.defects_control import DefectsControl
from ingesta_delta_lake.defects_service import inspection_bases
from ingesta_delta_lake.defects_storage import DefectsStore
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot


@pytest.fixture
def config(config):
    return replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")


def line(
    date="14/10/2026 0:00:00",
    mold=0,
    branch=1,
    machine=13,
    defect=15,
    inspected=96,
    rejected=2,
    channels=None,
    cavity="",
):
    channels = [rejected, *([0] * 9)] if channels is None else channels
    return (
        ";".join(
            map(str, [date, mold, branch, machine, defect, inspected, rejected, *channels, cavity])
        )
        + ";\n"
    )


@pytest.fixture
def source(config):
    p = config.input_root / "AV21" / "LISTA_DEFECTOS.csv"
    p.write_text(line(), encoding="utf-8")
    return p


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def rows(config):
    control_id = query(config, "SELECT value FROM settings WHERE key='control_id'")[0][0]
    return DefectsStore(config, control_id).read()[1]


def files(config):
    return {
        str(p.relative_to(config.table_path)): p.read_bytes()
        for p in config.table_path.rglob("*")
        if p.is_file()
    }


def parsed(tmp_path, text, encoding="utf-8-sig"):
    p = tmp_path / "snapshot.csv"
    data = text.encode("utf-8")
    p.write_bytes(data)
    return defects.parse(
        [
            Snapshot(
                "AV21",
                str(p),
                hashlib.sha256(data).hexdigest(),
                str(p),
                len(data),
                0,
                "2026-10-16T10:00:00Z",
                None,
            )
        ],
        encoding,
    )


def test_eighteen_columns_and_null_cavity_replay_without_any_write(config, source, monkeypatch):
    result = run(config)
    assert result["published_rows"] == 1 and result["complete"]
    arrow = DeltaTable(str(config.table_path)).to_pyarrow_table()
    assert arrow.schema == defects.SCHEMA
    assert arrow.column_names == [*defects.FIELDS, "origen_fenix"]
    assert arrow.to_pylist()[0]["CAVIDAD"] is None
    original = files(config)
    monkeypatch.setattr(DefectsStore, "insert", lambda *_: pytest.fail("Escritura sin novedades"))
    assert run(config)["status"] == "NO_CHANGE"
    source.write_text(line(date="14/10/2026 00:00:00", cavity="  "), encoding="utf-8")
    assert run(config)["matched_entries"] == 1
    assert files(config) == original
    assert query(config, "SELECT COUNT(*) FROM observation_intents") == [(1,)]


def test_five_defects_share_96_inspections_and_machine_12_remains_separate(config, source):
    source.write_text(
        "".join(line(defect=d) for d in [15, 17, 37, 38, 39])
        + line(machine=12, defect=14, inspected=89),
        encoding="utf-8",
    )
    result = run(config)
    assert result["published_rows"] == 6 and result["resolved_groups"] == 2
    assert result["resolved_inspection_total"] == 185
    assert sum(r["INSPECC"] for r in rows(config).values()) == 569
    groups = inspection_bases(config)["groups"]
    assert sorted(g["INSPECC"] for g in groups) == [89, 96]
    assert result["inspection_metric"] == "inspections_by_context_not_unique_bottles"


def test_equal_inspected_values_in_distinct_contexts_are_not_sum_distinct(config, source):
    source.write_text(
        line()
        + line(machine=12)
        + line(branch=2)
        + line(mold=1)
        + line(cavity="8F")
        + line(cavity="99Z")
        + line(cavity="null"),
        encoding="utf-8",
    )
    result = run(config)
    assert result["published_rows"] == result["resolved_groups"] == 7
    assert result["resolved_inspection_total"] == 7 * 96
    assert {r["CAVIDAD"] for r in rows(config).values()} == {None, "8F", "99Z", "null"}


def test_original_rech_and_all_channels_are_independent_and_never_recalculated(config, source):
    channels = [1, 0, 0, 1, 0, 0, 0, 0, 0, 0]
    source.write_text(line(rejected=1, channels=channels), encoding="utf-8")
    assert run(config)["complete"]
    row = next(iter(rows(config).values()))
    assert row["RECH"] == 1 and sum(row[f"RECH{i}"] for i in range(1, 11)) == 2
    assert [row[f"RECH{i}"] for i in range(1, 11)] == channels


@pytest.mark.parametrize("field", ["RECH", "RECH1", "RECH10"])
def test_changed_rejections_keep_full_candidate_and_never_overwrite(config, source, field):
    run(config)
    original = next(iter(rows(config).values()))
    values = ["14/10/2026 0:00:00", 0, 1, 13, 15, 96, 2, 2, *([0] * 9), ""]
    values[defects.FIELDS.index(field)] = 3
    source.write_text(";".join(map(str, values)) + ";\n" + line(machine=99), encoding="utf-8")
    result = run(config)
    assert result["status"] == "PARTIAL" and result["inserted_this_attempt"] == 1
    assert rows(config)[defects.key(original)] == original
    case = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert case["reason"] == "ROW_CONFLICT" and case["candidate"][field] == 3
    assert case["accepted"][0] == original
    assert len([f for f in defects.FIELDS if f in case["candidate"]]) == 18
    assert case["evidence"][0]["raw"]
    assert run(config)["inserted_this_attempt"] == 0
    assert query(config, "SELECT COUNT(*) FROM issues") == [(1,)]
    # La revisión semántica es única; cada lote conserva su propia evidencia.
    assert query(
        config,
        "SELECT COUNT(DISTINCT issue_id), COUNT(DISTINCT evidence_hash), "
        "COUNT(DISTINCT batch_id) FROM issue_evidence",
    ) == [(1, 1, 2)]
    assert inspection_bases(config)["complete"] is False


def test_changed_inspected_quantity_is_review_not_96_plus_97(config, source):
    run(config)
    original = files(config)
    source.write_text(line(inspected=97), encoding="utf-8")
    result = run(config)
    assert result["counter_revisions"] == 1 and result["inserted_this_attempt"] == 0
    assert files(config) == original
    assert [r["INSPECC"] for r in rows(config).values()] == [96]
    assert result["resolved_inspection_total"] == 0 and result["unresolved_groups"] == 1
    case = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert case["candidate"]["INSPECC"] == 97 and case["accepted"][0]["INSPECC"] == 96
    assert case["candidate_hash"] != case["accepted_hashes"][0]


def test_different_defects_with_incompatible_group_counts_are_not_a_resolved_base(config, source):
    source.write_text(line() + line(defect=17, inspected=97), encoding="utf-8")
    first = run(config)
    assert first["status"] == "BLOCKED" and first["inspection_conflicts"] == 2
    assert not config.table_path.exists()
    # Omitir una alternativa no decide una corrección de fuente.
    source.write_text(line(), encoding="utf-8")
    assert run(config)["status"] == "BLOCKED" and not config.table_path.exists()


def test_two_rejection_variants_in_initial_file_have_no_arbitrary_winner(config, source):
    source.write_text(line() * 3 + line(rejected=3), encoding="utf-8")
    assert run(config)["status"] == "BLOCKED"
    assert not config.table_path.exists()
    assert query(config, "SELECT COUNT(*) FROM issues") == [(2,)]
    source.write_text(line(), encoding="utf-8")
    assert run(config)["status"] == "BLOCKED"


def test_different_cavities_are_independent_even_with_different_inspected(config, source):
    source.write_text(line(cavity="8F") + line(cavity="1C", inspected=97), encoding="utf-8")
    result = run(config)
    assert result["complete"] and result["published_rows"] == 2
    assert result["resolved_groups"] == 2 and result["resolved_inspection_total"] == 193


def test_same_ids_and_counts_in_two_origins_are_not_duplicates(config, source):
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "LISTA_DEFECTOS.csv").write_bytes(source.read_bytes())
    selected = replace(config, sources=(*config.sources, ("AV22", other)))
    result = run(selected)
    assert result["published_rows"] == 2 and result["resolved_inspection_total"] == 192
    assert inspection_bases(selected, "AV22")["resolved_inspection_total"] == 96


def test_duplicate_receipts_rows_and_renames_keep_separate_audit_not_data(config, source):
    source.write_bytes(source.read_bytes() * 5)
    named = source.with_name("16102026_92028_LISTA_DEFECTOS.csv")
    named.write_bytes(source.read_bytes())
    result = run(config)
    assert result["source_files"] == 2 and result["rows_read"] == 5
    assert result["consolidated_repetitions"] == 4 and result["published_rows"] == 1
    intent = json.loads(query(config, "SELECT after_json FROM observation_intents")[0][0])
    assert intent["evidence"][0]["duplicate_count"] == 4
    before = files(config)
    assert run(config)["status"] == "NO_CHANGE" and files(config) == before
    assert query(config, "SELECT COUNT(*) FROM receipts") == [(4,)]


def test_overwritten_files_append_new_dates_without_deleting_absent_records(config, source):
    run(config)
    source.write_text(line(date="14/10/2026 0:10:00"), encoding="utf-8")
    assert run(config)["published_rows"] == 2
    source.rename(source.with_name("16102026_92028_LISTA_DEFECTOS.csv"))
    assert run(config)["status"] == "NO_CHANGE"


@pytest.mark.parametrize(
    "index,value",
    [
        (1, -1),
        (1, "1.5"),
        (5, -1),
        (5, ""),
        (5, "96.0"),
        (6, -1),
        (7, -1),
        (16, -1),
        (3, ""),
        (4, "1.0"),
        (5, "9223372036854775808"),
    ],
)
def test_invalid_integers_quantities_and_required_fields_keep_raw(tmp_path, index, value):
    values = line().strip().split(";")
    values[index] = str(value)
    result = parsed(tmp_path, ";".join(values) + "\n")
    assert result.invalid_rows == 1 and not result.entries
    assert result.issues[0]["reason"] == "INVALID_VALUE"
    assert result.issues[0]["evidence"]["raw"] == values


@pytest.mark.parametrize(
    "text",
    [
        line(date="10/31/2026 0:00:00"),
        line(date="29/02/2026 0:00:00"),
        line().rstrip("\n") + "EXTRA;\n",
        "1;2;3\n",
    ],
)
def test_invalid_dates_and_schema_changes_are_retained(tmp_path, text):
    result = parsed(tmp_path, text)
    assert result.invalid_rows == 1 and result.issues[0]["evidence"]["raw"]


def test_nullable_cavity_without_extra_final_separator_is_valid(tmp_path):
    value = line()[:-2] + "\n"  # Dieciocho campos; cavidad final vacía.
    result = parsed(tmp_path, value)
    assert not result.issues and len(result.entries) == 1


def test_invalid_version_blocks_new_rows_of_its_group_but_not_another_context(config, source):
    source.write_text(line() + line(rejected="BAD") + line(machine=99), encoding="utf-8")
    result = run(config)
    assert result["status"] == "PARTIAL" and result["invalid_rows"] == 1
    assert result["published_rows"] == 1
    assert next(iter(rows(config).values()))["COD_MAQ_ZF"] == 99


@pytest.mark.parametrize("phase", ["before", "after", "ack"])
def test_commit_recovery_does_not_duplicate_rows_or_confirmed_intents(
    config, source, monkeypatch, phase
):
    original = DefectsStore.insert

    def fail(self, *args):
        if phase == "after":
            original(self, *args)
        raise OSError("Simulated failure")

    with monkeypatch.context() as patch:
        if phase == "ack":
            patch.setattr(
                DefectsControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack"))
            )
        else:
            patch.setattr(DefectsStore, "insert", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["delta_version"] == 0 and result["published_rows"] == 1
    assert result["recovered_operations"][0]["wrote_this_attempt"] == (phase == "before")
    assert query(config, "SELECT state,COUNT(*) FROM observation_intents GROUP BY state") == [
        ("CONFIRMED", 1)
    ]


def test_recovery_uses_prepared_revision_after_source_changes(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(DefectsStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before")))
        with pytest.raises(IngestaError):
            run(config)
    source.write_text(line(date="14/10/2026 0:10:00"), encoding="utf-8")
    result = run(config)
    assert result["inserted_this_attempt"] == 2 and result["published_rows"] == 2


def test_altered_bronze_and_unparseable_csv_are_not_silently_accepted(config, source, tmp_path):
    result = parsed(tmp_path, '"UNCLOSED\n')
    assert result.issues[0]["reason"] == "CSV" and result.issues[0]["raw_text"]
    run(config)
    descriptor = json.loads(query(config, "SELECT descriptor_json FROM receipts")[0][0])
    Path(descriptor["bronze_path"]).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        defects.parse([Snapshot(**descriptor)], config.encoding)


def test_bases_query_is_read_only_and_empty_source_never_creates_empty_delta(config, source):
    assert inspection_bases(config)["status"] == "NO_INPUTS" and not config.control_root.exists()
    source.write_bytes(b"")
    assert run(config)["status"] == "NO_CHANGE" and not config.table_path.exists()
    source.write_text(line(), encoding="utf-8")
    run(config)
    before = query(config, "SELECT COUNT(*) FROM attempts")
    assert inspection_bases(config)["resolved_inspection_total"] == 96
    assert query(config, "SELECT COUNT(*) FROM attempts") == before
    with pytest.raises(ConfigurationError):
        replace(config, timestamp_policy=None).validate()


def test_bases_read_rejects_an_unconfirmed_delta_commit(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(DefectsControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        with pytest.raises(IngestaError):
            run(config)
    with pytest.raises(IntegrityError, match="Recuperar"):
        inspection_bases(config)
    run(config)
    assert inspection_bases(config)["complete"]


def test_cli_ingestion_and_inspection_bases(config, source, tmp_path, capsys):
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
    p = tmp_path / "defects.toml"
    p.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
        + '\n[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert main(["ingest", "--config", str(p)]) == 0
    assert json.loads(capsys.readouterr().out)["published_rows"] == 1
    assert main(["inspection-bases", "--config", str(p), "--origin", "AV21"]) == 0
    assert json.loads(capsys.readouterr().out)["groups"][0]["INSPECC"] == 96
