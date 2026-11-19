"""Disk spill for accepted facts, so a publication never holds every row as a dict."""

from __future__ import annotations

import pickle
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

from .common import IntegrityError


class FactStore:
    """One SQLite file of pickled rows. Readers see a production only after `ready`."""

    def __init__(self, path: Path, *, max_bytes: int | None = None):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # The Delta writer pulls batches from another thread. Reads stay on this connection.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        if max_bytes is not None:
            page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
            page_limit = max_bytes // page_size
            if page_limit < 1:
                self._conn.close()
                raise IntegrityError("Gold spill budget must cover at least one SQLite page")
            self._conn.execute(f"PRAGMA max_page_count={page_limit}")
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
                key_identity TEXT,
                payload BLOB NOT NULL
            )
            """
        )
        self._pending: list[tuple[str, str, str, str | None, bytes]] = []

    def add(
        self,
        family: str,
        production_id: str,
        obs_id: str,
        row: dict,
        key: tuple | None = None,
    ) -> None:
        identity = None if key is None else repr(key)
        self._pending.append(
            (family, production_id, str(obs_id), identity, pickle.dumps(row, protocol=5))
        )
        if len(self._pending) >= 2000:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        self._conn.executemany(
            """
            INSERT INTO fact (family, production_id, obs_id, key_identity, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            self._pending,
        )
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

    def dedupe(self) -> None:
        """Keep one copy of an identical output key. A real disagreement is an error."""
        self.flush()
        conflict = self._conn.execute(
            """
            SELECT family FROM fact
            WHERE key_identity IS NOT NULL
            GROUP BY family, key_identity
            HAVING COUNT(DISTINCT payload) > 1
            LIMIT 1
            """
        ).fetchone()
        if conflict:
            raise IntegrityError(f"{conflict[0]}: different values for one output key")
        self._conn.execute(
            """
            DELETE FROM fact
            WHERE key_identity IS NOT NULL
              AND rowid NOT IN (
                    SELECT MIN(rowid) FROM fact
                    WHERE key_identity IS NOT NULL
                    GROUP BY family, key_identity
              )
            """
        )
        self._conn.commit()

    def count(self, family: str) -> int:
        self.flush()
        return self._conn.execute(
            "SELECT COUNT(*) FROM fact WHERE family = ?", (family,)
        ).fetchone()[0]

    def families(self) -> set[str]:
        self.flush()
        return {row[0] for row in self._conn.execute("SELECT DISTINCT family FROM fact")}

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
        return list(self.iter_rows(family))

    def iter_rows(self, family: str) -> Iterator[dict]:
        self.flush()
        cursor = self._conn.execute(
            """
            SELECT payload FROM fact
            WHERE family = ?
            ORDER BY CASE WHEN key_identity IS NULL THEN obs_id ELSE key_identity END
            """,
            (family,),
        )
        while True:
            chunk = cursor.fetchmany(1000)
            if not chunk:
                break
            for (blob,) in chunk:
                yield pickle.loads(blob)

    def batches(self, family: str, size: int = 1000) -> Iterator[list[dict]]:
        batch: list[dict] = []
        for row in self.iter_rows(family):
            batch.append(row)
            if len(batch) == size:
                yield batch
                batch = []
        if batch:
            yield batch

    def rewrite(self, family: str, fn: Callable[[dict], dict]) -> None:
        """Replace each payload of one family. Only one batch of rows is in memory."""
        self.flush()
        rowids = [
            row[0]
            for row in self._conn.execute("SELECT rowid FROM fact WHERE family = ?", (family,))
        ]
        for start in range(0, len(rowids), 1000):
            chunk = rowids[start : start + 1000]
            marks = ",".join("?" for _ in chunk)
            current = self._conn.execute(
                f"SELECT rowid, payload FROM fact WHERE rowid IN ({marks})", chunk
            ).fetchall()
            updates = [
                (pickle.dumps(fn(pickle.loads(blob)), protocol=5), rowid) for rowid, blob in current
            ]
            self._conn.executemany("UPDATE fact SET payload = ? WHERE rowid = ?", updates)
        self._conn.commit()

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        try:
            self.flush()
        finally:
            self._conn.close()
            self._closed = True
