"""Live-owned maintenance queue; bounded discovery of immutable history receipts."""

import json
from dataclasses import asdict, replace

from .common import IntegrityError, canonical
from .pipeline_receipts import ArchiveReceipts


class RetirementQueue:
    def __init__(self, intake, historical):
        self.db = intake.db
        self.reader = ArchiveReceipts(historical)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS retirement_frontiers(head TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS retirement_receipts(
                operation TEXT PRIMARY KEY,head TEXT NOT NULL,family TEXT NOT NULL,
                complete INTEGER NOT NULL DEFAULT 0,last_turn INTEGER NOT NULL DEFAULT 0);
        """)

    def discover(self, snapshot, *, policy):
        head = snapshot.manifest.get("receipt_head")
        if not head:
            return 0
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO retirement_frontiers VALUES(?)", (canonical(head),)
            )
        count = 0
        for _ in range(policy.archive_publications_per_turn):
            frontier = self.db.execute(
                "SELECT head FROM retirement_frontiers ORDER BY rowid LIMIT 1"
            ).fetchone()
            if frontier is None:
                break
            head = json.loads(frontier[0])
            if self.db.execute(
                "SELECT 1 FROM retirement_receipts WHERE operation=?", (head["operation_id"],)
            ).fetchone():
                with self.db:
                    self.db.execute("DELETE FROM retirement_frontiers WHERE head=?", (frontier[0],))
                continue
            receipt = self.reader.read(head, max_rows=policy.archive_batch_rows)
            if receipt["owner"] != snapshot.manifest["owner"]:
                raise IntegrityError("Retirement receipt chain crosses archive owners")
            with self.db:
                self.db.execute(
                    "INSERT INTO retirement_receipts(operation,head,family) VALUES(?,?,?)",
                    (head["operation_id"], frontier[0], receipt["family"]),
                )
                self.db.execute("DELETE FROM retirement_frontiers WHERE head=?", (frontier[0],))
                if receipt["parent"]:
                    self.db.execute(
                        "INSERT OR IGNORE INTO retirement_frontiers VALUES(?)",
                        (canonical(receipt["parent"]),),
                    )
            count += 1
        return count

    def next_config(self, config, *, policy, unavailable):
        for row in self.db.execute(
            "SELECT operation,head,family FROM retirement_receipts WHERE complete=0 "
            "ORDER BY last_turn,rowid"
        ):
            if row["family"] in unavailable:
                continue
            with self.db:
                self.db.execute(
                    "UPDATE retirement_receipts SET last_turn="
                    "(SELECT coalesce(max(last_turn),0)+1 FROM retirement_receipts) "
                    "WHERE operation=?",
                    (row["operation"],),
                )
            return replace(
                config,
                family=row["family"],
                intake_ids=(),
                retirement_job={"receipt": json.loads(row["head"]), "policy": asdict(policy)},
                reviews_only=False,
            )
        return None

    def acknowledge(self, result):
        if result.get("status") not in {"RETIRED", "RETIREMENT_UNCHANGED"} or result.get(
            "publication_error"
        ):
            return
        with self.db:
            self.db.execute(
                "UPDATE retirement_receipts SET complete=1 WHERE operation=? AND head=?",
                (
                    result["retirement_receipt"]["operation_id"],
                    canonical(result["retirement_receipt"]),
                ),
            )
