import hashlib
from datetime import datetime

import pytest

from app.gold import publications
from app.gold.publications import Publisher, Snapshot
from app.gold.storage_audit import measure, scope_diff

from .test_publications import ScopeSpill, coverage_row, source

STATUS = "publication_scope_status"


@pytest.mark.parametrize("changes", [0, 1, 10, 128, 129, 2048])
def test_write_metrics_at_scope_merge_boundary(tmp_path, monkeypatch, changes):
    snap = source(tmp_path / "input")
    rows = [{**coverage_row(), "scope_id": f"k-{i:05}"} for i in range(2100)]
    corrected = [
        {
            **row,
            "evidence_reference": "new",
            **(
                {
                    "reason_code": "LATE_CORRECTION",
                    "last_successful_discovery": datetime(2026, 1, 1),
                }
                if i == 0
                else {}
            ),
        }
        if i < changes
        else row
        for i, row in enumerate(rows)
    ]

    def publish(root, values, cutoff):
        return Publisher(root, environment="test", plant="AV").publish(
            snap, ScopeSpill(values), config_id="c", cutoff=cutoff, coverage=[]
        )

    root = tmp_path / "candidate/published"
    publish(root, rows, "before")
    result = publish(root, corrected, "after")
    telemetry = result["write_telemetry"]
    metrics = telemetry["tables"][STATUS]
    assert telemetry["publication_ms"] >= metrics["compare_ms"] >= 0
    assert metrics["commits"] == int(changes > 0)
    assert metrics["previous_delta_version"] == 0
    assert metrics["delta_version"] == int(changes > 0)
    if changes <= 128:
        assert metrics["changed_keys"] == changes
        assert metrics["compared_inserted"] == 0
        assert metrics["compared_updated"] == changes
        assert metrics["rows_compared_unchanged"] == len(rows) - changes
        if changes:
            assert metrics["strategy"] == "sparse_merge"
            assert metrics["rows_updated"] == changes
            assert metrics["merge_wall_ms"] >= 0
            assert metrics["files_scanned_by_merge"] == 1
        else:
            assert metrics["strategy"] == "reuse" and metrics["parquet_added"] == 0
    else:
        assert metrics["strategy"] == "overwrite"
        assert metrics["comparison_outcome"] == "limit"
        assert metrics["changed_keys_min"] >= 129
        assert metrics["rows_updated"] is None
    assert (metrics["logical_bytes_added"] > 0) == bool(changes)

    # Reference the original full writer on the same input and compare persisted rows.
    baseline = tmp_path / "baseline/published"
    with monkeypatch.context() as patch:
        patch.setattr(publications, "_changed_scope_keys", lambda *args, **kwargs: None)
        publish(baseline, rows, "before")
        old = publish(baseline, corrected, "after")["write_telemetry"]["tables"][STATUS]
    assert old["strategy"] == "overwrite"

    def key(row):
        return row["scope_id"]

    assert sorted(Snapshot.open(root).rows(STATUS), key=key) == sorted(
        Snapshot.open(baseline).rows(STATUS), key=key
    )
    assert scope_diff(root.parent, 0, metrics["delta_version"], budget=5000)["updated"] == changes


def test_storage_inventory_tracks_history_and_hardlinks(tmp_path):
    snap = source(tmp_path / "input")
    root = tmp_path / "gold"
    publisher = Publisher(root / "published", environment="test", plant="AV")
    a = coverage_row()
    b = {**a, "scope_id": "two"}
    publisher.publish(snap, ScopeSpill([a, b]), config_id="c", cutoff="1", coverage=[])
    start = measure(root)
    result = publisher.publish(snap, ScopeSpill([b]), config_id="c", cutoff="2", coverage=[])
    write = result["write_telemetry"]["tables"][STATUS]
    assert write["comparison_outcome"] == "withdrawal"
    assert write["parquet_replaced"] == 1
    assert scope_diff(root, 0, 1, budget=10) == {
        "before": 0,
        "after": 1,
        "deleted": 1,
        "inserted": 0,
        "unchanged": 1,
        "updated": 0,
    }
    original = root / "inputs/data"
    evidence = root / "published/evidence/data"
    original.mkdir(parents=True)
    evidence.mkdir(parents=True)
    for payload, linked in ((b"shared", True), (b"duplicate", False)):
        name = hashlib.sha256(payload).hexdigest() + ".parquet"
        file = original / name
        file.write_bytes(payload)
        if linked:
            (evidence / name).hardlink_to(file)
        else:
            (evidence / name).write_bytes(payload)
    (root / f"published/tables/{STATUS}/unreferenced.parquet").write_bytes(b"unreferenced")
    report = measure(root, previous=start, verify_evidence=True)
    assert report["pin_unchanged_during_sample"]
    assert report["historical_publications"] == 1
    assert report["groups"]["historical_pinned"]["paths"] == 1
    assert report["groups"]["outside_pin"]["paths"] == 1
    assert report["groups"]["evidence"]["unique_inodes"] == 3
    assert report["evidence"]["hardlinked_pairs"] == 1
    assert report["evidence"]["separate_inode_pairs"] == 1
    if start["allocated_unique_bytes"] is not None:
        assert report["change"]["new_inode_allocated_bytes"] > 0
