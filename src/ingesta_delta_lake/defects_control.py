"""Auditoría por clave compuesta y revisión persistente de filas/bases MIA."""

import json
import time

from . import lista_defectos as defects
from .common import IntegrityError, canonical, now
from .control import Control
from .mia_scope import group_holds
from .sources import Snapshot


class DefectsControl(Control):
    model = defects

    def __init__(self, config):
        super().__init__(config)
        processing = canonical(
            {
                "contract": config.contract.name,
                "encoding": config.encoding,
                "timestamp_policy": config.timestamp_policy,
            }
        )
        if self.setting("input_processing_binding") not in (None, processing):
            self.close()
            raise IntegrityError("Input interpretation changed; an explicit migration is required")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO settings VALUES('input_processing_binding',?)", (processing,)
            )
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS observation_intents(
              operation_id TEXT NOT NULL, record_key TEXT NOT NULL, key_json TEXT NOT NULL,
              signature_json TEXT NOT NULL, group_json TEXT NOT NULL, after_json TEXT NOT NULL,
              business_hash TEXT NOT NULL, state TEXT NOT NULL, delta_version INTEGER,
              PRIMARY KEY(operation_id,record_key));
            CREATE TABLE IF NOT EXISTS inspection_groups(
              batch_id TEXT NOT NULL, group_id TEXT NOT NULL, payload_json TEXT NOT NULL,
              PRIMARY KEY(batch_id,group_id));
            CREATE TABLE IF NOT EXISTS consumed_inputs(
              origin TEXT NOT NULL, sha256 TEXT NOT NULL,
              batch_id TEXT NOT NULL REFERENCES batches(batch_id),
              PRIMARY KEY(origin,sha256));
            CREATE TABLE IF NOT EXISTS input_versions(
              origin TEXT NOT NULL, sha256 TEXT NOT NULL, descriptor_json TEXT NOT NULL,
              PRIMARY KEY(origin,sha256));
            CREATE TABLE IF NOT EXISTS inspection_current(
              group_id TEXT PRIMARY KEY, origin TEXT NOT NULL, payload_json TEXT NOT NULL,
              status TEXT NOT NULL, inspected INTEGER, row_count INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS inspection_current_origin ON inspection_current(origin);
        """)
        if self.setting("input_queue_migrated") is None:
            with self.db:
                for (text,) in self.db.execute(
                    "SELECT descriptor_json FROM receipts ORDER BY rowid"
                ):
                    snapshot = Snapshot(**json.loads(text))
                    self.db.execute(
                        "INSERT OR IGNORE INTO input_versions VALUES(?,?,?)",
                        (snapshot.origin, snapshot.sha256, text),
                    )
                self.db.execute("INSERT INTO settings VALUES('input_queue_migrated','1')")

    def receipts(self, attempt, snapshots):
        # Capture acknowledgement and enqueue are one durable SQLite transaction.
        with self.db:
            self.db.executemany(
                "INSERT INTO receipts VALUES(?,?,?)",
                [(attempt, s.source_path, canonical(s.descriptor())) for s in snapshots],
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO input_versions VALUES(?,?,?)",
                [(s.origin, s.sha256, canonical(s.descriptor())) for s in snapshots],
            )

    def consumed_inputs(self):
        return {(r[0], r[1]) for r in self.db.execute("SELECT origin,sha256 FROM consumed_inputs")}

    def progress(self, consumed, remaining):
        stamp = time.time()
        previous = json.loads(self.setting("mia_progress") or "null")
        rate = None
        if previous and consumed > previous["consumed"] and stamp > previous["at"]:
            rate = (consumed - previous["consumed"]) / (stamp - previous["at"])
        with self.db:
            self.db.execute(
                "INSERT INTO settings VALUES('mia_progress',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (canonical({"at": stamp, "consumed": consumed}),),
            )
        return {
            "consumed_versions": consumed,
            "versions_per_wall_second": rate,
            "estimated_backlog_seconds": remaining / rate if rate else None,
        }

    def acknowledge_inputs(self, batch_id, snapshots):
        batch = self.db.execute(
            "SELECT state FROM batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if batch is None or batch[0] not in {"COMPLETED", "PARTIAL"}:
            raise IntegrityError("Cannot acknowledge input before durable batch confirmation")
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO consumed_inputs VALUES(?,?,?)",
                [(s.origin, s.sha256, batch_id) for s in snapshots],
            )
            if snapshots:
                self.db.execute(
                    "INSERT INTO settings VALUES('last_mia_origin',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (snapshots[-1].origin,),
                )

    def captured_inputs(self):
        """Receipts are also the durable queue; include files no longer in /input."""
        versions = {}
        for (text,) in self.db.execute("SELECT descriptor_json FROM input_versions ORDER BY rowid"):
            snapshot = Snapshot(**json.loads(text))
            versions.setdefault((snapshot.origin, snapshot.sha256), snapshot)
        return versions

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

    def ensure_inspection_index(self, version, published_rows):
        """Adopt the last complete legacy group snapshot once; retain all old audit."""
        if self.setting("inspection_index_version") == str(version):
            if (
                self.db.execute(
                    "SELECT coalesce(sum(row_count),0) FROM inspection_current"
                ).fetchone()[0]
                != published_rows
            ):
                raise IntegrityError("MIA inspection index row coverage is inconsistent")
            return
        latest = self.db.execute(
            "SELECT batch_id FROM batches WHERE delta_version IS ? "
            "AND state IN ('COMPLETED','PARTIAL') ORDER BY confirmed_at DESC LIMIT 1",
            (version,),
        ).fetchone()
        with self.db:
            self.db.execute("DELETE FROM inspection_current")
            if latest:
                self.db.execute(
                    "INSERT INTO inspection_current SELECT group_id,"
                    "json_extract(payload_json,'$.group[0]'),payload_json,"
                    "json_extract(payload_json,'$.status'),"
                    "json_extract(payload_json,'$.INSPECC'),"
                    "coalesce(json_extract(payload_json,'$.defect_rows'),"
                    "json_extract(payload_json,'$.observation_rows')) "
                    "FROM inspection_groups WHERE batch_id=?",
                    (latest[0],),
                )
            count = self.db.execute(
                "SELECT coalesce(sum(row_count),0) FROM inspection_current"
            ).fetchone()[0]
            if count != published_rows:
                raise IntegrityError("Legacy MIA group snapshot does not cover its Delta version")
            self.update_inspection_holds(group_holds(self.cases()))
            self.db.execute(
                "INSERT INTO settings VALUES('inspection_index_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )

    def update_inspection_holds(self, holds):
        old_groups, old_origins = json.loads(self.setting("inspection_index_holds") or "[[],[]]")
        groups, origins = holds
        reset = (
            "UPDATE inspection_current SET status='RESOLVED',inspected="
            "json_extract(payload_json,'$.accepted_inspection_values[0]') "
        )
        self.db.executemany(
            reset + "WHERE group_id=?", [(g,) for g in set(old_groups) - set(groups)]
        )
        self.db.executemany(
            reset + "WHERE origin=?", [(o,) for o in set(old_origins) - set(origins)]
        )
        self.db.executemany(
            "UPDATE inspection_current SET status='REVIEW',inspected=NULL "
            "WHERE group_id=? AND status!='REVIEW'",
            [(g,) for g in groups],
        )
        self.db.executemany(
            "UPDATE inspection_current SET status='REVIEW',inspected=NULL "
            "WHERE origin=? AND status!='REVIEW'",
            [(o,) for o in origins],
        )
        self.db.execute(
            "INSERT INTO settings VALUES('inspection_index_holds',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (canonical(holds),),
        )

    def inspection_summary(self):
        row = self.db.execute(
            "SELECT count(*),coalesce(sum(status='RESOLVED'),0),"
            "coalesce(sum(CASE WHEN status='RESOLVED' THEN inspected ELSE 0 END),0) "
            "FROM inspection_current"
        ).fetchone()
        return {
            "resolved_groups": row[1],
            "unresolved_groups": row[0] - row[1],
            "resolved_inspection_total": row[2],
        }

    def prepare(self, batch_id, operation_id, prepared):
        if self.prepare_compact(batch_id, operation_id, prepared):
            return
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
            if "read_scope" not in prepared:
                # Legacy full snapshots are needed only for the initial migration.
                # Scoped batches keep their audit in prepared_json and update the
                # current index below; materializing it twice grows without limit.
                self.db.executemany(
                    "INSERT OR IGNORE INTO inspection_groups VALUES(?,?,?)",
                    [
                        (batch_id, g["group_id"], canonical(g))
                        for g in prepared["inspection_groups"]
                    ],
                )
            if "read_scope" in prepared:
                self.db.executemany(
                    "INSERT INTO inspection_current VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(group_id) DO UPDATE SET payload_json=excluded.payload_json,"
                    "status=excluded.status,inspected=excluded.inspected,row_count=excluded.row_count",
                    [
                        (
                            g["group_id"],
                            g["group"][0],
                            canonical(g),
                            g["status"],
                            g["INSPECC"],
                            g.get("defect_rows", g.get("observation_rows")),
                        )
                        for g in prepared["inspection_groups"]
                    ],
                )
                self.update_inspection_holds(prepared["inspection_holds"])
                self.db.execute(
                    "INSERT INTO settings VALUES('inspection_index_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(version),),
                )
