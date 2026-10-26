import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import mp9
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import ConfigurationError, IngestaError, IntegrityError
from ingesta_delta_lake.mp9_control import Mp9Control
from ingesta_delta_lake.mp9_storage import Mp9Store
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot


@pytest.fixture
def config(config):
    return replace(config, family="MP9_CAMBIO_MODELO", timestamp_policy="local_naive_test_v1")


@pytest.fixture
def models(config):
    path = config.input_root / "AV21" / "SIP_MODELO.csv"
    path.write_text("754;3081/360;1;\n800;TEST/001;1;\n", encoding="utf-8")
    run(replace(config, family="SIP_MODELO"))
    return path


def line(
    identifier=1,
    model=754,
    on="01/10/2026 6:00:00",
    off="",
    zone=1,
    rel="",
    ok=1,
    machine=7,
    preparation=100,
    n_mod=1,
):
    return f"{identifier};{model};{on};{off};{machine};{preparation};{zone};{rel};{n_mod};{ok};\n"


@pytest.fixture
def source(config, models):
    path = config.input_root / "AV21" / "MP9_CAMBIO_MODELO.csv"
    path.write_text(line() + line(2, zone=2, rel=1, on="01/10/2026 7:00:00"), encoding="utf-8")
    return path


def rows(config):
    return Mp9Store(
        config, query(config, "SELECT value FROM settings WHERE key='control_id'")[0][0]
    ).read()[1]


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def files(config):
    return {
        str(p.relative_to(config.table_path)): p.read_bytes()
        for p in config.table_path.rglob("*")
        if p.is_file()
    }


def parsed(tmp_path, text, encoding="utf-8-sig"):
    path = tmp_path / "snapshot.csv"
    data = text.encode("utf-8")
    path.write_bytes(data)
    return mp9.parse(
        [
            Snapshot(
                "AV21",
                str(path),
                hashlib.sha256(data).hexdigest(),
                str(path),
                len(data),
                0,
                "2026-10-16T10:00:00Z",
                None,
            )
        ],
        encoding,
    )


def test_initial_schema_order_nullable_dates_and_replay(config, source):
    result = run(config)
    assert result["status"] == "APPLIED" and result["complete"]
    assert result["inserted_this_attempt"] == 2 and result["updated_this_attempt"] == 0
    arrow = DeltaTable(str(config.table_path)).to_pyarrow_table()
    assert arrow.schema == mp9.SCHEMA
    assert arrow.column_names == [*mp9.FIELDS, "origen_fenix"]
    assert rows(config)[("AV21", 2)]["ID_ST1_REL"] == 1
    assert rows(config)[("AV21", 1)]["FECHA_OFF"] is None
    original = files(config)
    replay = run(config)
    assert replay["status"] == "NO_CHANGE" and replay["matched_entries"] == 2
    assert replay["delta_version"] == 0 and files(config) == original
    assert query(config, "SELECT kind,state,COUNT(*) FROM change_intents GROUP BY kind,state") == [
        ("INSERT", "CONFIRMED", 2)
    ]


@pytest.mark.parametrize("reverse", [False, True])
def test_open_and_unique_closed_variants_consolidate_independent_of_order(config, source, reverse):
    values = [line(), line(off="14/10/2026 6:23:00")]
    source.write_text("".join(reversed(values) if reverse else values), encoding="utf-8")
    assert run(config)["inserted_this_attempt"] == 1
    assert rows(config)[("AV21", 1)]["FECHA_OFF"] == "2026-10-14T06:23:00"


def test_close_only_updates_off_and_preserves_insert_audit_and_old_snapshot(config, source):
    run(config)
    first_intents = query(config, "SELECT after_json FROM change_intents WHERE kind='INSERT'")
    source.write_text(
        line(off="14/10/2026 6:23:00")
        + line(2, zone=2, rel=1, on="01/10/2026 7:00:00", off="14/10/2026 7:23:14"),
        encoding="utf-8",
    )
    result = run(config)
    assert result["updated_this_attempt"] == 2 and result["delta_version"] == 1
    assert result["inserted_this_attempt"] == 0
    assert rows(config)[("AV21", 2)]["FECHA_OFF"] == "2026-10-14T07:23:14"
    assert (
        query(config, "SELECT after_json FROM change_intents WHERE kind='INSERT'") == first_intents
    )
    audits = query(
        config,
        "SELECT before_json,after_json,before_hash,after_hash "
        "FROM change_intents WHERE kind='CLOSE'",
    )
    assert len(audits) == 2
    assert all(
        json.loads(before)["FECHA_OFF"] is None and before_hash != after_hash
        for before, after, before_hash, after_hash in audits
    )
    assert all(
        r["FECHA_OFF"] is None
        for r in DeltaTable(str(config.table_path), version=0).to_pyarrow_table().to_pylist()
    )
    old = files(config)
    source.write_text(line() + line(2, zone=2, rel=1, on="01/10/2026 7:00:00"), encoding="utf-8")
    assert run(config)["status"] == "NO_CHANGE"
    assert files(config) == old and rows(config)[("AV21", 1)]["FECHA_OFF"] is not None


