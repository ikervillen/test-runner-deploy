"""Disk spill for accepted facts, so a publication never holds every row as a dict."""

from __future__ import annotations

import pickle
import sqlite3
from pathlib import Path


class FactStore:
    """One SQLite file of pickled rows. Readers see a production only after `ready`."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=FILE")
        self._conn.execute("PRAGMA cache_size=-8192")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fact (
                family TEXT NOT NULL,
                production_id TEXT NOT NULL,
                obs_id TEXT NOT NULL,
                payload BLOB NOT NULL
            )
            """
        )
        self._pending: list[tuple[str, str, str, bytes]] = []

    def add(self, family: str, production_id: str, obs_id: str, row: dict) -> None:
        self._pending.append((family, production_id, obs_id, pickle.dumps(row, protocol=5)))
        if len(self._pending) >= 2000:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        self._conn.executemany("INSERT INTO fact VALUES (?, ?, ?, ?)", self._pending)
        self._conn.commit()
        self._pending.clear()

    def ready(self) -> None:
        """Finish writes and index them so other processes can read one production."""
        self.flush()
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS fact_lookup ON fact(family, production_id, obs_id)"
        )
        self._conn.commit()
        self._conn.execute("PRAGMA wal_checkpoint(FULL)")

    def load(self, family: str, production_id: str) -> list[dict]:
        self.flush()
        cursor = self._conn.execute(
            """
            SELECT payload FROM fact
            WHERE family = ? AND production_id = ?
            ORDER BY obs_id
            """,
            (family, production_id),
        )
        return [pickle.loads(blob) for (blob,) in cursor]

    def materialize(self, family: str) -> list[dict]:
        self.flush()
        cursor = self._conn.execute(
            "SELECT payload FROM fact WHERE family = ? ORDER BY obs_id",
            (family,),
        )
        return [pickle.loads(blob) for (blob,) in cursor]

    def close(self) -> None:
        self.flush()
        self._conn.close()
