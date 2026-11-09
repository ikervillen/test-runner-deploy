from datetime import UTC, datetime, timedelta

import pytest
from deltalake import DeltaTable, write_deltalake

from app.gold.cli import main
from app.gold.common import IntegrityError
from app.gold.failures import EXIT_DATA
from app.gold.maintenance import maintain
from app.gold.operations import render
from app.gold.pipeline import run
from app.gold.publications import Publisher, Snapshot

from .test_pipeline import CUTOFF, bundle
from .test_pipeline import calculation as calculation


@pytest.fixture
def store(calculation, tmp_path):
    snap, config = calculation
    root = tmp_path / "gold"

    def publish(step):
        return run(
            snap.root,
            root,
            bundle=bundle(),
            config=config,
            cutoff=CUTOFF + timedelta(minutes=10 * step),
            environment="test",
            plant="AV",
        )

    published = [publish(0)["publication_id"]]
    # Scoped replacements insert one new file each, like incremental publication does.
    publisher = Publisher(root, environment="test", plant="AV")
    template = Snapshot.open(root).rows("publication_scope_status")[0]
    for step in range(3):
        origin = f"EXTRA{step}"
        result = publisher.publish(
            snap,
            {"publication_scope_status": [{**template, "scope_id": origin, "origin": origin}]},
            config_id="scoped",
            cutoff=origin,
            coverage=[],
            replacement_scopes={"publication_scope_status": {"origin": [origin]}},
        )
        published.append(result["publication_id"])
    return root, publish, published


def contents(root, publication_id=None):
    snap = Snapshot.open(root, publication_id=publication_id, require_gold_contract=True)
    return {r["name"]: sorted(map(repr, snap.rows(r["name"]))) for r in snap.manifest["tables"]}


def test_compaction_keeps_rows_and_the_next_publication_writes(store):
    root, publish, published = store
    before = contents(root)
    base = Snapshot.open(root).manifest
    result = maintain(root, keep_days=30)
    assert result["status"] == "MAINTAINED"
    scopes = result["tables"]["publication_scope_status"]
    assert (scopes["files_before"], scopes["files_after"]) == (4, 1)
    assert contents(root) == before
    current = Snapshot.open(root).manifest
    assert current["previous_publication_id"] == published[-1]
    assert current["input_publication_id"] == base["input_publication_id"]
    assert publish(3)["status"] == "PUBLISHED"
    assert "calidad_gold_maintenance_success_timestamp_seconds 0" not in render(root)


def test_vacuum_only_drops_publications_older_than_the_window(store):
    root, _, published = store
    first = contents(root, published[0])
    assert sum(t["files_vacuumed"] for t in maintain(root, keep_days=30)["tables"].values()) == 0
    assert contents(root, published[0]) == first
    current = contents(root)
    later = datetime.now(UTC) + timedelta(days=2)
    result = maintain(root, keep_days=1, now=later)
    assert sum(t["files_vacuumed"] for t in result["tables"].values()) > 0
    assert contents(root) == current
    with pytest.raises(IntegrityError, match="missing"):
        Snapshot.open(root, publication_id=published[0])


def test_interrupted_maintenance_blocks_publishing_until_resumed(store):
    root, publish, _ = store

    def crash(point):
        if point.startswith("table:"):
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        maintain(root, keep_days=30, failpoint=crash)
    with pytest.raises(IntegrityError, match="Recover the pending"):
        publish(3)
    assert maintain(root, keep_days=30)["status"] == "MAINTAINED"
    assert publish(3)["status"] == "PUBLISHED"


def test_commit_outside_a_publication_is_not_adopted(store):
    root, _, _ = store
    path = root / "tables" / "dim_model"
    write_deltalake(path, DeltaTable(path).to_pyarrow_table(), mode="overwrite")
    with pytest.raises(IntegrityError, match="outside any publication"):
        maintain(root, keep_days=30)


@pytest.mark.parametrize("days", ["0", "3651"])
def test_retention_window_must_be_explicit_and_bounded(tmp_path, capsys, days):
    assert main(["maintain", "--root", str(tmp_path), "--keep-days", days]) == EXIT_DATA
    assert "retention" in capsys.readouterr().err
