"""Facts stay on disk per production."""

import sqlite3

import pytest

from app.gold.common import IntegrityError
from app.gold.spill import FactStore


def test_fact_store_has_a_configured_database_limit_and_releases_failed_connections(tmp_path):
    path = tmp_path / "facts.sqlite"
    store = FactStore(path, max_bytes=64 * 1024)
    store.add("mia_rejections", "p-a", "id", {"payload": b"a" * (128 * 1024)})
    with pytest.raises(sqlite3.OperationalError) as error:
        store.close()
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    path.unlink()


def test_one_production_load_does_not_return_the_other(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    store.add("mia_rejections", "p-b", "2", {"id_observacion": "2", "id_produccion": "p-b"})
    store.add("mia_rejections", "p-a", "1", {"id_observacion": "1", "id_produccion": "p-a"})
    store.ready()
    assert store.load("mia_rejections", "p-a") == [{"id_observacion": "1", "id_produccion": "p-a"}]
    assert [r["id_observacion"] for r in store.materialize("mia_rejections")] == ["1", "2"]
    store.close()


def test_batches_walk_one_family_a_thousand_at_a_time(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    for obs_id in ("1", "2", "3"):
        store.add(
            "mia_rejections",
            "p-a",
            obs_id,
            {"id_observacion": obs_id},
            key=(obs_id,),
        )
    store.add("mia_inspections", "p-a", "9", {"id_observacion": "9"}, key=("9",))
    store.ready()
    batches = list(store.batches("mia_rejections", 2))
    assert [len(batch) for batch in batches] == [2, 1]
    assert [row["id_observacion"] for batch in batches for row in batch] == ["1", "2", "3"]
    assert store.count("mia_inspections") == 1
    store.close()


def test_identical_output_keys_collapse_and_conflicts_fail(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    row = {"id_observacion": "1", "rechazos": 1}
    store.add("mia_rejections", "p-a", "1", row, key=("1",))
    store.add("mia_rejections", "p-a", "1b", row, key=("1",))
    store.dedupe()
    assert store.count("mia_rejections") == 1
    store.add("mia_rejections", "p-a", "1c", {"id_observacion": "1", "rechazos": 2}, key=("1",))
    with pytest.raises(IntegrityError, match="different values"):
        store.dedupe()
    store.close()
