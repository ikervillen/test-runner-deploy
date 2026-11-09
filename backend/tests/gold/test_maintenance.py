import io
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import ijson
import pytest

from app.gold.artifact_stream import Blocks, items
from app.gold.backup import create, drill, restore, verify
from app.gold.cli import main
from app.gold.common import IntegrityError, read_json
from app.gold.operations import observed, render
from app.gold.publications import Snapshot

from .test_api import gold_api as gold_api
from .test_pipeline import calculation as calculation


def test_backup_restores_independently_and_rejects_corruption(gold_api, tmp_path):
    _, root = gold_api
    backup = tmp_path / "backup"
    result = create(root, backup)
    assert result["status"] == "VERIFIED"
    restored = tmp_path / "restored"
    assert restore(backup, restored)["status"] == "RESTORED"
    original_id = Snapshot.open(root).publication_id
    root.rename(tmp_path / "original-unavailable")
    assert Snapshot.open(restored, require_gold_contract=True).publication_id == original_id
    with pytest.raises(IntegrityError, match="existing destination"):
        restore(backup, restored)
    manifest = read_json(backup / "backup.json")
    target = backup / manifest["files"][0]["path"]
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(IntegrityError, match="checksum/size"):
        verify(backup)
    assert not (tmp_path / "invalid-restore").exists()


def test_maintenance_cli_inventory_and_verified_backup(gold_api, tmp_path, capsys):
    _, root = gold_api
    target = tmp_path / "backup"
    assert main(["inventory", "--root", str(root)]) == 0
    assert '"partition_columns"' in capsys.readouterr().out
    assert main(["backup", "--source", str(root), "--destination", str(target)]) == 0
    assert '"VERIFIED"' in capsys.readouterr().out
    assert main(["verify-backup", "--root", str(target)]) == 0
    capsys.readouterr()
    assert (
        main(["restore", "--source", str(target), "--destination", str(tmp_path / "restored")]) == 0
    )
    assert '"RESTORED"' in capsys.readouterr().out
    scratch = tmp_path / "scratch"
    assert main(["restore-drill", "--root", str(root), "--scratch", str(scratch)]) == 0
    assert '"RESTORE_VERIFIED"' in capsys.readouterr().out
    assert "calidad_gold_restore_drill_success_timestamp_seconds 0" not in render(root)


def test_restore_drill_reads_the_latest_backup_and_discards_it(gold_api, tmp_path):
    _, root = gold_api
    scratch = tmp_path / "scratch"
    with pytest.raises(IntegrityError, match="No verified backup"):
        drill(root, scratch)
    create(root, tmp_path / "backup")
    result = drill(root, scratch)
    assert result["status"] == "RESTORE_VERIFIED"
    assert result["publication_id"] == Snapshot.open(root).publication_id
    assert result["rows"] > 0
    assert list(scratch.iterdir()) == []
    assert read_json(root / "restore-status.json")["backup"] == str(tmp_path / "backup")


def test_restore_drill_fails_on_a_corrupt_backup_without_leftovers(gold_api, tmp_path):
    _, root = gold_api
    backup = tmp_path / "backup"
    create(root, backup)
    target = backup / read_json(backup / "backup.json")["files"][0]["path"]
    target.write_bytes(target.read_bytes() + b"tampered")
    scratch = tmp_path / "scratch"
    with pytest.raises(IntegrityError, match="checksum/size"):
        drill(root, scratch)
    assert not scratch.exists() or list(scratch.iterdir()) == []
    assert not (root / "restore-status.json").exists()


def test_history_tables_are_partitioned_by_production_and_pruned_on_read(gold_api):
    from app.gold.query import Query
    from app.gold.schemas import PARTITION_COLUMN, PARTITIONED_TABLES

    _, root = gold_api
    snapshot = Snapshot.open(root, require_gold_contract=True)
    for name in PARTITIONED_TABLES:
        assert snapshot.table(name).metadata().partition_columns == [PARTITION_COLUMN]
    assert snapshot.table("quality_scores").metadata().partition_columns == []
    query = Query(root)
    productions = [p["id_produccion"] for p in query.rows("productions")]
    everything = set(snapshot.table("mia_inspections").to_pyarrow_dataset().files)
    assert len(everything) == len(productions)
    query.rows("mia_inspections", productions[:1])
    pruned = query.files & everything
    assert len(pruned) == 1
    assert all(f"{PARTITION_COLUMN}={productions[0]}/" in f for f in pruned)


def test_publisher_health_preserves_last_success_on_failure(tmp_path):
    assert "calidad_gold_status_available 0" in render(tmp_path)
    observed(
        tmp_path, lambda: {"status": "PUBLISHED", "publication_id": "one", "rows": {"facts": 4}}
    )
    first = read_json(tmp_path / "operation-status.json")

    def fail():
        raise RuntimeError("no publication promoted")

    with pytest.raises(RuntimeError):
        observed(tmp_path, fail)
    failed = read_json(tmp_path / "operation-status.json")
    assert failed["last_success_at"] == first["last_success_at"]
    assert failed["attempts"] == 2 and failed["failures"] == 1
    assert 'calidad_gold_duration_seconds_bucket{le="+Inf"} 2' in render(tmp_path)
    assert "calidad_gold_last_attempt_success 0" in render(tmp_path)


def test_source_freshness_survives_unchanged_runs_and_is_exported(tmp_path):
    source = {
        "publication_id": "silver-1",
        "published_at": "2026-11-06T14:00:00+00:00",
        "discovery_lag_seconds": {"CONT_ZC": 1800.0, "LISTA_RECH": 0.0},
    }
    observed(tmp_path, lambda: {"status": "PUBLISHED", "publication_id": "one", "source": source})
    observed(tmp_path, lambda: {"status": "UNCHANGED", "publication_id": "one"})
    text = render(tmp_path)
    published = datetime(2026, 11, 6, 14, tzinfo=UTC).timestamp()
    assert f"calidad_gold_source_publication_timestamp_seconds {published}" in text
    assert 'calidad_gold_source_discovery_lag_seconds{family="CONT_ZC"} 1800.0' in text


def test_json_stream_handles_objects_split_across_small_blocks():
    parts = [b'[{"id":', b'1,"label":"a', b'b"},{"id":2}]']
    with io.BufferedReader(Blocks(parts), buffer_size=4) as stream:
        assert list(ijson.items(stream, "item")) == [{"id": 1, "label": "ab"}, {"id": 2}]


def test_native_association_artifacts_stream_and_verify_hashes(tmp_path, monkeypatch):
    native = Path(__file__).resolve().parents[4] / "Ingesta_Delta_Lake/src"
    if not native.exists():
        pytest.skip("Cross-project ingestion adapter requires the ingestion checkout")
    monkeypatch.syspath_prepend(str(native))
    from ingesta_delta_lake.association_cas import encoded, write

    cfg = SimpleNamespace(control_root=tmp_path)
    descriptor = write(tmp_path / "association-artifacts", encoded({"registry": {"a": {"id": 1}}}))
    assert dict(items(cfg, descriptor, "registry", mapping=True)) == {"a": {"id": 1}}
    ledger = write(tmp_path / "association-artifacts", encoded([{"id": 1}, {"id": 2}]))
    assert list(items(cfg, ledger, "item")) == [{"id": 1}, {"id": 2}]
    with pytest.raises(IntegrityError, match="budget"):
        list(items(cfg, ledger, "item", max_items=1))
