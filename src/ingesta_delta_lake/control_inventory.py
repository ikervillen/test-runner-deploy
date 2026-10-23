"""Read-only, time-bounded control inventory; never allocates business payloads."""

import sqlite3
import time
from contextlib import closing


def inventory(path, *, seconds=3):
    deadline = time.monotonic() + seconds
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        result = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "wal_bytes": path.with_name(path.name + "-wal").stat().st_size
            if path.with_name(path.name + "-wal").exists()
            else 0,
            "allocated_bytes": page_size * db.execute("PRAGMA page_count").fetchone()[0],
            "reusable_bytes": page_size * db.execute("PRAGMA freelist_count").fetchone()[0],
            "objects": [],
            "allocation_complete": False,
        }
        objects = db.execute(
            "SELECT type,name,tbl_name FROM sqlite_master "
            "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        result["objects"] = [dict(zip(("type", "name", "table"), r, strict=True)) for r in objects]
        try:
            allocation = dict(db.execute("SELECT name,sum(pgsize) FROM dbstat GROUP BY name"))
            for entry in result["objects"]:
                entry["bytes"] = allocation.get(entry["name"], 0)
            result["allocation_complete"] = True
        except sqlite3.OperationalError as exc:
            result["allocation_note"] = str(exc)
        return result
