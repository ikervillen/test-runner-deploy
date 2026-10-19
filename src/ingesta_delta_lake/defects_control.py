"""Auditoría por clave compuesta y revisión persistente de filas/bases MIA."""

import json

from . import lista_defectos as defects
from .common import canonical, now
from .control import Control


class DefectsControl(Control):
    model = defects

    def __init__(self, config):
        super().__init__(config)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS observation_intents(
              operation_id TEXT NOT NULL, record_key TEXT NOT NULL, key_json TEXT NOT NULL,
              signature_json TEXT NOT NULL, group_json TEXT NOT NULL, after_json TEXT NOT NULL,
              business_hash TEXT NOT NULL, state TEXT NOT NULL, delta_version INTEGER,
              PRIMARY KEY(operation_id,record_key));
            CREATE TABLE IF NOT EXISTS inspection_groups(
              batch_id TEXT NOT NULL, group_id TEXT NOT NULL, payload_json TEXT NOT NULL,
              PRIMARY KEY(batch_id,group_id));
        """)

    def cases(self):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT payload_json FROM issues WHERE status!='RESOLVED' ORDER BY issue_id"
            )
        ]

    def held(self):
        keys, signatures, groups = set(), set(), set()
        for issue in self.cases():
            if "key" in issue:
                keys.add(tuple(issue["key"]))
            if issue["reason"] == "COUNTER_REVISION":
                signatures.add(tuple(issue["signature"]))
            if "group" in issue and (
                issue["reason"] in {"INVALID_VALUE", "INSPECTION_GROUP_CONFLICT"}
                or len(issue.get("inspection_values", [])) > 1
            ):
                groups.add(tuple(issue["group"]))
        return keys, signatures, groups

    def prepare(self, batch_id, operation_id, prepared):
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,'PREPARED',?,?,NULL,NULL)",
                (batch_id, operation_id, canonical(prepared), now()),
            )
            self.db.executemany(
                "INSERT INTO observation_intents VALUES(?,?,?,?,?,?,?,'PREPARED',NULL)",
                [
                    (
                        operation_id,
                        r["record_key"],
                        canonical(self.model.key(r)),
                        canonical(self.model.signature(r)),
                        canonical(self.model.group(r)),
                        canonical(r),
                        self.model.row_hash(r),
                    )
                    for r in prepared["inserts"]
                ],
            )

    def confirm(self, batch_id, version):
        with self.db:
            batch = self.batch(batch_id)
            prepared = self.prepared(batch)
            self.db.execute(
                "UPDATE batches SET state=?,confirmed_at=?,delta_version=? WHERE batch_id=?",
                (prepared["outcome_state"], now(), version, batch_id),
            )
            self.db.execute(
                "UPDATE observation_intents SET state='CONFIRMED',delta_version=? "
                "WHERE operation_id=?",
                (version, batch["operation_id"]),
            )
            self.db.execute(
                "INSERT INTO settings VALUES('last_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO inspection_groups VALUES(?,?,?)",
                [(batch_id, g["group_id"], canonical(g)) for g in prepared["inspection_groups"]],
            )
