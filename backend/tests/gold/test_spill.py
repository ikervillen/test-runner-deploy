"""Facts stay on disk per production."""

from app.gold.spill import FactStore


def test_one_production_load_does_not_return_the_other(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    store.add("mia_rejections", "p-b", "2", {"id_observacion": "2", "id_produccion": "p-b"})
    store.add("mia_rejections", "p-a", "1", {"id_observacion": "1", "id_produccion": "p-a"})
    store.ready()
    assert store.load("mia_rejections", "p-a") == [{"id_observacion": "1", "id_produccion": "p-a"}]
    assert [r["id_observacion"] for r in store.materialize("mia_rejections")] == ["1", "2"]
    store.close()
