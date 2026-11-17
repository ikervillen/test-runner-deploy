"""Intenciones y confirmaciones duraderas; nunca asumir atomicidad SQLite/Delta."""

import json
import logging
import sqlite3
import uuid

from .common import IntegrityError, canonical, digest, now
from .config import Config
from .sources import Snapshot


class Control:
    def __init__(self, config: Config):
        self.config = config
        self.contract = config.contract
        self.intent_key = "id_modelo_fenix" if config.family == "SIP_MODELO" else "record_id"
        new_control = not config.control_path.exists()
        self.db = sqlite3.connect(config.control_path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(f"""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(
              attempt_id TEXT PRIMARY KEY, batch_id TEXT, started_at TEXT NOT NULL,
              finished_at TEXT, status TEXT NOT NULL, result_json TEXT);
            CREATE TABLE IF NOT EXISTS receipts(
              attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
              source_path TEXT NOT NULL, descriptor_json TEXT NOT NULL,
              PRIMARY KEY(attempt_id, source_path));
            CREATE TABLE IF NOT EXISTS batches(
              batch_id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL,
              state TEXT NOT NULL, prepared_json TEXT NOT NULL,
              created_at TEXT NOT NULL, confirmed_at TEXT, delta_version INTEGER);
            CREATE TABLE IF NOT EXISTS row_intents(
              operation_id TEXT NOT NULL, {self.intent_key} INTEGER NOT NULL,
              after_json TEXT NOT NULL, state TEXT NOT NULL, delta_version INTEGER,
              PRIMARY KEY(operation_id, {self.intent_key}));
            CREATE TABLE IF NOT EXISTS issues(
              issue_id TEXT PRIMARY KEY, reason TEXT NOT NULL,
              payload_json TEXT NOT NULL, first_seen TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'PENDING_REVIEW');
            CREATE TABLE IF NOT EXISTS issue_evidence(
              issue_id TEXT NOT NULL REFERENCES issues(issue_id), batch_id TEXT NOT NULL,
              evidence_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
              PRIMARY KEY(issue_id, batch_id, evidence_hash));
            CREATE TABLE IF NOT EXISTS exclusions(
              exclusion_id TEXT PRIMARY KEY, reason TEXT NOT NULL,
              entry_json TEXT NOT NULL, first_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exclusion_evidence(
              exclusion_id TEXT NOT NULL REFERENCES exclusions(exclusion_id),
              batch_id TEXT NOT NULL, evidence_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
              PRIMARY KEY(exclusion_id, batch_id, evidence_hash));
        """)
        binding = canonical(
            {
                "plant": config.plant,
                "silver_root": str(config.silver_root),
                "bronze_root": str(config.bronze_root),
                "contract": self.contract.name,
            }
        )
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES('binding', ?)", (binding,))
            self.db.execute(
                "INSERT OR IGNORE INTO settings VALUES('control_id', ?)", (str(uuid.uuid4()),)
            )
        if self.setting("binding") != binding:
            self.close()
            raise IntegrityError("El control pertenece a otra planta, rutas o contrato")
        self.control_id = self.setting("control_id")
        if new_control and config.control_format == 3:
            with self.db:
                self.db.execute("INSERT INTO settings VALUES('control_format','3')")
        self.compact = self.setting("control_format") == "3"
        if self.setting("control_format") not in (None, "2", "3"):
            self.close()
            raise IntegrityError("Unsupported control format")
        self.work_root = config.control_root / "work" / config.family / self.control_id
        if self.compact:
            from .compact_control import initialize as initialize_compact

            initialize_compact(self.db)
        from .reviews import initialize

        initialize(self.db)
        self.capture_cached = config.family not in {
            "LISTA_RECH",
            "LISTA_DEFECTOS",
            "CATALOGO_PRODUCCIONES",
            "ASIGNACION_PRODUCCION_RAMAL",
        }
        if self.capture_cached:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS capture_cache("
                "origin TEXT NOT NULL,sha256 TEXT NOT NULL,"
                "descriptor_json TEXT NOT NULL,handled INTEGER NOT NULL,PRIMARY KEY(origin,sha256))"
            )
            if self.setting("capture_cache_seeded") is None:
                with self.db:
                    for row in self.db.execute(
                        "SELECT r.descriptor_json,a.status FROM receipts r "
                        "JOIN attempts a USING(attempt_id) ORDER BY r.rowid"
                    ):
                        snap = Snapshot(**json.loads(row[0]))
                        self.db.execute(
                            "INSERT INTO capture_cache VALUES(?,?,?,?) "
                            "ON CONFLICT(origin,sha256) DO UPDATE SET handled="
                            "max(capture_cache.handled,excluded.handled)",
                            (
                                snap.origin,
                                snap.sha256,
                                row[0],
                                int(row[1] not in {"RUNNING", "FAILED"}),
                            ),
                        )
                    self.db.execute("INSERT INTO settings VALUES('capture_cache_seeded','1')")

    def close(self) -> None:
        self.db.close()

    def setting(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def advance_observed_until(self, marks: dict) -> None:
        """History cursor: fecha of rows the backlog has actually stored."""
        self._advance_mark("observed_until", marks)

    def set_live_slot(self, marks: dict) -> None:
        """Live cursor equals the Bronze slot. A clock that ran ahead is corrected."""
        if not marks:
            return
        current = json.loads(self.setting("live_until") or "{}")
        changed = False
        for origin, stamp in marks.items():
            if not isinstance(origin, str) or not isinstance(stamp, str) or len(stamp) != 19:
                continue
            if current.get(origin) != stamp:
                current[origin] = stamp
                changed = True
        if not changed:
            return
        with self.db:
            self.db.execute(
                "INSERT INTO settings VALUES('live_until', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (canonical(current),),
            )

    def _advance_mark(self, key: str, marks: dict) -> None:
        if not marks:
            return
        current = json.loads(self.setting(key) or "{}")
        changed = False
        for origin, stamp in marks.items():
            if not isinstance(origin, str) or not isinstance(stamp, str) or len(stamp) != 19:
                continue
            if current.get(origin) is None or stamp > current[origin]:
                current[origin] = stamp
                changed = True
        if not changed:
            return
        with self.db:
            self.db.execute(
                f"INSERT INTO settings VALUES('{key}', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (canonical(current),),
            )

    def start_attempt(self) -> str:
        attempt = str(uuid.uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO attempts VALUES(?, NULL, ?, NULL, 'RUNNING', NULL)", (attempt, now())
            )
        return attempt

    def finish_attempt(self, attempt: str, result: dict) -> None:
        with self.db:
            self.db.execute(
                "UPDATE attempts SET batch_id=?, finished_at=?, status=?, result_json=? "
                "WHERE attempt_id=?",
                (result.get("batch_id"), now(), result["status"], canonical(result), attempt),
            )
            if self.capture_cached and result["status"] != "FAILED":
                committed = result.get("committed_captures")
                if committed is None:
                    # Families that still commit the whole pending set in one turn.
                    self.db.execute("UPDATE capture_cache SET handled=1 WHERE handled=0")
                else:
                    self.db.executemany(
                        "UPDATE capture_cache SET handled=1 WHERE origin=? AND sha256=?",
                        committed,
                    )
        from .retention import compact_batch
        from .supervision_export import export

        if self.compact and result["status"] != "FAILED":
            from .compact_control import settle

            settle(self, attempt, result)
        elif result.get("batch_id"):
            try:
                compact_batch(self.db, result["batch_id"])
            except Exception as exc:
                # Its transaction rolls back to the complete original payload.
                # A maintenance failure must not undo a confirmed publication.
                logging.getLogger(__name__).warning(
                    "control_compaction_failed family=%s type=%s",
                    self.config.family,
                    type(exc).__name__,
                )

        export(self, self.config, result)

    def receipts(self, attempt: str, snapshots: list[Snapshot]) -> None:
        with self.db:
            if not self.compact:
                self.db.executemany(
                    "INSERT INTO receipts VALUES(?, ?, ?)",
                    [(attempt, s.source_path, canonical(s.descriptor())) for s in snapshots],
                )
            if self.capture_cached:
                self.db.executemany(
                    "INSERT OR IGNORE INTO capture_cache VALUES(?,?,?,0)",
                    [(s.origin, s.sha256, canonical(s.descriptor())) for s in snapshots],
                )

    def known_captures(self, *, pending=False):
        clause = " WHERE handled=0" if pending else ""
        return {
            (s.origin, s.sha256): s
            for (text,) in self.db.execute("SELECT descriptor_json FROM capture_cache" + clause)
            for s in [Snapshot(**json.loads(text))]
        }

    def batch(self, batch_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def pending(self) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN') "
                "ORDER BY created_at, batch_id"
            )
        ]

    def prepare(self, batch_id: str, operation_id: str, prepared: dict) -> None:
        if self.prepare_compact(batch_id, operation_id, prepared):
            return
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES(?, ?, 'PREPARED', ?, ?, NULL, NULL)",
                (batch_id, operation_id, canonical(prepared), now()),
            )
            self.db.executemany(
                "INSERT INTO row_intents VALUES(?, ?, ?, 'PREPARED', NULL)",
                [(operation_id, r[self.contract.key], canonical(r)) for r in prepared["inserts"]],
            )

    def mark(self, batch_id: str, state: str) -> None:
        with self.db:
            self.db.execute("UPDATE batches SET state=? WHERE batch_id=?", (state, batch_id))

    def confirm(self, batch_id: str, version: int | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE batches SET state='COMPLETED', confirmed_at=?, delta_version=? "
                "WHERE batch_id=?",
                (now(), version, batch_id),
            )
            self.db.execute(
                "UPDATE row_intents SET state='CONFIRMED', delta_version=? WHERE operation_id="
                "(SELECT operation_id FROM batches WHERE batch_id=?)",
                (version, batch_id),
            )
            self.db.execute(
                "INSERT INTO settings VALUES('last_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )

    def rejected(
        self,
        batch_id: str,
        prepared: dict,
        issues: list[dict],
        plant: str,
        *,
        create_batch: bool = True,
    ) -> None:
        with self.db:
            if create_batch:
                from .compact_control import blocked_manifest

                self.db.execute(
                    "INSERT OR IGNORE INTO batches VALUES(?, ?, 'BLOCKED', ?, ?, NULL, NULL)",
                    (
                        batch_id,
                        digest([batch_id, "rejected"]),
                        canonical(blocked_manifest(prepared) if self.compact else prepared),
                        now(),
                    ),
                )
            for issue in issues:
                semantic = {k: v for k, v in issue.items() if k != "evidence"}
                evidence = issue.get("evidence", {})
                refs = evidence if isinstance(evidence, list) else [evidence]
                # Para inválidos, la fila original forma parte de la identidad del caso.
                if self.contract.key not in semantic:
                    semantic["raw"] = [r.get("raw") for r in refs]
                identifier = digest([plant, self.contract.name, semantic])
                self.db.execute(
                    "INSERT OR IGNORE INTO issues(issue_id,reason,payload_json,first_seen) "
                    "VALUES(?,?,?,?)",
                    (identifier, issue["reason"], canonical(issue), now()),
                )
                for ref in refs:
                    self.db.execute(
                        "INSERT OR IGNORE INTO issue_evidence VALUES(?,?,?,?)",
                        (identifier, batch_id, digest(ref), canonical(ref)),
                    )

    def exclusions(self, batch_id: str, entries: dict, plant: str) -> None:
        """Resultados esperados del filtro, separados de incidencias de calidad."""
        with self.db:
            for entry, refs in entries.items():
                identifier = digest([plant, self.contract.name, "NO_USAR", entry])
                self.db.execute(
                    "INSERT OR IGNORE INTO exclusions VALUES(?, 'NO_USAR', ?, ?)",
                    (identifier, canonical(entry), now()),
                )
                for ref in refs:
                    self.db.execute(
                        "INSERT OR IGNORE INTO exclusion_evidence VALUES(?, ?, ?, ?)",
                        (identifier, batch_id, digest(ref), canonical(ref)),
                    )

    def prepared(self, row: dict) -> dict:
        from .retention import payload

        document = payload(row["prepared_json"])
        from .compact_control import WORK_KEY, load
        from .control_lifecycle import archived_receipt

        if WORK_KEY in document:
            return load(self.work_root, document)
        if "$receipt_archive" in document:
            return archived_receipt(self, row["batch_id"], document)
        return document

    def prepare_compact(self, batch_id, operation_id, prepared):
        """Shared pending protocol; subclasses retain their confirmation semantics."""
        if not self.compact:
            return False
        from .compact_control import publish

        reference = publish(self.work_root, prepared)
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,'PREPARED',?,?,NULL,NULL)",
                (batch_id, operation_id, canonical(reference), now()),
            )
            self.db.execute(
                "INSERT INTO prepared_artifacts VALUES(?,?)",
                (batch_id, reference["$control_work"]),
            )
        return True
