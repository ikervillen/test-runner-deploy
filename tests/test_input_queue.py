import sqlite3
from contextlib import closing

from ingesta_delta_lake.common import canonical
from ingesta_delta_lake.input_queue import Captures, Consumed
from ingesta_delta_lake.sources import Snapshot


def test_pending_iteration_does_not_materialize_consumed_catalog():
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript("""
            CREATE TABLE input_versions(origin TEXT,sha256 TEXT,descriptor_json TEXT,
                                        PRIMARY KEY(origin,sha256));
            CREATE TABLE pending_inputs(origin TEXT,sha256 TEXT,PRIMARY KEY(origin,sha256));
            CREATE TABLE consumed_inputs(origin TEXT,sha256 TEXT,batch_id TEXT,
                                         PRIMARY KEY(origin,sha256));
        """)
        for index in range(1000):
            sha = f"{index:064x}"
            snapshot = Snapshot("AV21", "source", sha, "bronze", 0, 0, "now", None)
            db.execute(
                "INSERT INTO input_versions VALUES(?,?,?)",
                ("AV21", sha, canonical(snapshot.descriptor())),
            )
            if index:
                db.execute("INSERT INTO consumed_inputs VALUES(?,?,'done')", ("AV21", sha))
            else:
                db.execute("INSERT INTO pending_inputs VALUES(?,?)", ("AV21", sha))
        queue = Captures(db)
        consumed = Consumed(db)
        assert len(list(queue.items())) == 1
        assert len(consumed) == 999
        assert ("AV21", f"{999:064x}") in consumed
        assert queue.get(("AV21", f"{999:064x}")).sha256 == f"{999:064x}"
        assert ("AV21", "0" * 64) not in consumed
