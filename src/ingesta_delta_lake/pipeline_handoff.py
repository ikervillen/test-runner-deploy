"""History imports immutable live Bronze; it never opens live checkpoints or Fenix."""

import time
from dataclasses import replace
from datetime import datetime

from . import topology
from .common import IntegrityError
from .contracts import CONTRACTS, DictionaryContract
from .pipeline_bronze import BronzeFeed
from .pipeline_lots import read_manifest
from .sources import publish_capture


class BronzeHandoff:
    def __init__(self, intake, live_storage):
        if intake.lane != "history":
            raise IntegrityError("Only history can import live captures")
        self.intake, self.db, self.live_storage = intake, intake.db, live_storage
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS live_imports(
                input_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
                source_path TEXT NOT NULL, producer_time TEXT,
                family TEXT NOT NULL, eligible INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS imported_aliases ON live_imports(source_path,sequence);
        """)

    def turn(self, config, policy, *, window, clock=time.monotonic):
        began = clock()
        pointer = self.live_storage.exchange / "bronze" / "current.json"
        if not pointer.exists():
            return {"imported": 0, "deferred": 0}
        feed = BronzeFeed(self.live_storage)
        ownership = read_manifest(self.live_storage, "lots")
        strict = ownership is not None and ownership["frontier"] is not None
        transferred = (
            {
                unit["input_id"]
                for lot in ownership["lots"]
                if lot["state"] == "TRANSFERRED_FOR_REVIEW"
                for unit in lot["inputs"]
                if unit["state"] != "HANDLED"
            }
            if strict
            else set()
        )
        if feed.lane != "live":
            raise IntegrityError("Bronze handoff requires the live producer")
        previous = self.intake.setting("live_import_identity")
        if previous not in (None, feed.identity):
            raise IntegrityError("Live Bronze producer changed; migrate explicitly")
        self.intake.set("live_import_identity", feed.identity)
        cursor = int(self.intake.setting("live_import_sequence") or 0)
        imported = 0
        for item in feed.captures(after=cursor, limit=policy.history_batch_files):
            if clock() - began >= policy.history_turn_seconds:
                break
            snapshot = item.snapshot
            if snapshot.origin not in dict(config.sources):
                raise IntegrityError("Live capture belongs to an unknown Fenix origin")
            # Mark deferred before creating any input that a transform could select.
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO live_imports VALUES(?,?,?,?,?,0)",
                    (
                        item.input_id,
                        item.sequence,
                        snapshot.source_path,
                        snapshot.producer_time,
                        item.family,
                    ),
                )
            data = feed.read(item, max_file_bytes=policy.max_file_bytes)
            target = (
                config.bronze_root
                / config.plant
                / snapshot.origin
                / item.family
                / snapshot.received_at[:10]
                / (snapshot.sha256 + ".csv")
            )
            copied = replace(snapshot, bronze_path=str(target))
            publish_capture(replace(config, family=item.family), copied, data)
            self.intake.remember(item.family, copied)
            self.intake.set("live_import_sequence", str(item.sequence))
            imported += 1
        latest = self.db.execute(
            "SELECT max(producer_time) FROM live_imports WHERE instr(producer_time,'T')>0"
        ).fetchone()[0]
        if latest:
            known = policy.source_window(datetime.fromisoformat(latest))
            if window is None or known.start > window.start:
                window = known
        with self.db:
            for row in self.db.execute("SELECT * FROM live_imports WHERE eligible=0").fetchall():
                stamp = row["producer_time"]
                contract = CONTRACTS.get(row["family"])
                reference = (
                    isinstance(contract, DictionaryContract) or row["family"] in topology.FAMILIES
                )
                eligible = reference
                if strict:
                    eligible = row["input_id"] in transferred or (
                        reference and (stamp is None or "T" not in stamp)
                    )
                elif stamp is None:
                    eligible |= (
                        self.db.execute(
                            "SELECT 1 FROM live_imports WHERE source_path=? AND sequence>? LIMIT 1",
                            (row["source_path"], row["sequence"]),
                        ).fetchone()
                        is not None
                    )
                elif window is not None:
                    eligible |= datetime.fromisoformat(stamp) < window.start
                if eligible:
                    self.db.execute(
                        "UPDATE live_imports SET eligible=1 WHERE input_id=?", (row["input_id"],)
                    )
        deferred = self.db.execute("SELECT count(*) FROM live_imports WHERE eligible=0").fetchone()[
            0
        ]
        return {"imported": imported, "deferred": deferred}
