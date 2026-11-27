import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import lista_ar as ar
from ingesta_delta_lake.ar_control import ArControl
from ingesta_delta_lake.ar_storage import ArStore, load_references
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import ConfigurationError, IngestaError, IntegrityError
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot


@pytest.fixture
def config(config):
    return replace(config, family="LISTA_AR", timestamp_policy="local_naive_test_v1")


@pytest.fixture
def dictionaries(config):
    texts = {
        "SIP_DEFECTO": "501;129;129: Pliegue cuello;Pliegue;0;\n",
        "SIP_DEFECTO_STATUS": "21;1;Tiempo Real;1;TR;2;1;\n22;2;Muestreo IFA;0;MF;1;1;\n",
        "SIP_DEFECTO_AR": "71;3;Aviso ZF;2;\n",
    }
    paths = {}
    for family, text in texts.items():
        p = config.input_root / "AV21" / f"{family}.csv"
        p.write_text(text, encoding="utf-8")
        assert run(replace(config, family=family))["status"] == "APPLIED"
        paths[family] = p
    return paths


def line(
    code=129,
    on="14/10/2026 9:59:33",
    machine=21,
    cavity="2F",
    status=1,
    kind=3,
    model="2031/487",
    off="",
    color="O",
    process="PS",
    mold=34,
    group=39954,
):
    return (
        ";".join(
            map(
                str,
                [code, on, machine, cavity, status, kind, model, off, color, process, mold, group],
            )
        )
        + ";\n"
    )


