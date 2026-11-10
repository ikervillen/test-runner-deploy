import hashlib
import json
import sqlite3
from dataclasses import replace
from decimal import ROUND_UP, Decimal, localcontext
from pathlib import Path

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import cont_zc as zc
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import ConfigurationError, IngestaError, IntegrityError
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot
from ingesta_delta_lake.zc_service import yield_report
from ingesta_delta_lake.zc_storage import ZcControl, ZcStore


@pytest.fixture
def config(config):
    return replace(config, family="CONT_ZC", timestamp_policy="local_naive_test_v1")


def line(**changes):
    values = dict(
        zip(
            zc.FIELDS,
            [
                23,
                "14/10/2026 0:00:00",
                1614,
                0,
                1614,
                0,
                0,
                1583,
                0,
                1614,
                0,
                1613,
                0,
                "80.01",
                "0.00",
                0,
                1,
            ],
            strict=True,
        )
    )
    values.update(changes)
    return ";".join(str(values[f]) for f in zc.FIELDS) + ";\n"


@pytest.fixture
def source(config):
    p = config.input_root / "AV21" / "CONT_ZC.csv"
    p.write_text(line(), encoding="utf-8")
    return p


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def rows(config):
    control_id = query(config, "SELECT value FROM settings WHERE key='control_id'")[0][0]
    return ZcStore(config, control_id).read()[1]


def files(config):
    return {
        str(p.relative_to(config.table_path)): p.read_bytes()
        for p in config.table_path.rglob("*")
        if p.is_file()
    }


def parsed(tmp_path, text):
    p = tmp_path / "snapshot.csv"
    data = text.encode("utf-8") if isinstance(text, str) else text
    p.write_bytes(data)
    return zc.parse(
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
        "utf-8-sig",
    )


def test_all_17_fields_and_decimal_arrow_schema_preserve_original_quantities(config, source):
    result = run(config)
    assert result["status"] == "APPLIED" and result["complete"]
    table = DeltaTable(str(config.table_path)).to_pyarrow_table()
    assert table.schema == zc.SCHEMA and table.column_names == [*zc.FIELDS, "origen_fenix"]
    row = table.to_pylist()[0]
    assert row["VEL_A"] == Decimal("80.01") and row["VEL_B"] == Decimal("0.00")
    assert row["RUEDA"] == 1583 and row["EXPULSOR"] == 1613 and row["SALIDA_MAQ_IS"] == 1
    assert result["yield_summary"]["rate_percent"] == "98.0793"
    assert result["yield_summary"]["eligible_wheel"] == 1583
    assert result["yield_summary"]["rate_numerator"] == 158300


@pytest.mark.parametrize(
    "token,expected",
    [
        ("80.010", "80.01"),
        ("80", "80.00"),
        ("80.00", "80.00"),
        ("+00080.01000", "80.01"),
        ("0.00100", None),
        ("0.0000", "0.00"),
        ("-0.00", "0.00"),
        ("9999999999.9900", "9999999999.99"),
        ("10000000000.00", None),
        ("80.011", None),
        ("-1.00", None),
        ("NaN", None),
        ("Infinity", None),
        ("1e2", None),
        ("80,01", None),
        ("", None),
        (".5", None),
    ],
)
def test_decimal_precision_scale_and_equivalence_without_rounding(token, expected):
    if expected is None:
        with pytest.raises(ValueError):
            zc.speed(token)
    else:
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_UP
            assert zc.speed(token) == expected


def test_normalized_speed_dates_and_replay_never_call_writer(config, source, monkeypatch):
    run(config)
    before = files(config)
    monkeypatch.setattr(ZcStore, "insert", lambda *_: pytest.fail("Escritura sin novedades"))
    source.write_text(
        line(FECHA="14/10/2026 00:00:00", VEL_A="080.01000", VEL_B="-0.000"), encoding="utf-8"
    )
    assert run(config)["status"] == "NO_CHANGE" and files(config) == before
    assert query(config, "SELECT COUNT(*) FROM measurement_intents") == [(1,)]


def test_formula_sums_same_eligible_set_not_mean_or_counter_differences(config, source):
    source.write_text(
        line(RUEDA=1, TEORICO_A=2, EXPULSOR=100)
        + line(FECHA="14/10/2026 0:10:00", RUEDA=9, TEORICO_A=10, TEORICO_B=0, SALIDA_MAQ_IS=0)
        + line(FECHA="14/10/2026 0:20:00", RUEDA=999, TEORICO_A=0, TEORICO_B=0),
        encoding="utf-8",
    )
    result = run(config)
    summary = result["yield_summary"]
    assert result["published_records"] == 3 and result["complete"]
    assert summary["rate_percent"] == "83.3333" and summary["eligible_wheel"] == 10
    assert summary["eligible_theoretical"] == 12 and summary["eligible_observations"] == 2
    assert (
        summary["excluded_zero_theoretical"] == 1
        and summary["excluded_zero_theoretical_wheel"] == 999
    )


