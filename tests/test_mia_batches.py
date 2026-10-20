import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import defects_service as batching
from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.defects_control import DefectsControl
from ingesta_delta_lake.service import run


@pytest.fixture(params=["LISTA_RECH", "LISTA_DEFECTOS"])
def mia(config, request):
    return replace(config, family=request.param, timestamp_policy="local_naive_test_v1")


def csv_row(family, minute, rejected=12):
    date = f"14/10/2026 00:{minute:02d}:00"
    fields = (
        [date, 0, 1, 13, 96, rejected, 0, "", ""]
        if family == "LISTA_RECH"
        else [date, 0, 1, 13, 2, 96, rejected, *([0] * 10), ""]
    )
    return ";".join(map(str, fields)) + ";\n"


def sources(config, count):
    paths = []
    for i in range(count):
        path = config.input_root / "AV21" / f"14102026_{90000 + i:06d}_{config.family}.csv"
        path.write_text(csv_row(config.family, i), encoding="utf-8")
        paths.append(path)
    return paths


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def test_bounded_parse_and_consumed_hashes_skip_replay_but_keep_alias_receipts(mia, monkeypatch):
    paths = sources(mia, 5)
    monkeypatch.setattr(batching, "BATCH_FILES", 2)
    model = __import__(f"ingesta_delta_lake.{mia.family.lower()}", fromlist=["parse"])
    original = model.parse
    seen = []

    def parse(snapshots, encoding):
        seen.append(len(snapshots))
        assert len(snapshots) <= 2
        return original(snapshots, encoding)

    monkeypatch.setattr(model, "parse", parse)
    first = run(mia)
    assert seen == [2, 2, 1]
    assert first["processed_versions"] == 5 and first["completed_batches"] == 3
    assert first["remaining_versions"] == 0 and first["published_rows"] == 5
    table = DeltaTable(str(mia.table_path))
    assert table.version() == 2
    alias = paths[0].with_name(f"14102026_100000_{mia.family}.csv")
    alias.write_bytes(paths[0].read_bytes())
    seen.clear()
    second = run(mia)
    assert second["status"] == "NO_CHANGE" and second["processed_versions"] == 0
    assert seen == [0] and DeltaTable(str(mia.table_path)).version() == 2
    assert query(mia, "SELECT count(*) FROM consumed_inputs") == [(5,)]
    assert query(mia, "SELECT count(*) FROM receipts") == [(11,)]


@pytest.mark.parametrize("after_ack", [False, True])
def test_failure_around_input_ack_never_loses_or_duplicates_rows(mia, monkeypatch, after_ack):
    sources(mia, 5)
    monkeypatch.setattr(batching, "BATCH_FILES", 2)
    original = DefectsControl.acknowledge_inputs

    def fail(self, batch_id, snapshots):
        if after_ack:
            original(self, batch_id, snapshots)
        raise OSError("Simulated process loss at input checkpoint")

    with monkeypatch.context() as patch:
        patch.setattr(DefectsControl, "acknowledge_inputs", fail)
        with pytest.raises(OSError):
            run(mia)
    assert DeltaTable(str(mia.table_path)).version() == 0
    assert query(mia, "SELECT count(*) FROM consumed_inputs") == [(2 if after_ack else 0,)]
    result = run(mia)
    assert result["published_rows"] == 5 and result["remaining_versions"] == 0
    table = DeltaTable(str(mia.table_path))
    assert table.version() == 2 and table.to_pyarrow_table().num_rows == 5
    assert query(mia, "SELECT count(*) FROM consumed_inputs") == [(5,)]


def test_stop_between_committed_batches_resumes_remaining_versions(mia, monkeypatch):
    sources(mia, 5)
    monkeypatch.setattr(batching, "BATCH_FILES", 2)
    calls = []
    original = DefectsControl.acknowledge_inputs

    def acknowledge(self, *args):
        original(self, *args)
        calls.append(True)

    monkeypatch.setattr(DefectsControl, "acknowledge_inputs", acknowledge)
    first = run(mia, stop_requested=lambda: bool(calls))
    assert first["status"] == "STOPPED_AFTER_BATCH"
    assert first["published_rows"] == 2 and first["remaining_versions"] == 3
    assert query(mia, "SELECT count(*) FROM consumed_inputs") == [(2,)]
    second = run(mia)
    assert second["processed_versions"] == 3 and second["published_rows"] == 5


