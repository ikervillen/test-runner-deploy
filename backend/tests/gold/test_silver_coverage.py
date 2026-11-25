from datetime import datetime, timedelta

from app.gold.fragment_index import DEPTH, FragmentIndex
from app.gold.metrics import Window
from app.gold.silver_coverage import BlankReceipts, SlotCoverage, covers, slot_number

START = datetime(2026, 11, 25, 8)
STEP = timedelta(minutes=10)
FAMILY = "LISTA_DEFECTOS"


def test_index_and_inline_match_without_bridging_missing_or_decoding_parquet(tmp_path):
    slots = [slot_number(START + STEP * i, 600) for i in range(1, 7)]
    parts = []
    for index, slot in enumerate(slots):
        if index == 2:
            continue
        stamp = (START + STEP * (index + 1)).isoformat()
        parts.append({"rows": 1, "partition": ["AV32", slot], "time_bounds": [stamp, stamp]})
    # Most nodes do not belong to the requested hour.
    partitions = {tuple(p["partition"]): p for p in parts}
    for slot in range(slots[0] - 1000, slots[0]):
        partitions["AV23", slot] = dict(parts[0], partition=["AV23", slot])
    writer = FragmentIndex(tmp_path)
    tree = writer.updated(None, partitions)
    index = FragmentIndex(tmp_path)
    hour = Window("production", "HOUR", START, START + 6 * STEP, START + 6 * STEP)
    receipts = BlankReceipts(tmp_path / "slots", 600)
    blank = (FAMILY, "AV32", slots[2])

    def project(ref):
        row = dict(source=FAMILY, origin="AV32", estado="READY", live_until="11:10")
        projection = SlotCoverage(
            [row],
            seconds=600,
            indexed={FAMILY: (ref, [], 0)},
            tables={},
            index=index,
            receipts=receipts,
        )
        projection.prepare(hour.start, hour.cutoff)
        projection.finalize(hour.cutoff)
        return row

    inline = project({"parts": parts})
    indexed = project({"parts_root": tree})
    assert inline == indexed
    assert not covers(indexed, hour)
    assert indexed["covered_from"] == "2026-11-25T08:30:00"
    assert indexed["covered_until"] == "2026-11-25T09:00:00"
    assert indexed["live_until"] == "11:10"
    assert index.pages_read < 6 * (DEPTH + 1)
    assert index.pages_read < writer.pages_written

    receipts.update({blank: "empty"}, {blank: "empty"})
    assert covers(project({"parts_root": tree}), hour)
    assert project({"parts": parts}) == project({"parts_root": tree})
    # A newer file invalidates the empty proof until Silver verifies a replacement.
    receipts.update({blank: "later-revision"}, {})
    assert not covers(project({"parts_root": tree}), hour)
    receipts.update({blank: "later-revision"}, {blank: "later-revision"})
    assert covers(project({"parts_root": tree}), hour)
    assert BlankReceipts(tmp_path / "slots", 600).confirmed(*blank)


def test_an_empty_slot_needs_both_bronze_bytes_and_owner_confirmation(monkeypatch):
    from types import SimpleNamespace

    import pytest

    from app.gold.silver_coverage import confirmed_blanks

    bronze = pytest.importorskip("ingesta_delta_lake.pipeline_bronze")
    clock = (START + STEP).isoformat()

    class Feed:
        identity = "a"
        sequence = 4

        def __init__(self, storage):
            pass

        def captures(self, **limits):
            for sequence, (identity, origin) in enumerate(
                (("confirmed", "AV32"), ("missing-silver", "AV21"), ("not-blank", "AV23")), 1
            ):
                yield SimpleNamespace(
                    family=FAMILY,
                    input_id=identity,
                    sequence=sequence,
                    snapshot=SimpleNamespace(
                        origin=origin, producer_time=clock, source_path=origin, size=1
                    ),
                )

        def read(self, capture, **options):
            return b" " if capture.input_id == "confirmed" else b"bad"

    monkeypatch.setattr(bronze, "BronzeFeed", Feed)
    snapshot = SimpleNamespace(
        manifest={
            "identity": "a",
            "tables": {
                FAMILY: {
                    "captured_ids": ["confirmed", "not-blank"],
                    "observed_until": {"AV32": clock, "AV23": clock, "AV21": clock},
                }
            },
        }
    )
    seen, verified = confirmed_blanks(
        None, snapshot, SimpleNamespace(live_batch_files=64, window_seconds=600)
    )
    slot = slot_number(START + STEP, 600)
    assert set(seen) == {(FAMILY, origin, slot) for origin in ("AV32", "AV21", "AV23")}
    assert verified == {(FAMILY, "AV32", slot): "confirmed"}
