import hashlib
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ingesta_delta_lake.common import (
    CaptureError,
    horizon_marks,
    live_slot_marks,
    note_observed_time,
    split_live_backlog,
)
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.lista_defectos import parse
from ingesta_delta_lake.sources import Snapshot, capture_inputs


def _snapshot(path: Path, producer_time: str | None) -> Snapshot:
    data = path.read_bytes()
    return Snapshot(
        "AV21",
        str(path),
        hashlib.sha256(data).hexdigest(),
        str(path),
        len(data),
        path.stat().st_mtime_ns,
        "2026-11-12T09:10:05+00:00",
        producer_time,
    )


def test_an_empty_file_records_its_clock_and_no_rows(tmp_path):
    path = tmp_path / "12112026_091000_LISTA_DEFECTOS.csv"
    path.write_bytes(b"\x00\r\n")
    parsed = parse([_snapshot(path, "2026-11-12T09:10:00")], "utf-8")
    assert parsed.entries == {}
    assert parsed.issues == []
    assert parsed.observed == {"AV21": "2026-11-12T09:10:00"}


def test_only_the_newest_slot_is_the_live_signal():
    class Snap:
        def __init__(self, origin, producer_time):
            self.origin = origin
            self.producer_time = producer_time

    marks = live_slot_marks(
        [
            Snap("AV21", "2026-11-12T06:50:00"),
            Snap("AV21", "2026-11-12T15:02:01"),
            Snap("AV22", "2026-11-12T15:10:00"),
        ]
    )
    assert marks == {"AV21": "2026-11-12T15:02:01", "AV22": "2026-11-12T15:10:00"}


def test_realtime_does_not_take_october_when_the_current_slot_is_stored():
    class Snap:
        def __init__(self, origin, producer_time, sha):
            self.origin = origin
            self.producer_time = producer_time
            self.sha256 = sha

    current = Snap("AV21", "2026-11-16T16:10:00", "current")
    october = Snap("AV21", "2026-10-19T09:00:00", "october")
    window = ("2026-11-16T16:10:00", "2026-11-16T16:20:00")
    live, backlog, marks = split_live_backlog([october], [current, october], window)
    assert live == []
    assert backlog == [october]
    assert marks == {"AV21": "2026-11-16T16:10:00"}
    late = Snap("AV32", "2026-11-16T08:40:00", "late")
    slot = Snap("AV21", "2026-11-16T16:10:00", "slot")
    live, backlog, marks = split_live_backlog([late, slot], [late, slot], window)
    assert live == [slot]
    assert backlog == [late]
    assert marks == {"AV32": "2026-11-16T16:10:00", "AV21": "2026-11-16T16:10:00"}


def test_a_file_without_a_clock_does_not_invent_a_cutoff():
    marks = {}
    note_observed_time(marks, "AV21", "2026-11-12")
    assert marks == {}


def test_rejected_rows_do_not_move_the_horizon():
    marks = horizon_marks(
        {},
        [{"origen_fenix": "AV21", "FECHA": datetime(2026, 11, 12, 9, 10)}],
        wrote=False,
    )
    assert marks == {}


def test_the_observed_clock_only_moves_forward(config):
    config = replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")
    config.control_root.mkdir()
    with closing(Control(config)) as control:
        control.advance_observed_until({"AV21": "2026-11-12T09:20:00"})
        control.advance_observed_until({"AV21": "2026-11-12T09:10:00"})
        assert control.setting("observed_until") == '{"AV21":"2026-11-12T09:20:00"}'


def test_an_unstable_file_does_not_block_the_next_one(config, monkeypatch):
    folder = config.input_root / "AV21"
    early = folder / "12112026_090000_CONT_ZC.csv"
    later = folder / "12112026_091000_CONT_ZC.csv"
    early.write_bytes(b"")
    later.write_bytes(b"")
    config = replace(config, family="CONT_ZC", timestamp_policy="local_naive_test_v1")
    config.control_root.mkdir()

    def flaky(config, origin, path, producer_time, *, known=None, verified=None):
        if Path(path).name.startswith("12112026_090000"):
            raise CaptureError("still changing")
        return Snapshot(
            origin,
            str(path),
            "a" * 64,
            str(path),
            0,
            0,
            "2026-11-12T09:10:05+00:00",
            producer_time,
        )

    monkeypatch.setattr("ingesta_delta_lake.sources.capture", flaky)
    with closing(Control(config)) as control:
        captured = capture_inputs(config, control, control.start_attempt())
    assert [Path(item.source_path).name for item in captured] == ["12112026_091000_CONT_ZC.csv"]