def test_conflict_across_batches_keeps_original_and_quarantines_variant(mia, monkeypatch):
    paths = sources(mia, 2)
    paths[1].write_text(csv_row(mia.family, 0, rejected=13), encoding="utf-8")
    monkeypatch.setattr(batching, "BATCH_FILES", 1)
    result = run(mia)
    assert result["status"] == "PARTIAL" and result["published_rows"] == 1
    assert DeltaTable(str(mia.table_path)).to_pyarrow_table()["RECH"].to_pylist() == [12]
    cases = [json.loads(row[0]) for row in query(mia, "SELECT payload_json FROM issues")]
    assert any(case["reason"] == "ROW_CONFLICT" for case in cases)
    module = "rejections_service" if mia.family == "LISTA_RECH" else "defects_service"
    bases = __import__(f"ingesta_delta_lake.{module}", fromlist=["inspection_bases"])
    bases = bases.inspection_bases(mia)
    assert not bases["complete"] and bases["resolved_inspection_total"] == 0
    assert query(mia, "SELECT count(*) FROM consumed_inputs") == [(2,)]


def test_chunk_byte_budget_and_single_oversize_file(monkeypatch):
    monkeypatch.setattr(batching, "BATCH_BYTES", 10)
    items = [SimpleNamespace(size=size) for size in [6, 6, 12, 1, 1]]
    batches = list(batching.chunks(items))
    assert [[item.size for item in batch] for batch in batches] == [[6], [6], [12], [1, 1]]


def test_overwrite_with_same_size_and_mtime_is_still_detected(mia):
    import os

    path = sources(mia, 1)[0]
    run(mia)
    stamp = path.stat()
    path.write_text(csv_row(mia.family, 1), encoding="utf-8")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    result = run(mia)
    assert result["processed_versions"] == 1 and result["published_rows"] == 2


def test_new_arrivals_are_consumed_on_next_scan_without_replaying_history(mia, monkeypatch):
    sources(mia, 1)
    original = DefectsControl.acknowledge_inputs
    late = mia.input_root / "AV21" / ("14102026_100000_" + mia.family + ".csv")

    def acknowledge(self, batch_id, snapshots):
        original(self, batch_id, snapshots)
        if snapshots:
            late.write_text(csv_row(mia.family, 1), encoding="utf-8")

    with monkeypatch.context() as patch:
        patch.setattr(DefectsControl, "acknowledge_inputs", acknowledge)
        assert run(mia)["processed_versions"] == 1
    result = run(mia)
    assert result["processed_versions"] == 1 and result["published_rows"] == 2


def test_partial_capture_is_durable_even_if_a_later_file_fails(mia, monkeypatch):
    paths = sources(mia, 2)
    original = batching.capture

    def capture(config, origin, path, stamp, **kwargs):
        if path == paths[1]:
            raise OSError("Second source still being written")
        return original(config, origin, path, stamp, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(batching, "capture", capture)
        with pytest.raises(OSError):
            run(mia)
    paths[0].unlink()
    result = run(mia)
    assert result["processed_versions"] == 2 and result["published_rows"] == 2


def test_consumed_versions_cannot_silently_change_interpretation(mia):
    sources(mia, 1)
    run(mia)
    with pytest.raises(IntegrityError, match="explicit migration"):
        run(replace(mia, encoding="latin1"))


def test_capture_survives_process_loss_before_enqueue_and_source_rotation(mia, monkeypatch):
    path = sources(mia, 1)[0]

    def fail(*_):
        raise OSError("Process loss after Bronze fsync, before SQLite receipt")

    with monkeypatch.context() as patch:
        patch.setattr(DefectsControl, "receipts", fail)
        with pytest.raises(OSError):
            run(mia)
    path.unlink()
    result = run(mia)
    assert result["processed_versions"] == 1 and result["published_rows"] == 1
    assert query(mia, "SELECT COUNT(*) FROM input_versions") == [(1,)]
    assert run(mia)["processed_versions"] == 0
