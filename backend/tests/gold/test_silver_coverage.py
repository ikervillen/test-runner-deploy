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

    receipts.update({blank: (1, "empty")}, {blank: "empty"})
    assert covers(project({"parts_root": tree}), hour)
    assert project({"parts": parts}) == project({"parts_root": tree})
    # A newer file invalidates the empty proof until Silver verifies a replacement.
    receipts.update({blank: (2, "later-revision")}, {})
    assert not covers(project({"parts_root": tree}), hour)
    receipts.update({blank: (1, "empty")}, {blank: "empty"})
    assert not covers(project({"parts_root": tree}), hour)
    receipts.update({blank: (2, "later-revision")}, {blank: "later-revision"})
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
            entries = (
                (1, "confirmed", "AV32", b" "),
                (2, "missing-silver", "AV21", b""),
                (3, "not-blank", "AV23", b"25/11/2026 07:50:00;old"),
                (4, "new-revision", "AV22", b""),
                (5, "ar-accepted", "AV31", b"x" * 5000),
            )
            for sequence, identity, origin, data in entries:
                if not limits["after"] < sequence <= limits["after"] + limits["limit"]:
                    continue
                yield SimpleNamespace(
                    family="LISTA_AR" if sequence == 5 else FAMILY,
                    input_id=identity,
                    sequence=sequence,
                    snapshot=SimpleNamespace(
                        origin=origin, producer_time=clock, source_path=origin, size=len(data)
                    ),
                    data=data,
                )

        def read(self, capture, **options):
            return capture.data

    monkeypatch.setattr(bronze, "BronzeFeed", Feed)
    snapshot = SimpleNamespace(
        manifest={
            "identity": "a",
            "tables": {
                FAMILY: {
                    "captured_ids": ["missing-silver"],
                    "observed_until": {"AV32": START.isoformat()},
                    "slot_receipts": [
                        [1, "confirmed", "AV32", clock, True],
                        [3, "not-blank", "AV23", clock, True],
                        [2, "old-revision", "AV22", clock, True],
                        [5, "absent-bronze", "AV31", clock, True],
                    ],
                }
            },
        }
    )
    seen, verified = confirmed_blanks(
        None, snapshot, SimpleNamespace(live_batch_files=64, window_seconds=600)
    )
    slot = slot_number(START + STEP, 600)
    assert seen[FAMILY, "AV22", slot] == (4, "new-revision")
    assert verified == {(FAMILY, "AV32", slot): "confirmed", (FAMILY, "AV23", slot): None}
    old_pin = SimpleNamespace(manifest={"identity": "a", "tables": {}})
    assert not confirmed_blanks(
        None, old_pin, SimpleNamespace(live_batch_files=64, window_seconds=600)
    )[1]
    # The exact published receipt is directly addressable even after the Bronze tail advances.
    Feed.sequence = 9000
    assert confirmed_blanks(
        None, snapshot, SimpleNamespace(live_batch_files=64, window_seconds=600), after=8999
    )[1] == {(FAMILY, "AV32", slot): "confirmed", (FAMILY, "AV23", slot): None}
    snapshot.manifest["controls"] = {
        "LISTA_AR": {"slot_receipts": [[5, "ar-accepted", "AV31", clock, True]]}
    }
    assert (
        confirmed_blanks(
            None, snapshot, SimpleNamespace(live_batch_files=64, window_seconds=600), after=8999
        )[1]["LISTA_AR", "AV31", slot]
        == "ar-accepted"
    )


def test_ar_with_events_requires_exact_receipts_and_ignores_unrelated_review(tmp_path):
    from types import SimpleNamespace

    from app.gold.pipeline import _coverage_ready

    family = "LISTA_AR"
    row = dict(
        source=family,
        origin="AV21",
        estado="READY",
        reason_code="MANUAL_REVIEW",
        ar_receipts_required=True,
        ar_review_intervals=[(None, START.isoformat())],
    )
    evidence = SimpleNamespace(manifest={"lane": "live", "coverage": [row]})
    prod = dict(origen_fenix="AV21", id_produccion="p")
    end = START + 6 * STEP
    hour = Window("p", "HOUR", START, end, end)
    receipts = BlankReceipts(tmp_path, 600)
    slots = [(family, "AV21", slot_number(START + STEP * i, 600)) for i in range(1, 7)]
    seen = {key: (i, f"ar-{i}") for i, key in enumerate(slots, 1)}
    assert not _coverage_ready(evidence, prod, hour, [], {family})

    def prepare():
        row.pop("confirmed_slots", None)
        SlotCoverage(
            [row], seconds=600, indexed={}, tables={}, index=None, receipts=receipts
        ).prepare(START, end)

    verified = {k: capture for k, (_, capture) in seen.items()}
    receipts.update(seen, {k: v for k, v in verified.items() if k != slots[0]})
    prepare()
    assert not _coverage_ready(evidence, prod, hour, [], {family})
    receipts.update(seen, verified)
    prepare()
    assert _coverage_ready(evidence, prod, hour, [], {family})
    row["ar_review_intervals"] = [((START - STEP).isoformat(), None)]
    assert not _coverage_ready(evidence, prod, hour, [], {family})
    row["ar_review_intervals"] = []
    receipts.update({slots[0]: (7, "revision-pending")}, {})
    prepare()
    assert not _coverage_ready(evidence, prod, hour, [], {family})