@pytest.fixture
def source(config, dictionaries):
    p = config.input_root / "AV21" / "LISTA_AR.csv"
    p.write_text(line(), encoding="utf-8")
    return p


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def rows(config):
    control_id = query(config, "SELECT value FROM settings WHERE key='control_id'")[0][0]
    return ArStore(config, control_id).read()[1]


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
    return ar.parse(
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


def test_twelve_fields_order_nulls_and_reference_codes_not_ids(config, source):
    source.write_text(line(cavity="", group=""), encoding="utf-8")
    result = run(config)
    assert result["complete"] and result["published_records"] == 1
    table = DeltaTable(str(config.table_path)).to_pyarrow_table()
    assert table.schema == ar.SCHEMA and table.column_names == [*ar.FIELDS, "origen_fenix"]
    row = table.to_pylist()[0]
    assert row["CAVIDAD"] is None and row["ID_Agrupación"] is None and row["FECHA_OFF"] is None
    refs, manifest = load_references(config)
    assert refs["SIP_DEFECTO"]["codes"][129]["id_defecto_fenix"] == 501
    assert refs["SIP_DEFECTO_STATUS"]["codes"][1]["descripcion"] == "Tiempo Real"
    assert refs["SIP_DEFECTO_AR"]["codes"][3]["descripcion"] == "Aviso ZF"
    assert len(manifest) == 3 and not replace(config, family="SIP_MODELO").table_path.exists()


def test_group_39954_preserves_two_segments_and_never_computes_minutes(config, source):
    source.write_text(
        line(off="14/10/2026 10:33:51")
        + line(on="14/10/2026 10:33:51", off="14/10/2026 11:13:43", status=2),
        encoding="utf-8",
    )
    result = run(config)
    assert result["published_records"] == 2 and result["inserted_this_attempt"] == 2
    values = sorted(rows(config).values(), key=lambda r: r["FECHA_ON"])
    assert [r["STATUS"] for r in values] == [1, 2]
    assert values[0]["FECHA_OFF"] == values[1]["FECHA_ON"]
    assert all(r["ID_Agrupación"] == 39954 for r in values)
    assert set(values[0]) == set(ar.SCHEMA.names)


def test_group_and_status_are_not_unique_and_null_group_rows_remain_distinct(config, source):
    source.write_text(
        line(status=2, group=19607, off="14/10/2026 10:33:51")
        + line(status=2, group=19607, on="14/10/2026 10:33:51", off="14/10/2026 11:13:43")
        + "".join(line(group="", cavity=f"{i}F") for i in range(5)),
        encoding="utf-8",
    )
    result = run(config)
    assert result["published_records"] == 7 and result["published_without_group"] == 5


@pytest.mark.parametrize("cavity,group", [("2F", 39954), ("", 39954), ("2F", ""), ("", "")])
def test_merge_closes_once_with_null_equal_keys_without_reopening(config, source, cavity, group):
    source.write_text(line(cavity=cavity, group=group), encoding="utf-8")
    run(config)
    original = query(config, "SELECT after_json FROM ar_intents WHERE kind='INSERT'")
    source.write_text(line(cavity=cavity, group=group, off="14/10/2026 10:33:51"), encoding="utf-8")
    closed = run(config)
    assert closed["delta_version"] == 1 and closed["updated_this_attempt"] == 1
    assert closed["published_records"] == 1 and closed["inserted_this_attempt"] == 0
    assert query(config, "SELECT after_json FROM ar_intents WHERE kind='INSERT'") == original
    before = files(config)
    source.write_text(line(cavity=cavity, group=group), encoding="utf-8")
    assert run(config)["status"] == "NO_CHANGE" and files(config) == before
    assert next(iter(rows(config).values()))["FECHA_OFF"] == "2026-10-14T10:33:51"
    assert query(config, "SELECT kind,state,COUNT(*) FROM ar_intents GROUP BY kind,state") == [
        ("CLOSE", "CONFIRMED", 1),
        ("INSERT", "CONFIRMED", 1),
    ]


@pytest.mark.parametrize("reverse", [False, True])
def test_open_and_closed_variants_consolidate_independent_of_order(config, source, reverse):
    values = [line(), line(off="14/10/2026 10:33:51")]
    source.write_text("".join(reversed(values) if reverse else values), encoding="utf-8")
    result = run(config)
    assert result["inserted_this_attempt"] == 1 and result["updated_this_attempt"] == 0
    assert next(iter(rows(config).values()))["FECHA_OFF"] == "2026-10-14T10:33:51"


def test_two_different_closures_preserve_accepted_and_candidate_in_review(config, source):
    source.write_text(line(off="14/10/2026 10:33:51"), encoding="utf-8")
    run(config)
    before = files(config)
    source.write_text(line(off="14/10/2026 11:33:51"), encoding="utf-8")
    result = run(config)
    assert result["status"] == "PARTIAL" and result["updated_this_attempt"] == 0
    assert files(config) == before
    case = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert (
        case["reason"] == "CLOSURE_CONFLICT"
        and case["accepted"]["FECHA_OFF"] == "2026-10-14T10:33:51"
    )
    assert case["candidates"][0]["FECHA_OFF"] == "2026-10-14T11:33:51"
    assert case["changed_fields"] == ["FECHA_OFF"] and case["evidence"][0]["raw"]
    assert all(f in case["candidates"][0] for f in ar.FIELDS)
    assert run(config)["updated_this_attempt"] == 0
    assert query(config, "SELECT COUNT(*) FROM issues") == [(1,)]


def test_initial_closure_ambiguity_persists_when_variant_disappears(config, source):
    source.write_text(
        line(off="14/10/2026 10:33:51") + line(off="14/10/2026 11:33:51"), encoding="utf-8"
    )
    assert run(config)["status"] == "BLOCKED" and not config.table_path.exists()
    source.write_text(line(off="14/10/2026 10:33:51"), encoding="utf-8")
    assert run(config)["status"] == "BLOCKED" and not config.table_path.exists()


def test_single_merge_combines_close_and_new_segments_without_crossing_origins(config, source):
    source.write_text(line(cavity="", group=""), encoding="utf-8")
    run(config)
    source.write_text(
        line(cavity="", group="", off="14/10/2026 10:33:51")
        + line(cavity="", group="", on="14/10/2026 10:33:51", status=2),
        encoding="utf-8",
    )
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "LISTA_AR.csv").write_text(line(cavity="", group=""), encoding="utf-8")
    selected = replace(config, sources=(*config.sources, ("AV22", other)))
    result = run(selected)
    assert result["delta_version"] == 1 and result["published_records"] == 3
    assert result["inserted_this_attempt"] == 2 and result["updated_this_attempt"] == 1
    assert sum(r["FECHA_OFF"] is not None for r in rows(selected).values()) == 1
    original = files(selected)
    assert run(selected)["status"] == "NO_CHANGE" and files(selected) == original