def test_zero_theoretical_and_unused_counters_are_kept_without_fake_percentage(config, source):
    source.write_text(
        line(
            TEORICO_A=0,
            TEORICO_B=0,
            RUEDA=3,
            TUNEL=7,
            IMP_ARCA=11,
            CONT_ZC_3=13,
            CONT_ZC_4=17,
            VEL_A="0",
        ),
        encoding="utf-8",
    )
    result = run(config)
    assert result["complete"] and result["yield_summary"]["rate_percent"] is None
    row = next(iter(rows(config).values()))
    assert (row["TUNEL"], row["IMP_ARCA"], row["CONT_ZC_3"], row["CONT_ZC_4"]) == (7, 11, 13, 17)
    report = yield_report(config)
    assert report["status"] == "NOT_EVALUABLE" and report["source_complete"]
    assert not report["metric_evaluable"] and report["summary"]["eligible_wheel"] == 0


def test_above_100_and_falling_counters_preserved_and_flagged_not_clipped(config, source):
    source.write_text(
        line(RUEDA=150, TEORICO_A=100) + line(FECHA="14/10/2026 0:10:00", RUEDA=80, TEORICO_A=100),
        encoding="utf-8",
    )
    result = run(config)
    assert result["complete"] and result["open_reviews"] == 0
    summary = result["yield_summary"]
    assert summary["rate_percent"] == "115.0000" and summary["above_100_window"]
    assert summary["above_100_observations"] == 1 and summary["eligible_wheel"] == 230


@pytest.mark.parametrize(
    "field,value",
    [
        ("RUEDA", 1500),
        ("TEORICO_B", 1),
        ("EXPULSOR", 1600),
        ("VEL_A", "80.02"),
        ("VEL_B", "0.01"),
        ("SALIDA_MAQ_IS", 0),
        ("CONT_ZC_4", 1),
    ],
)
def test_later_record_for_the_same_minute_replaces_the_earlier_one_without_review(
    config, source, field, value
):
    run(config)
    source.write_text(line(**{field: value}), encoding="utf-8")
    result = run(config)
    stored = next(iter(rows(config).values()))
    assert result["status"] == "APPLIED" and result["inserted_this_attempt"] == 1
    assert stored[field] == value
    assert result["open_reviews"] == 0 and result["yield_summary"]["review_observations"] == 0
    assert query(config, "SELECT COUNT(*) FROM issues WHERE status!='RESOLVED'") == [(0,)]
    assert yield_report(config)["source_complete"] is True


def test_same_file_keeps_the_later_row_and_a_later_file_replaces_it(config, source):
    source.write_text(line() * 3 + line(RUEDA=1500), encoding="utf-8")
    first = run(config)
    assert first["status"] == "APPLIED" and config.table_path.exists()
    assert next(iter(rows(config).values()))["RUEDA"] == 1500
    source.write_text(line(), encoding="utf-8")
    second = run(config)
    assert second["status"] == "APPLIED"
    assert next(iter(rows(config).values()))["RUEDA"] == 1583
    assert second["open_reviews"] == 0


def test_identity_includes_machine_date_origin_even_if_values_equal(config, source):
    source.write_text(line() + line(MAQ_IS=24) + line(FECHA="14/10/2026 0:10:00"), encoding="utf-8")
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "CONT_ZC.csv").write_text(line(), encoding="utf-8")
    selected = replace(config, sources=(*config.sources, ("AV22", other)))
    result = run(selected)
    assert (
        result["published_records"] == 4 and result["yield_summary"]["eligible_wheel"] == 4 * 1583
    )
    assert len(yield_report(selected)["per_machine"]) == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("RUEDA", -1),
        ("CONT_ZC_1", ""),
        ("TEORICO_A", "1.5"),
        ("TEORICO_B", "9223372036854775808"),
        ("TUNEL", -1),
        ("SALIDA_MAQ_IS", 2),
        ("VEL_A", "80.001"),
        ("VEL_B", "-0.01"),
        ("FECHA", "29/02/2026 0:00:00"),
        ("MAQ_IS", "23.0"),
    ],
)
def test_invalid_fields_keep_original_evidence(tmp_path, field, value):
    text = line(**{field: value})
    result = parsed(tmp_path, text)
    assert result.invalid_rows == 1 and not result.entries
    assert result.issues[0]["column"] == field
    assert result.issues[0]["evidence"]["raw"] == text.rstrip("\n").split(";")


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


def test_optional_separator_and_duplicate_files_audit_only_once(config, source, tmp_path):
    assert len(parsed(tmp_path, line().rstrip("\n")[:-1] + "\n").entries) == 1
    source.write_text(line() * 5, encoding="utf-8")
    renamed = source.with_name("16102026_92029_CONT_ZC.csv")
    renamed.write_bytes(source.read_bytes())
    result = run(config)
    assert (
        result["source_files"] == 2
        and result["rows_read"] == 5
        and result["consolidated_repetitions"] == 4
    )
    before = files(config)
    assert run(config)["status"] == "NO_CHANGE" and files(config) == before
    assert query(config, "SELECT COUNT(*) FROM receipts") == [(4,)]


