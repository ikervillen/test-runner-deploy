import sqlite3
from contextlib import closing

from ingesta_delta_lake.maintenance import replace_control
from ingesta_delta_lake.service import run


def test_reformulation_rebuilds_an_unused_damaged_source_index(config, source):
    run(config)
    with closing(sqlite3.connect(config.control_path)) as db:
        db.execute("CREATE INDEX unused_attempt_status ON attempts(status)")
        db.commit()
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        index_page = db.execute(
            "SELECT rootpage FROM sqlite_master WHERE name='unused_attempt_status'"
        ).fetchone()[0]
        assert db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    # Only this disposable index is damaged, never the source business table.
    with config.control_path.open("r+b") as stream:
        stream.seek((index_page - 1) * page_size)
        stream.write(b"\x00")
    with closing(sqlite3.connect(config.control_path)) as db:
        try:
            checked = db.execute("PRAGMA quick_check").fetchall()
        except sqlite3.DatabaseError:
            checked = [("corrupt",)]
        assert checked != [("ok",)]
    report = replace_control(config, apply=True, reformulate=True)
    assert report["validation"].startswith("source-streamed-tables,")
    assert report["tables"]["format"] == 3
    with closing(sqlite3.connect(config.control_path)) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert (
            db.execute("SELECT count(*) FROM attempts INDEXED BY unused_attempt_status").fetchone()[
                0
            ]
            == 1
        )
    assert run(config)["status"] == "NO_CHANGE"