def test_all_identity_components_distinguish_independent_segments():
    base = {
        "origen_fenix": "AV21",
        "COD_AR": 129,
        "FECHA_ON": "2026-10-14T09:59:33",
        "MAQ_IS": 21,
        "CAVIDAD": "2F",
        "STATUS": 1,
        "TIPO": 3,
        "MODELO": "2031/487",
        "FECHA_OFF": None,
        "COLOR": "O",
        "PROCESO": "PS",
        "MOLDE": 34,
        "ID_Agrupación": 39954,
    }
    replacements = {
        "origen_fenix": "AV22",
        "COD_AR": 131,
        "FECHA_ON": "2026-10-14T10:00:00",
        "MAQ_IS": 22,
        "CAVIDAD": None,
        "STATUS": 2,
        "TIPO": 4,
        "MODELO": "0001/A01",
        "COLOR": "M",
        "PROCESO": "SS",
        "MOLDE": 35,
        "ID_Agrupación": None,
    }
    candidates = [base, *({**base, f: v} for f, v in replacements.items())]
    assert len({ar.key(r) for r in candidates}) == 13
    assert ar.key(base) == ar.key({**base, "FECHA_OFF": "2026-10-14T10:33:51"})
    assert ar.business_hash(base) != ar.business_hash({**base, "FECHA_OFF": "2026-10-14T10:33:51"})


@pytest.mark.parametrize(
    "index,value",
    [
        (0, "129.0"),
        (0, ""),
        (2, "9223372036854775808"),
        (4, "1.5"),
        (5, "x"),
        (6, ""),
        (8, " "),
        (9, ""),
        (10, "34.0"),
        (11, "null"),
        (1, "29/02/2026 09:00:00"),
        (7, "14/10/2026 08:00:00"),
        (7, "BAD"),
    ],
)
def test_bad_types_dates_and_empty_required_fields_keep_raw(tmp_path, index, value):
    raw = line().rstrip("\n").split(";")
    raw[index] = value
    result = parsed(tmp_path, ";".join(raw) + "\n")
    assert result.invalid_rows == 1 and not result.entries
    assert result.issues[0]["evidence"]["raw"] == raw
    assert result.issues[0]["column"] == ar.FIELDS[index]


def test_text_preserves_model_letters_leading_zeros_and_literal_cavity_null(tmp_path):
    result = parsed(
        tmp_path, line(model=" 0001/A01 ", cavity="null", color=" Azul ", process=" PS ", group="")
    )
    entry = next(iter(result.entries))
    row = dict(zip(("origen_fenix", *ar.FIELDS), entry, strict=True))
    assert row["MODELO"] == "0001/A01" and row["CAVIDAD"] == "null"
    assert row["COLOR"] == "Azul" and row["PROCESO"] == "PS" and row["ID_Agrupación"] is None


@pytest.mark.parametrize(
    "text", ["1;2;3\n", line().rstrip("\n") + "EXTRA;\n", '"UNCLOSED\n', b"\xff"]
)
def test_structure_encoding_and_csv_errors_are_explicit(tmp_path, text):
    result = parsed(tmp_path, text)
    assert result.issues and not result.entries
    assert result.issues[0]["reason"] in {"STRUCTURE", "ENCODING", "CSV"}


def test_null_only_file_is_discarded_without_review(tmp_path):
    result = parsed(tmp_path, b"\x00" * 80)
    assert result.issues == [] and not result.entries


def test_equivalent_dates_names_repetition_and_absent_rows_do_not_write(config, source):
    source.write_text(line() * 5, encoding="utf-8")
    renamed = source.with_name("16102026_92029_LISTA_AR.csv")
    renamed.write_bytes(source.read_bytes())
    first = run(config)
    assert (
        first["source_files"] == 2
        and first["rows_read"] == 5
        and first["consolidated_repetitions"] == 4
    )
    before = files(config)
    source.write_text(line(on="14/10/2026 09:59:33"), encoding="utf-8")
    assert run(config)["status"] == "NO_CHANGE" and files(config) == before
    renamed.unlink()
    source.write_bytes(b"")
    assert run(config)["published_records"] == 1 and files(config) == before


