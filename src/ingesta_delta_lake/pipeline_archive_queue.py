"""History-owned archive queue fed exclusively by immutable live publications."""

from .common import IntegrityError
from .pipeline_publications import SilverSnapshot, confirmation_age


class ArchiveQueue:
    def __init__(self, archive, live_storage):
        self.archive, self.live_storage = archive, live_storage
        self.db = archive.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS archive_source(identity TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS discovered_publications(publication TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS publication_frontiers(publication TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS archive_queue(
                source_table TEXT NOT NULL, source_version INTEGER NOT NULL,
                publication TEXT NOT NULL, family TEXT NOT NULL,
                confirmed_at TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0,
                last_turn INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(source_table,source_version));
        """)

    def discover(self, *, limit):
        root = self.live_storage.exchange / "silver"
        if not (root / "current.json").exists():
            return 0
        latest = SilverSnapshot.open(self.live_storage)
        source = latest.manifest["identity"]
        if latest.manifest["lane"] != "live":
            raise IntegrityError("Archive discovery requires the live producer")
        previous = self.db.execute("SELECT identity FROM archive_source").fetchone()
        if previous and previous[0] != source:
            raise IntegrityError("Archive source identity changed; migrate explicitly")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO archive_source VALUES(?)", (source,))
            self.db.execute(
                "INSERT OR IGNORE INTO publication_frontiers VALUES(?)",
                (latest.manifest["publication_id"],),
            )
        discovered = 0
        # Persist each frontier before moving it; restart never loses an ancestor.
        for _ in range(limit):
            frontier = self.db.execute(
                "SELECT publication FROM publication_frontiers ORDER BY rowid LIMIT 1"
            ).fetchone()
            if frontier is None:
                break
            identity = frontier[0]
            if self.db.execute(
                "SELECT 1 FROM discovered_publications WHERE publication=?", (identity,)
            ).fetchone():
                with self.db:
                    self.db.execute(
                        "DELETE FROM publication_frontiers WHERE publication=?", (identity,)
                    )
                continue
            snapshot = SilverSnapshot.open(self.live_storage, publication_id=identity)
            if snapshot.manifest["identity"] != source or snapshot.manifest["lane"] != "live":
                raise IntegrityError("Publication ancestry crosses producers")
            with self.db:
                for family, ref in snapshot.manifest["tables"].items():
                    self.db.execute(
                        "INSERT OR IGNORE INTO archive_queue VALUES(?,?,?,?,?,0,0)",
                        (ref["table_id"], ref["version"], identity, family, ref["confirmed_at"]),
                    )
                self.db.execute("INSERT INTO discovered_publications VALUES(?)", (identity,))
                self.db.execute(
                    "DELETE FROM publication_frontiers WHERE publication=?", (identity,)
                )
                if snapshot.manifest["parent"]:
                    self.db.execute(
                        "INSERT OR IGNORE INTO publication_frontiers VALUES(?)",
                        (snapshot.manifest["parent"],),
                    )
            discovered += 1
        return discovered

    def turn(self, *, policy, technical_now):
        discovered = self.discover(limit=policy.archive_publications_per_turn)
        pending = self.db.execute(
            "SELECT * FROM archive_queue WHERE complete=0 "
            "AND julianday(confirmed_at)<=julianday(?) "
            "ORDER BY last_turn,confirmed_at,source_version LIMIT 1",
            (policy.archive_before(technical_now).isoformat(),),
        ).fetchone()
        if pending is None:
            waiting = self.db.execute(
                "SELECT 1 FROM archive_queue WHERE complete=0 LIMIT 1"
            ).fetchone()
            return {"status": "NOT_DUE" if waiting else "IDLE", "discovered": discovered}
        ref = dict(pending)
        if confirmation_age(ref, technical_now) < policy.retention_seconds:
            return {"status": "NOT_DUE", "discovered": discovered}
        snapshot = SilverSnapshot.open(self.live_storage, publication_id=ref["publication"])
        result = self.archive.copy(
            snapshot, ref["family"], policy=policy, technical_now=technical_now
        )
        with self.db:
            self.db.execute(
                "UPDATE archive_queue SET complete=?,last_turn="
                "(SELECT coalesce(max(last_turn),0)+1 FROM archive_queue) "
                "WHERE source_table=? AND source_version=?",
                (int(bool(result.get("complete"))), ref["source_table"], ref["source_version"]),
            )
        return {"family": ref["family"], "discovered": discovered, **result}