def test_overwritten_file_appends_new_date_and_preserves_absent_old_rows(config, source):
    run(config)
    source.write_text(line(FECHA="14/10/2026 0:10:00"), encoding="utf-8")
    assert run(config)["published_records"] == 2
    source.rename(source.with_name("16102026_92029_CONT_ZC.csv"))
    assert run(config)["status"] == "NO_CHANGE"


def test_invalid_variant_holds_its_key_but_independent_context_publishes(config, source):
    source.write_text(line() + line(VEL_A="BAD") + line(MAQ_IS=24), encoding="utf-8")
    result = run(config)
    assert result["status"] == "PARTIAL" and result["published_records"] == 1
    assert next(iter(rows(config).values()))["MAQ_IS"] == 24


@pytest.mark.parametrize("phase", ["before", "after", "ack"])
def test_recovery_confirms_rows_and_sums_once(config, source, monkeypatch, phase):
    original = ZcStore.insert

    def fail(self, *args):
        if phase == "after":
            original(self, *args)
        raise OSError("simulated")

    with monkeypatch.context() as patch:
        if phase == "ack":
            patch.setattr(ZcControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        else:
            patch.setattr(ZcStore, "insert", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["delta_version"] == 0 and result["published_records"] == 1
    assert result["yield_summary"]["eligible_wheel"] == 1583
    assert result["recovered_operations"][0]["wrote_this_attempt"] == (phase == "before")
    assert query(config, "SELECT state,COUNT(*) FROM measurement_intents GROUP BY state") == [
        ("CONFIRMED", 1)
    ]


def test_recovery_uses_prepared_revision_when_live_file_changes(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(ZcStore, "insert", lambda *_: (_ for _ in ()).throw(OSError("before")))
        with pytest.raises(IngestaError):
            run(config)
    source.write_text(line(FECHA="14/10/2026 0:10:00"), encoding="utf-8")
    result = run(config)
    assert result["published_records"] == 2 and result["inserted_this_attempt"] == 2


def test_unconfirmed_commit_and_tampered_bronze_prevent_query_or_recovery(
    config, source, monkeypatch
):
    with monkeypatch.context() as patch:
        patch.setattr(ZcControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        with pytest.raises(IngestaError):
            run(config)
    with pytest.raises(IntegrityError, match="Recuperar"):
        yield_report(config)
    snap = json.loads(query(config, "SELECT descriptor_json FROM receipts")[0][0])
    Path(snap["bronze_path"]).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        run(config)


def test_read_only_report_uses_sample_ends_open_start_closed_end_without_imputing_gap(
    config, source
):
    source.write_text(
        line() + line(FECHA="14/10/2026 0:10:00") + line(FECHA="14/10/2026 0:30:00"),
        encoding="utf-8",
    )
    run(config)
    before = files(config)
    attempts = query(config, "SELECT COUNT(*) FROM attempts")
    result = yield_report(
        config, origin="AV21", machine=23, start="2026-10-14T00:00:00", end="2026-10-14T00:30:00"
    )
    assert result["status"] == "OK" and result["summary"]["observations"] == 2
    assert result["summary"]["first_sample_end"] == "2026-10-14T00:10:00"
    assert result["summary"]["sample_timestamp_role"] == "period_end"
    assert result["coverage_scope"] == "observed_samples_only_not_guaranteed_continuous"
    assert result["summary"]["eligible_wheel"] == 2 * 1583
    assert files(config) == before and query(config, "SELECT COUNT(*) FROM attempts") == attempts
    assert yield_report(config, origin="AV99")["status"] == "NO_OBSERVATIONS"
    duplicate = list(rows(config).values())
    with pytest.raises(IntegrityError):
        zc.yield_summary([duplicate[0], duplicate[0]])


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-14", None),
        ("2026-10-14T00:00:00Z", None),
        ("2026-02-30T00:00:00", None),
        ("2026-10-14T00:10:00", "2026-10-14T00:00:00"),
    ],
)
def test_query_requires_explicit_local_valid_window(config, start, end):
    with pytest.raises(ConfigurationError):
        yield_report(config, start=start, end=end)


def test_unscoped_error_marks_origin_review_and_empty_file_does_not_create_delta(config, source):
    assert yield_report(config)["status"] == "NO_INPUTS" and not config.control_root.exists()
    source.write_bytes(b"")
    assert run(config)["status"] == "NO_CHANGE" and not config.table_path.exists()
    source.write_text(line(), encoding="utf-8")
    run(config)
    source.write_text("BAD;SCHEMA\n", encoding="utf-8")
    assert run(config)["yield_summary"]["review_observations"] == 1
    with pytest.raises(ConfigurationError):
        replace(config, timestamp_policy=None).validate()


def test_cli_ingest_and_read_only_yield(config, source, tmp_path, capsys):
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
    p = tmp_path / "zc.toml"
    p.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
        + '\n[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert main(["ingest", "--config", str(p)]) == 0
    assert json.loads(capsys.readouterr().out)["published_records"] == 1
    assert main(["zc-yield", "--config", str(p), "--origin", "AV21", "--machine", "23"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["rate_percent"] == "98.0793"