def test_invalid_close_blocks_same_identity_but_independent_segment_can_publish(config, source):
    source.write_text(line() + line(off="BAD") + line(cavity="3F"), encoding="utf-8")
    result = run(config)
    assert result["status"] == "PARTIAL" and result["invalid_rows"] == 1
    assert result["published_records"] == 1 and next(iter(rows(config).values()))["CAVIDAD"] == "3F"


def test_missing_dictionary_or_code_waits_and_recovers_without_live_event(config, dictionaries):
    source = config.input_root / "AV21" / "LISTA_AR.csv"
    source.write_text(line(code=131), encoding="utf-8")
    result = run(config)
    assert result["status"] == "WAITING_DEPENDENCIES" and result["open_dependencies"] == 1
    assert not config.table_path.exists()
    source.write_bytes(b"")
    p = dictionaries["SIP_DEFECTO"]
    p.write_text(
        p.read_text(encoding="utf-8") + "502;131;131: Otro defecto;Otro;0;\n", encoding="utf-8"
    )
    run(replace(config, family="SIP_DEFECTO"))
    result = run(config)
    assert result["status"] == "APPLIED" and result["open_dependencies"] == 0
    assert result["restored_dependencies"] == 1 and result["published_records"] == 1
    assert query(config, "SELECT state FROM batches ORDER BY created_at DESC LIMIT 1") == [
        ("COMPLETED",)
    ]
    assert query(config, "SELECT COUNT(*) FROM dependency_events WHERE event='RESOLVED'") == [(1,)]


def test_all_missing_dictionaries_wait_without_creating_dictionary_controls(config):
    p = config.input_root / "AV21" / "LISTA_AR.csv"
    p.write_text(line(), encoding="utf-8")
    result = run(config)
    assert result["status"] == "WAITING_DEPENDENCIES" and result["open_dependencies"] == 1
    payload = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    assert {r["field"] for r in payload["references"]} == {"COD_AR", "STATUS", "TIPO"}
    assert not replace(config, family="SIP_DEFECTO").control_path.exists()


def test_dependency_escalation_never_discards_or_fabricates_event(config, dictionaries):
    source = config.input_root / "AV21" / "LISTA_AR.csv"
    source.write_text(line(code=131), encoding="utf-8")
    run(config)
    with sqlite3.connect(config.control_path) as db:
        db.execute("UPDATE issues SET first_seen='2026-01-01T00:00:00+00:00'")
    result = run(replace(config, pending_review_after_hours=24))
    assert result["escalated_dependencies"] == 1 and result["oldest_dependency_hours"] > 24
    assert result["published_records"] == 0 and result["restored_dependencies"] == 1


def test_dictionary_review_marks_even_absent_accepted_event_as_incomplete(
    config, source, dictionaries
):
    run(config)
    before = files(config)
    source.write_bytes(b"")
    dictionaries["SIP_DEFECTO"].write_text(
        "501;129;Changed description;Pliegue;0;\n", encoding="utf-8"
    )
    assert run(replace(config, family="SIP_DEFECTO"))["status"] == "BLOCKED"
    result = run(config)
    assert result["status"] == "PARTIAL" and result["accepted_reference_pending"] == 1
    assert not result["complete"] and files(config) == before


def test_dictionary_conflict_blocks_only_affected_codes_and_keeps_replay_idempotent(
    config, source, dictionaries
):
    path = dictionaries["SIP_DEFECTO"]
    path.write_text(path.read_text(encoding="utf-8") + "502;112;Otro;Otro;0;\n", encoding="utf-8")
    dictionary = replace(config, family="SIP_DEFECTO")
    assert run(dictionary)["status"] == "APPLIED"
    path.write_text("501;129;Conflicto;Pliegue;0;\n", encoding="utf-8")
    assert run(dictionary)["status"] == "BLOCKED"
    source.write_text(line(code=129) + line(code=112, cavity="3D"), encoding="utf-8")
    first = run(config)
    assert first["status"] == "PARTIAL"
    assert not first["complete"] and first["inserted_this_attempt"] == 1
    assert [r["COD_AR"] for r in rows(config).values()] == [112]
    gap = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])["references"][0]
    assert gap["code"] == 129 and gap["cause"] == "DICTIONARY_REVIEW"
    assert len(gap["dictionary_issue_ids"]) == 1
    again = run(config)
    assert again["delta_version"] == first["delta_version"]
    assert again["inserted_this_attempt"] == 0
    with sqlite3.connect(dictionary.control_path) as db:
        db.execute("UPDATE issues SET status='RESOLVED'")
    recovered = run(config)
    assert recovered["complete"] and recovered["inserted_this_attempt"] == 1
    assert {r["COD_AR"] for r in rows(config).values()} == {112, 129}


