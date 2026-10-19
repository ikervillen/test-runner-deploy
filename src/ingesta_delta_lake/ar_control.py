"""Intenciones INSERT/CLOSE por identidad compuesta y referencias AR pendientes."""

import json
from datetime import datetime

from . import lista_ar as ar
from .common import canonical, now
from .control import Control


class ArControl(Control):
    def __init__(self, config):
        super().__init__(config)
        self.review_hours = config.pending_review_after_hours
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS ar_intents(
              operation_id TEXT NOT NULL, record_key TEXT NOT NULL, key_json TEXT NOT NULL,
              kind TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL,
              before_hash TEXT, after_hash TEXT NOT NULL, state TEXT NOT NULL,
              delta_version INTEGER, PRIMARY KEY(operation_id,record_key));
            CREATE TABLE IF NOT EXISTS dependency_events(
              issue_id TEXT NOT NULL REFERENCES issues(issue_id), batch_id TEXT NOT NULL,
              event TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(issue_id,batch_id,event));
        """)

    def cases(self):
        return [
            (r["issue_id"], r["reason"], r["status"], json.loads(r["payload_json"]))
            for r in self.db.execute(
                "SELECT * FROM issues WHERE status!='RESOLVED' ORDER BY issue_id"
            )
        ]

    def held(self):
        return {
            tuple(p["key"])
            for _, reason, _, p in self.cases()
            if reason != "REFERENCE_PENDING" and "key" in p
        }

    def dependency_candidates(self):
        return [p for _, reason, _, p in self.cases() if reason == "REFERENCE_PENDING"]

    def prepare(self, batch_id, operation_id, prepared):
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,'PREPARED',?,?,NULL,NULL)",
                (batch_id, operation_id, canonical(prepared), now()),
            )
            for kind, rows in (("INSERT", prepared["inserts"]), ("CLOSE", prepared["updates"])):
                self.db.executemany(
                    "INSERT INTO ar_intents VALUES(?,?,?,?,?,?,?,?,'PREPARED',NULL)",
                    [
                        (
                            operation_id,
                            r["record_key"],
                            canonical(ar.key(r)),
                            kind,
                            canonical(r["before"]) if "before" in r else None,
                            canonical(r),
                            ar.business_hash(r["before"]) if "before" in r else None,
                            ar.business_hash(r),
                        )
                        for r in rows
                    ],
                )

    def confirm(self, batch_id, version):
        with self.db:
            batch = self.batch(batch_id)
            self.db.execute(
                "UPDATE batches SET state=?,confirmed_at=?,delta_version=? WHERE batch_id=?",
                (self.prepared(batch)["outcome_state"], now(), version, batch_id),
            )
            self.db.execute(
                "UPDATE ar_intents SET state='CONFIRMED',delta_version=? WHERE operation_id=?",
                (version, batch["operation_id"]),
            )
            self.db.execute(
                "INSERT INTO settings VALUES('last_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )

    def observe(self, batch_id, issues, plant):
        self.rejected(batch_id, {}, issues, plant, create_batch=False)
        with self.db:
            for identifier, reason, _, _payload in self.cases():
                if reason != "REFERENCE_PENDING":
                    continue
                first = self.db.execute(
                    "SELECT first_seen FROM issues WHERE issue_id=?", (identifier,)
                ).fetchone()[0]
                hours = (
                    datetime.fromisoformat(now()) - datetime.fromisoformat(first)
                ).total_seconds() / 3600
                state = (
                    "DEPENDENCY_ESCALATED"
                    if self.review_hours is not None and hours >= self.review_hours
                    else "DEPENDENCY_PENDING"
                )
                self.db.execute("UPDATE issues SET status=? WHERE issue_id=?", (state, identifier))
                self.db.execute(
                    "INSERT OR IGNORE INTO dependency_events VALUES(?,?,?,?)",
                    (identifier, batch_id, state, now()),
                )

    def resolve_dependencies(self, rows, references, batch_id):
        with self.db:
            held = self.held()
            for identifier, reason, _, payload in self.cases():
                if reason != "REFERENCE_PENDING":
                    continue
                candidate = payload["candidate"]
                accepted = rows.get(ar.key(candidate))
                if (
                    accepted is None
                    or ar.key(candidate) in held
                    or ar.reference_gaps(candidate, references)
                    or candidate["FECHA_OFF"] not in (None, accepted["FECHA_OFF"])
                ):
                    continue
                self.db.execute(
                    "UPDATE issues SET status='RESOLVED' WHERE issue_id=?", (identifier,)
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO dependency_events VALUES(?,?,'RESOLVED',?)",
                    (identifier, batch_id, now()),
                )

    def counts(self):
        cases = self.cases()
        first = [
            self.db.execute("SELECT first_seen FROM issues WHERE issue_id=?", (i,)).fetchone()[0]
            for i, r, _, _ in cases
            if r == "REFERENCE_PENDING"
        ]
        return {
            "open_dependencies": sum(r == "REFERENCE_PENDING" for _, r, _, _ in cases),
            "open_reviews": sum(r != "REFERENCE_PENDING" for _, r, _, _ in cases),
            "escalated_dependencies": sum(s == "DEPENDENCY_ESCALATED" for _, _, s, _ in cases),
            "oldest_dependency_hours": max(
                (
                    (datetime.fromisoformat(now()) - datetime.fromisoformat(t)).total_seconds()
                    / 3600
                    for t in first
                ),
                default=0.0,
            ),
        }
