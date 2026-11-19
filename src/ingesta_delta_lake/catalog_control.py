"""Publicaciones coherentes y composición auditada, separadas de las tablas fuente."""

import json

from . import catalog
from .common import canonical, now
from .control import Control


class CatalogControl(Control):
    def __init__(self, config):
        super().__init__(config)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS production_intents(
              operation_id TEXT NOT NULL, production_id TEXT NOT NULL, kind TEXT NOT NULL,
              before_json TEXT, after_json TEXT NOT NULL,
              before_hash TEXT, after_hash TEXT NOT NULL,
              state TEXT NOT NULL, delta_version INTEGER, PRIMARY KEY(operation_id,production_id));
            CREATE TABLE IF NOT EXISTS publications(
              publication_no INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT UNIQUE NOT NULL,
              delta_version INTEGER, source_manifest_json TEXT NOT NULL,
              diagnostics_json TEXT NOT NULL,
              complete INTEGER NOT NULL, confirmed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS composition(
              batch_id TEXT NOT NULL, production_id TEXT NOT NULL, observation_json TEXT NOT NULL,
              PRIMARY KEY(batch_id,production_id));
        """)

    def prepare(self, batch_id, operation_id, prepared):
        if self.prepare_compact(batch_id, operation_id, prepared):
            return
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,'PREPARED',?,?,NULL,NULL)",
                (batch_id, operation_id, canonical(prepared), now()),
            )
            for kind, rows in (("INSERT", prepared["inserts"]), ("COMPLETE", prepared["updates"])):
                for row in rows:
                    old = row.get("before")
                    self.db.execute(
                        "INSERT INTO production_intents VALUES(?,?,?,?,?,?,?,'PREPARED',NULL)",
                        (
                            operation_id,
                            row["id_produccion"],
                            kind,
                            canonical(old) if old else None,
                            canonical(row),
                            catalog.row_hash(old) if old else None,
                            catalog.row_hash(row),
                        ),
                    )

    def confirm(self, batch_id, version):
        with self.db:
            batch = self.batch(batch_id)
            prepared = self.prepared(batch)
            self.db.execute(
                "UPDATE batches SET state='COMPLETED',confirmed_at=?,delta_version=? "
                "WHERE batch_id=?",
                (now(), version, batch_id),
            )
            self.db.execute(
                "UPDATE production_intents SET state='CONFIRMED',delta_version=? "
                "WHERE operation_id=?",
                (version, batch["operation_id"]),
            )
            self.db.execute(
                "INSERT INTO settings VALUES('last_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO publications(batch_id,delta_version,source_manifest_json,"
                "diagnostics_json,complete,confirmed_at) VALUES(?,?,?,?,?,?)",
                (
                    batch_id,
                    version,
                    canonical(prepared["source_manifest"]),
                    canonical(prepared["diagnostics"]),
                    int(prepared["complete"]),
                    now(),
                ),
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO composition VALUES(?,?,?)",
                [
                    (batch_id, identity, canonical(obs))
                    for identity, obs in prepared["observations"].items()
                ],
            )

    def observations(self):
        row = self.db.execute(
            "SELECT batch_id FROM publications ORDER BY publication_no DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {}
        return {
            r["production_id"]: json.loads(r["observation_json"])
            for r in self.db.execute(
                "SELECT * FROM composition WHERE batch_id=?", (row["batch_id"],)
            )
        }

    def historical_mp9_reference(self):
        row = self.db.execute(
            "SELECT source_manifest_json FROM publications ORDER BY publication_no DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]).get("MP9_CAMBIO_MODELO", {}).get("historical_closures")
