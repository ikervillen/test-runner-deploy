"""The approved second-occurrence policy is narrow, atomic and replayable."""

import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from deltalake import DeltaTable
from test_cont_zc import line as zc_line
from test_lista_defectos import line as defect_line
from test_lista_rech import line as rech_line

from ingesta_delta_lake.common import IngestaError
from ingesta_delta_lake.repeated_hour import RULE, repeated
from ingesta_delta_lake.service import run


@pytest.fixture(params=["LISTA_RECH", "LISTA_DEFECTOS", "CONT_ZC"])
def selected(config, request):
    return replace(config, family=request.param, timestamp_policy="local_naive_test_v1")


def sample(family, value, *, stamp="25/10/2026 2:50:00", extra=False):
    if family == "LISTA_RECH":
        return rech_line(date=stamp, inspected=value, rejected=1)
    if family == "CONT_ZC":
        return zc_line(FECHA=stamp, RUEDA=value)
    return defect_line(date=stamp, inspected=value, defect=15) + (
        defect_line(date=stamp, inspected=value, defect=16) if extra else ""
    )


def export(config, second, text, *, suffix=None):
    filename = f"25102026_{suffix or ('025027' if second else '025020')}_{config.family}.csv"
    path = config.input_root / "AV21" / filename
    path.write_text(text, encoding="utf-8")
    stamp = datetime(2026, 10, 25, int(second), 50, 27 if second else 20, tzinfo=UTC).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def data(config):
    # Parquet reads with no background scanner also work in short-lived containers.
    import pyarrow.parquet as pq

    table = DeltaTable(str(config.table_path))
    return [r for path in table.file_uris() for r in pq.ParquetFile(path).read().to_pylist()]


def test_only_second_occurrence_survives_without_incidents_or_replay_writes(selected):
    config = selected
    first = sample(config.family, 96, extra=True)
    first += sample(config.family, 77, stamp="25/10/2026 3:10:00")
    export(config, False, first)
    run(config)
    export(config, True, sample(config.family, 97))
    result = run(config)
    assert result["complete"] and result["automatic_repeated_hour_slots"] == 1
    rows = data(config)
    repeated_rows = [r for r in rows if r["FECHA"].hour == 2]
    assert len(repeated_rows) == 1
    assert repeated_rows[0]["RUEDA" if config.family == "CONT_ZC" else "INSPECC"] == 97
    assert len([r for r in rows if r["FECHA"].hour == 3]) == 1
    assert query(config, "SELECT count(*) FROM issues WHERE status!='RESOLVED'") == [(0,)]
    version = result["delta_version"]
    assert run(config)["delta_version"] == version
    if config.family != "CONT_ZC":
        assert query(config, "SELECT sum(row_count) FROM inspection_current") == [(len(rows),)]
    receipts = query(config, "SELECT result_json FROM review_operations WHERE state='VERIFIED'")
    assert any(json.loads(r[0]).get("automatic_policy") == RULE for r in receipts)


def test_older_export_arriving_late_does_not_replace_the_second_occurrence(selected):
    config = selected
    export(config, False, sample(config.family, 96))
    run(config)
    export(config, True, sample(config.family, 97))
    version = run(config)["delta_version"]
    export(config, False, sample(config.family, 95), suffix="025039")
    result = run(config)
    assert result["complete"] and result["delta_version"] == version
    assert data(config)[0]["RUEDA" if config.family == "CONT_ZC" else "INSPECC"] == 97


@pytest.mark.parametrize("after_commit", [False, True])
def test_recovery_retains_the_intention_and_commits_once(selected, monkeypatch, after_commit):
    config = selected
    export(config, False, sample(config.family, 96))
    run(config)
    export(config, True, sample(config.family, 97))
    from ingesta_delta_lake import reviews

    original = reviews.write_deltalake

    def fail(*args, **kwargs):
        if kwargs.get("mode") == "overwrite":
            if after_commit:
                original(*args, **kwargs)
            raise OSError("Policy interrupted")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(reviews, "write_deltalake", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["complete"] and result["delta_version"] == 1
    assert data(config)[0]["RUEDA" if config.family == "CONT_ZC" else "INSPECC"] == 97
    assert query(config, "SELECT count(*) FROM review_operations WHERE state='PREPARED'") == [(0,)]


def test_repeated_hour_is_calendar_based_and_does_not_cover_normal_hours():
    assert repeated("2026-10-25T02:50:00")
    assert not repeated("2026-10-25T03:50:00")
    assert not repeated("2026-10-24T02:50:00")
    assert not repeated("2026-03-29T02:50:00")


def test_timezone_rules_are_available_without_an_operating_system_database():
    import zoneinfo

    previous = zoneinfo.TZPATH
    try:
        zoneinfo.reset_tzpath(())
        zoneinfo.ZoneInfo.clear_cache()
        assert repeated("2026-10-25T02:50:00")
        assert not repeated("2026-10-25T03:00:00")
    finally:
        zoneinfo.reset_tzpath(previous)
        zoneinfo.ZoneInfo.clear_cache()