def test_open_event_before_t0_is_kept_but_finished_event_is_not(config, source, monkeypatch):
    monkeypatch.setenv("FENIX_LIVE_T0", "2026-10-14T10:20:00")
    source.write_text(
        line(cavity="3D") + line(cavity="2D", off="14/10/2026 10:00:00"), encoding="utf-8"
    )
    assert run(config)["inserted_this_attempt"] == 1
    assert [r["CAVIDAD"] for r in rows(config).values()] == ["3D"]


@pytest.mark.parametrize("phase", ["before", "after", "ack"])
@pytest.mark.parametrize("closing", [False, True])
def test_insert_and_close_recovery_confirms_once(config, source, monkeypatch, phase, closing):
    if closing:
        source.write_text(line(cavity="", group=""), encoding="utf-8")
        run(config)
        source.write_text(line(cavity="", group="", off="14/10/2026 10:33:51"), encoding="utf-8")
    original = ArStore.apply_changes

    def fail(self, *args):
        if phase == "after":
            original(self, *args)
        raise OSError("simulated")

    with monkeypatch.context() as patch:
        if phase == "ack":
            patch.setattr(ArControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        else:
            patch.setattr(ArStore, "apply_changes", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["published_records"] == 1 and result["delta_version"] == int(closing)
    assert result["recovered_operations"][0]["wrote_this_attempt"] == (phase == "before")
    assert query(config, "SELECT COUNT(*) FROM ar_intents WHERE state!='CONFIRMED'") == [(0,)]
    assert query(config, "SELECT COUNT(*) FROM ar_intents") == [(2 if closing else 1,)]


def test_recovery_pins_reference_versions_even_if_dictionaries_advance(
    config, source, dictionaries, monkeypatch
):
    with monkeypatch.context() as patch:
        patch.setattr(ArStore, "apply_changes", lambda *_: (_ for _ in ()).throw(OSError("before")))
        with pytest.raises(IngestaError):
            run(config)
    p = dictionaries["SIP_DEFECTO"]
    p.write_text(p.read_text(encoding="utf-8") + "502;131;131: Otro;Otro;0;\n", encoding="utf-8")
    run(replace(config, family="SIP_DEFECTO"))
    source.write_text(line(cavity="3F"), encoding="utf-8")
    result = run(config)
    assert (
        result["published_records"] == 2
        and result["reference_snapshots"]["SIP_DEFECTO"]["version"] == 1
    )
    assert result["recovered_operations"]


def test_tampered_dependency_bronze_stops_processing(config, source):
    source.write_text(line(code=131), encoding="utf-8")
    run(config)
    payload = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    Path(payload["evidence"][0]["bronze_path"]).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        run(config)


def test_pending_dictionary_commit_requires_recovery(config, source, dictionaries, monkeypatch):
    from ingesta_delta_lake.control import Control

    p = dictionaries["SIP_DEFECTO"]
    p.write_text(p.read_text(encoding="utf-8") + "502;131;131: Otro;Otro;0;\n", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(Control, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        with pytest.raises(IngestaError):
            run(replace(config, family="SIP_DEFECTO"))
    with pytest.raises(IntegrityError, match="Recuperar primero"):
        run(config)


def test_empty_source_no_empty_table_and_explicit_timestamp_policy(config, dictionaries):
    p = config.input_root / "AV21" / "LISTA_AR.csv"
    p.write_bytes(b"")
    assert run(config)["status"] == "NO_CHANGE" and not config.table_path.exists()
    with pytest.raises(ConfigurationError):
        replace(config, timestamp_policy=None).validate()


def test_cli_ingest(config, source, tmp_path, capsys):
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
    p = tmp_path / "ar.toml"
    p.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
        + '\n[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert main(["ingest", "--config", str(p)]) == 0
    assert json.loads(capsys.readouterr().out)["published_records"] == 1
