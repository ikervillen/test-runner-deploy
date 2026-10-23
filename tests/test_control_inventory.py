import sqlite3

from ingesta_delta_lake.control_inventory import inventory


def test_inventory_reports_allocation_without_changing_rows(tmp_path):
    path = tmp_path / "control.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE business(id INTEGER PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO business VALUES(1,?)", ("large" * 1000,))
    before = path.read_bytes()
    result = inventory(path)
    assert result["bytes"] == len(before)
    assert result["allocated_bytes"] >= result["bytes"]
    assert any(o["name"] == "business" for o in result["objects"])
    assert path.read_bytes() == before


def test_inventory_can_bound_optional_allocation(tmp_path):
    path = tmp_path / "control.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE payload(value TEXT)")
        db.executemany("INSERT INTO payload VALUES(?)", [("x" * 10000,)] * 100)
    result = inventory(path, seconds=0.01)
    assert result["objects"]
