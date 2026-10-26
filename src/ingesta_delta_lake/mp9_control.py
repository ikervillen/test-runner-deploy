"""Intenciones de cierres, publicación parcial y dependencias persistentes por identidad."""

import json
from datetime import datetime

from . import mp9
from .common import canonical, now
from .control import Control

DEPENDENCIES = {"MODEL_PENDING", "ZC_PENDING"}
CONFLICTS = {"CONFLICT", "RELATION_CONFLICT", "REVIEW_REQUIRED"}


class Mp9Control(Control):
    def __init__(self, config):
        super().__init__(config)
        self.review_hours = config.pending_review_after_hours
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS change_intents(
              operation_id TEXT NOT NULL, origin TEXT NOT NULL, record_id INTEGER NOT NULL,
              kind TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL,
              before_hash TEXT, after_hash TEXT NOT NULL, state TEXT NOT NULL,
              delta_version INTEGER, PRIMARY KEY(operation_id, origin, record_id));
            CREATE TABLE IF NOT EXISTS dependency_events(
              issue_id TEXT NOT NULL REFERENCES issues(issue_id), batch_id TEXT NOT NULL,
              event TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(issue_id,batch_id,event));
        """)

    def prepare(self, batch_id, operation_id, prepared):
        if self.prepare_compact(batch_id, operation_id, prepared):
            return
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,'PREPARED',?,?,NULL,NULL)",
                (batch_id, operation_id, canonical(prepared), now()),
            )
            for kind, rows in (("INSERT", prepared["inserts"]), ("CLOSE", prepared["updates"])):
                for row in rows:
                    old = row.get("before")
                    self.db.execute(
                        "INSERT INTO change_intents VALUES(?,?,?,?,?,?,?,?, 'PREPARED',NULL)",
                        (
                            operation_id,
                            row["origen_fenix"],
                            row["ID"],
                            "CONFIRM_ZF" if old and old["FECHA_OFF"] == row["FECHA_OFF"] else kind,
                            canonical(old) if old else None,
                            canonical(row),
                            mp9.business_hash(old) if old else None,
                            mp9.business_hash(row),
                        ),
                    )

    def confirm(self, batch_id, version):
        with self.db:
            batch = self.batch(batch_id)
            outcome = self.prepared(batch)["outcome_state"]
            self.db.execute(
                "UPDATE batches SET state=?,confirmed_at=?,delta_version=? WHERE batch_id=?",
                (outcome, now(), version, batch_id),
            )
            self.db.execute(
                "UPDATE change_intents SET state='CONFIRMED',delta_version=? WHERE operation_id=?",
                (version, batch["operation_id"]),
            )
            self.db.execute(
                "INSERT INTO settings VALUES('last_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )

    def cases(self):
        return [
            (r["issue_id"], r["reason"], r["status"], json.loads(r["payload_json"]))
            for r in self.db.execute("SELECT * FROM issues WHERE status != 'RESOLVED'")
        ]

    def held(self):
        return {
            (p["origen_fenix"], p["ID"])
            for _, reason, _, p in self.cases()
            if reason in CONFLICTS and (reason == "RELATION_CONFLICT" or not self.lifecycle_case(p))
        }

    @staticmethod
    def case_rows(payload):
        return [
            r
            for r in [
                payload.get("accepted"),
                payload.get("candidate"),
                *payload.get("candidates", []),
            ]
            if r is not None
        ]

    @classmethod
    def lifecycle_case(cls, payload):
        rows = cls.case_rows(payload)
        return bool(rows) and not mp9.consolidate(rows)[1]

    def dependency_candidates(self):
        return [p for _, reason, _, p in self.cases() if reason in DEPENDENCIES]

    def retry_candidates(self):
        """Revisit stored evidence too: a policy upgrade must not need a new CSV."""
        return [
            p
            for _, reason, _, p in self.cases()
            if reason in DEPENDENCIES
            or (reason in {"CONFLICT", "REVIEW_REQUIRED"} and self.lifecycle_case(p))
        ]

    def observe(self, batch_id, prepared, issues, plant):
        self.rejected(batch_id, prepared, issues, plant, create_batch=False)
        with self.db:
            for identifier, reason, _state, _payload in self.cases():
                if reason not in DEPENDENCIES:
                    continue
                first = self.db.execute(
                    "SELECT first_seen FROM issues WHERE issue_id=?", (identifier,)
                ).fetchone()[0]
                hours = (
                    datetime.fromisoformat(now()) - datetime.fromisoformat(first)
                ).total_seconds() / 3600
                escalated = self.review_hours is not None and hours >= self.review_hours
                new_state = "DEPENDENCY_ESCALATED" if escalated else "DEPENDENCY_PENDING"
                self.db.execute(
                    "UPDATE issues SET status=? WHERE issue_id=?", (new_state, identifier)
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO dependency_events VALUES(?,?,?,?)",
                    (identifier, batch_id, new_state, now()),
                )

    def resolve_dependencies(self, rows, batch_id):
        held = self.held()
        with self.db:
            for identifier, reason, _, payload in self.cases():
                if reason not in DEPENDENCIES | {"CONFLICT", "REVIEW_REQUIRED"}:
                    continue
                candidates = self.case_rows(payload)
                if not candidates or mp9.key(candidates[0]) in held:
                    continue
                accepted = rows.get(mp9.key(candidates[0]))
                if accepted is None or not all(mp9.advances(r, accepted) for r in candidates):
                    continue
                payload = {
                    **payload,
                    "resolution": {
                        "type": "AUTOMATIC_LIFECYCLE",
                        "rule": mp9.LIFECYCLE_RULE,
                        "batch_id": batch_id,
                        "delta_version": self.setting("last_version"),
                        "at": now(),
                    },
                }
                self.db.execute(
                    "UPDATE issues SET status='RESOLVED',payload_json=? WHERE issue_id=?",
                    (canonical(payload), identifier),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO dependency_events VALUES(?,?,'RESOLVED',?)",
                    (identifier, batch_id, now()),
                )

    def counts(self):
        cases = self.cases()
        first_seen = [
            self.db.execute("SELECT first_seen FROM issues WHERE issue_id=?", (i,)).fetchone()[0]
            for i, reason, _, _ in cases
            if reason in DEPENDENCIES
        ]
        return {
            "oldest_dependency_hours": max(
                (
                    (datetime.fromisoformat(now()) - datetime.fromisoformat(t)).total_seconds()
                    / 3600
                    for t in first_seen
                ),
                default=0.0,
            ),
            "open_dependencies": sum(r in DEPENDENCIES for _, r, _, _ in cases),
            "escalated_dependencies": sum(s == "DEPENDENCY_ESCALATED" for _, _, s, _ in cases),
            "open_reviews": sum(r not in DEPENDENCIES for _, r, _, _ in cases),
        }