def test_ignored_columns_equivalent_dates_and_new_names_do_not_write(config, source, monkeypatch):
    run(config)
    original = files(config)
    monkeypatch.setattr(Mp9Store, "apply_changes", lambda *_: pytest.fail("No debe escribir"))
    source.write_text(line(on="01/10/2026 06:00:00", preparation=88, n_mod=7), encoding="utf-8")
    assert run(config)["status"] == "NO_CHANGE"
    source.rename(source.with_name("16102026_92028_MP9_CAMBIO_MODELO.csv"))
    assert run(config)["status"] == "NO_CHANGE" and files(config) == original


@pytest.mark.parametrize(
    "kwargs,field",
    [
        ({"model": 800}, "ID_MODELO"),
        ({"on": "02/10/2026 6:00:00"}, "FECHA_ON"),
        ({"zone": 2}, "STATUS"),
        ({"rel": 99}, "ID_ST1_REL"),
        ({"machine": 99}, "ID_MAQUINA"),
    ],
)
def test_changed_invariants_are_reviewed_without_overwriting(config, source, kwargs, field):
    run(config)
    before = rows(config)[("AV21", 1)]
    source.write_text(line(**kwargs) + line(9, model=800), encoding="utf-8")
    result = run(config)
    assert result["status"] == "PARTIAL" and not result["complete"]
    assert result["inserted_this_attempt"] == 1 and result["conflict_entries"] == 1
    assert rows(config)[("AV21", 1)] == before
    issue = json.loads(
        query(config, "SELECT payload_json FROM issues WHERE reason='CONFLICT'")[0][0]
    )
    assert issue["changed_fields"] == [field] and issue["accepted"] == before
    assert issue["candidates"] and issue["evidence"][0]["raw"]


def test_two_distinct_closures_are_never_resolved_by_min_max_or_order(config, source):
    source.write_text(
        line(off="14/10/2026 6:00:00") + line(off="14/10/2026 7:00:00"), encoding="utf-8"
    )
    assert run(config)["status"] == "BLOCKED" and not config.table_path.exists()
    source.write_text(line(off="14/10/2026 7:00:00"), encoding="utf-8")
    assert run(config)["status"] == "BLOCKED"  # Requiere una decisión, no olvidar el conflicto.
    assert not config.table_path.exists()


def test_ok_progression_accepts_new_id_and_its_dependent_zf(config, source):
    source.write_text(
        line(ok=0) + line(ok=1) + line(2, zone=2, rel=1) + line(9, model=800), encoding="utf-8"
    )
    result = run(config)
    assert result["status"] == "APPLIED" and result["published_records"] == 3
    assert result["conflict_entries"] == 0 and result["open_dependencies"] == 0
    assert rows(config)[("AV21", 1)]["ZF_OK"] == 1
    assert ("AV21", 2) in rows(config)
    assert rows(config)[("AV21", 9)]["FECHA_OFF"] is None  # ZF_OK=1 no inventa cierre.
    source.write_text(line(ok=1), encoding="utf-8")
    assert run(config)["complete"] is True and ("AV21", 1) in rows(config)


@pytest.mark.parametrize("zone,rel,model", [(2, 1, 754), (1, 1, 800), (1, 2, 754)])
def test_wrong_zone_model_or_self_reference_is_a_conflict(config, source, zone, rel, model):
    source.write_text(line(zone=zone, model=model) + line(2, zone=2, rel=rel), encoding="utf-8")
    result = run(config)
    assert not result["complete"]
    assert query(config, "SELECT COUNT(*) FROM issues WHERE reason='RELATION_CONFLICT'")[0][0] >= 1
    assert ("AV21", 2) not in rows(config)


def test_missing_zc_is_retried_from_bronze_even_if_current_csv_omits_it(config, source):
    source.write_text(line(2, zone=2, rel=1), encoding="utf-8")
    first = run(config)
    assert first["status"] == "WAITING_DEPENDENCIES" and not config.table_path.exists()
    source.write_text(line(), encoding="utf-8")
    result = run(config)
    assert result["inserted_this_attempt"] == 2 and result["complete"]
    assert result["open_dependencies"] == 0 and result["restored_dependencies"] == 1
    assert query(config, "SELECT status FROM issues") == [("RESOLVED",)]
    assert query(config, "SELECT COUNT(*) FROM dependency_events WHERE event='RESOLVED'") == [(1,)]


def test_missing_model_is_retried_after_dictionary_update_and_csv_disappears(
    config, source, models
):
    source.write_text(line(model=900), encoding="utf-8")
    assert run(config)["status"] == "WAITING_DEPENDENCIES"
    models.write_text("900;FUTURE/001;1;\n", encoding="utf-8")
    run(replace(config, family="SIP_MODELO"))
    source.unlink()
    result = run(config)
    assert result["inserted_this_attempt"] == 1 and result["complete"]
    assert rows(config)[("AV21", 1)]["ID_MODELO"] == 900


def test_no_model_catalog_does_not_invent_one(config):
    path = config.input_root / "AV21" / "MP9_CAMBIO_MODELO.csv"
    path.write_text(line(), encoding="utf-8")
    assert run(config)["status"] == "WAITING_DEPENDENCIES"
    assert not replace(config, family="SIP_MODELO").control_path.exists()


def test_dependency_age_escalates_without_fabricating_data(config, source):
    source.write_text(line(2, zone=2, rel=1), encoding="utf-8")
    config = replace(config, pending_review_after_hours=1.0)
    run(config)
    with sqlite3.connect(config.control_path) as db:
        db.execute("UPDATE issues SET first_seen='2020-01-01T00:00:00+00:00'")
    result = run(config)
    assert result["escalated_dependencies"] == 1 and result["oldest_dependency_hours"] > 1
    assert not config.table_path.exists()
    source.write_text(line(), encoding="utf-8")
    assert run(config)["complete"]


def test_same_ids_in_two_origins_never_cross_link_or_update_each_other(config, source):
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "MP9_CAMBIO_MODELO.csv").write_text(line(model=800), encoding="utf-8")
    selected = replace(config, sources=(*config.sources, ("AV22", other)))
    assert run(selected)["published_records"] == 3
    source.write_text(line(off="14/10/2026 6:00:00"), encoding="utf-8")
    result = run(selected)
    assert result["updated_this_attempt"] == 1
    assert rows(config)[("AV22", 1)]["FECHA_OFF"] is None
    assert rows(config)[("AV21", 1)]["FECHA_OFF"] is not None


def test_cross_origin_reference_does_not_satisfy_missing_local_zc(config, source):
    source.write_text(line(), encoding="utf-8")
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "MP9_CAMBIO_MODELO.csv").write_text(line(2, zone=2, rel=1), encoding="utf-8")
    result = run(replace(config, sources=(*config.sources, ("AV22", other))))
    assert result["published_records"] == 1 and result["open_dependencies"] == 1


@pytest.mark.parametrize(
    "text",
    [
        line(model="1.5"),
        line(identifier="9223372036854775808"),
        line(ok=2),
        line(zone=0),
        line(on="10/31/2026 6:00:00"),
        line(on="29/02/2026 6:00:00"),
        line(on=""),
        line(off="30/09/2026 6:00:00"),
        line(rel="1.5"),
        "1;754;01/10/2026 6:00:00\n",
        line().rstrip("\n") + "EXTRA;\n",
    ],
)
def test_invalid_values_and_structures_preserve_raw(tmp_path, text):
    result = parsed(tmp_path, text)
    assert result.invalid_rows == 1 and not result.entries
    assert result.issues[0]["evidence"]["raw"]


def test_dst_hours_are_preserved_as_local_test_values_not_assumed_utc(tmp_path):
    result = parsed(tmp_path, line(on="25/10/2026 2:30:00"))
    assert next(iter(result.entries))[3] == "2026-10-25T02:30:00"


def test_parser_repetitions_and_literal_csv_failures_keep_full_evidence(tmp_path):
    result = parsed(tmp_path, line() * 5)
    assert result.rows_read == 5 and len(result.entries) == 1
    assert next(iter(result.entries.values()))[0]["duplicate_count"] == 4
    result = parsed(tmp_path, '1;754;"UNCLOSED\n')
    assert result.issues[0]["reason"] == "CSV" and result.issues[0]["raw_text"]


@pytest.mark.parametrize("phase", ["before", "after", "ack"])
@pytest.mark.parametrize("operation", ["insert", "close"])
def test_recover_insert_or_close_without_duplicate_effects(
    config, source, monkeypatch, phase, operation
):
    if operation == "close":
        run(config)
        source.write_text(line(off="14/10/2026 6:23:00"), encoding="utf-8")
    original = Mp9Store.apply_changes

    def fail(self, *args):
        if phase == "after":
            original(self, *args)
        raise OSError("Simulated failure")

    with monkeypatch.context() as patch:
        if phase == "ack":
            patch.setattr(Mp9Control, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        else:
            patch.setattr(Mp9Store, "apply_changes", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["recovered_operations"][0]["wrote_this_attempt"] == (phase == "before")
    assert result["delta_version"] == (1 if operation == "close" else 0)
    assert len(rows(config)) == 2
    assert query(config, "SELECT COUNT(*) FROM change_intents WHERE state!='CONFIRMED'") == [(0,)]
    if operation == "close":
        assert rows(config)[("AV21", 1)]["FECHA_OFF"] == "2026-10-14T06:23:00"
        assert query(config, "SELECT COUNT(*) FROM change_intents WHERE kind='CLOSE'") == [(1,)]


def test_empty_inputs_and_config_require_explicit_local_test_policy(config, models):
    assert run(config)["status"] == "NO_INPUTS" and not config.table_path.exists()
    for changes in (
        {"timestamp_policy": None},
        {"timestamp_policy": "UTC"},
        {"pending_review_after_hours": -1},
        {"pending_review_after_hours": float("nan")},
    ):
        with pytest.raises(ConfigurationError):
            replace(config, **changes).validate()


def test_altered_dependency_bronze_blocks_retry(config, source):
    source.write_text(line(2, zone=2, rel=1), encoding="utf-8")
    run(config)
    payload = json.loads(query(config, "SELECT payload_json FROM issues")[0][0])
    Path(payload["evidence"][0]["bronze_path"]).write_bytes(b"changed")
    source.write_text(line(), encoding="utf-8")
    with pytest.raises(IntegrityError):
        run(config)
    assert not config.table_path.exists()


def test_cli_reports_partial_as_incomplete(config, source, tmp_path, capsys):
    source.write_text(line(machine=7) + line(machine=8) + line(9, model=800), encoding="utf-8")
    fields = {
        "plant": config.plant,
        "family": config.family,
        "timestamp_policy": config.timestamp_policy,
    }
    fields.update(
        {
            k: str(getattr(config, k))
            for k in ("input_root", "bronze_root", "silver_root", "control_root")
        }
    )
    file = tmp_path / "mp9.toml"
    file.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in fields.items())
        + '\n[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert main(["ingest", "--config", str(file)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PARTIAL" and not result["complete"]


def test_invalid_variant_prevents_publishing_an_alternative_of_the_same_id(config, source):
    source.write_text(line() + line(off="impossible"), encoding="utf-8")
    result = run(config)
    assert result["status"] == "BLOCKED" and not config.table_path.exists()
    assert result["invalid_rows"] == 1


def test_model_table_without_its_control_is_not_adopted(config, source):
    selected = replace(config, control_root=config.control_root.with_name("other-control"))
    with pytest.raises(IntegrityError, match="Falta el control"):
        run(selected)
    assert not config.table_path.exists()


def test_invalid_direct_closure_is_rejected_before_merge(config, source):
    run(config)
    control_id = query(config, "SELECT value FROM settings WHERE key='control_id'")[0][0]
    store = Mp9Store(config, control_id)
    version, accepted = store.read()
    before = accepted[("AV21", 1)]
    candidate = {**before, "before": before, "ID_MODELO": 800, "FECHA_OFF": "2026-10-14T06:00:00"}
    with pytest.raises(IntegrityError, match="cierre"):
        store.apply_changes([], [candidate], "bad-operation", version)
    assert store.read()[0] == version
