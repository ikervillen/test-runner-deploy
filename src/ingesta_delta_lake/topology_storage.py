"""Snapshots y MERGE de catálogos físicos; control separado por familia."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime

import pyarrow as pa
from deltalake import write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__
from . import topology as t
from .commit_protocol import application_transaction, delta_configuration
from .common import IntegrityError, canonical, digest, now
from .control import Control
from .delta import DeltaStore


class TopologyControl(Control):
    def __init__(self, config):
        super().__init__(config)
        self.family = config.family
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS topology_intents(
            operation_id TEXT NOT NULL, record_key TEXT NOT NULL, before_json TEXT,
            after_json TEXT NOT NULL, before_hash TEXT, after_hash TEXT NOT NULL,
            state TEXT NOT NULL, delta_version INTEGER, PRIMARY KEY(operation_id,record_key));
          CREATE TABLE IF NOT EXISTS topology_review_events(
            issue_id TEXT NOT NULL,batch_id TEXT NOT NULL,event TEXT NOT NULL,at TEXT NOT NULL,
            PRIMARY KEY(issue_id,batch_id,event));
        """)

    def cases(self):
        return [
            {"issue_id": r[0], "reason": r[1], "status": r[2], "payload": json.loads(r[3])}
            for r in self.db.execute(
                "SELECT issue_id,reason,status,payload_json FROM issues "
                "WHERE status!='RESOLVED' ORDER BY issue_id"
            )
        ]

    def prepare(self, batch_id, operation_id, prepared):
        if self.prepare_compact(batch_id, operation_id, prepared):
            return
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,'PREPARED',?,?,NULL,NULL)",
                (batch_id, operation_id, canonical(prepared), now()),
            )
            for row in [*prepared["inserts"], *prepared["updates"]]:
                before = row.get("before")
                self.db.execute(
                    "INSERT INTO topology_intents VALUES(?,?,?,?,?,?,'PREPARED',NULL)",
                    (
                        operation_id,
                        canonical(t.key(self.family, row)),
                        canonical(before) if before else None,
                        canonical({f: row[f] for f in t.FIELDS[self.family]}),
                        t.row_hash(self.family, before) if before else None,
                        t.row_hash(self.family, row),
                    ),
                )

    def confirm(self, batch_id, version):
        with self.db:
            self.db.execute(
                "UPDATE batches SET state='COMPLETED',confirmed_at=?,delta_version=? "
                "WHERE batch_id=?",
                (now(), version, batch_id),
            )
            self.db.execute(
                "UPDATE topology_intents SET state='CONFIRMED',delta_version=? "
                "WHERE operation_id=(SELECT operation_id FROM batches WHERE batch_id=?)",
                (version, batch_id),
            )
            self.db.execute(
                "INSERT INTO settings VALUES('last_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )

    def resolve_dependencies(self, rows, batch_id):
        with self.db:
            for case in self.cases():
                if case["reason"] != "MACHINE_PENDING":
                    continue
                p = case["payload"]
                accepted = rows.get(tuple(p["key"]))
                if accepted is None or any(
                    any(c[f] != accepted[f] for f in t.FIELDS[self.family] if f != "vigente_hasta")
                    for c in p["candidates"]
                ):
                    continue
                if any(
                    c["vigente_hasta"] not in (None, accepted["vigente_hasta"])
                    for c in p["candidates"]
                ):
                    continue
                self.db.execute(
                    "UPDATE issues SET status='RESOLVED' WHERE issue_id=?", (case["issue_id"],)
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO topology_review_events VALUES(?,?,'RESOLVED',?)",
                    (case["issue_id"], batch_id, now()),
                )


class TopologyStore(DeltaStore):
    def __init__(self, config, control_id):
        super().__init__(config, control_id)
        self.family = config.family

    def read(self, table=None):
        table = table if table is not None else self.table()
        if table is None:
            return None, {}
        arrow = table.to_pyarrow_table()
        if arrow.schema != t.SCHEMAS[self.family]:
            raise IntegrityError("Esquema de topología inesperado")
        rows = {}
        for row in arrow.to_pylist():
            for f in ("vigente_desde", "vigente_hasta"):
                if row[f] is not None:
                    if row[f].tzinfo is not None or row[f].microsecond:
                        raise IntegrityError("Fecha de topología incompatible")
                    row[f] = row[f].isoformat(timespec="seconds")
            k = t.key(self.family, row)
            if k in rows or any(
                v is None or (isinstance(v, str) and not v.strip())
                for f, v in row.items()
                if f != "vigente_hasta"
            ):
                raise IntegrityError("Topología duplicada o nula")
            if row["vigente_hasta"] is not None and row["vigente_hasta"] <= row["vigente_desde"]:
                raise IntegrityError("Vigencia de topología incompatible")
            rows[k] = row
        if t.conflict_keys(self.family, rows):
            raise IntegrityError("Topología publicada solapada")
        return table.version(), rows

    def apply_changes(self, inserts, updates, operation, before_version):
        table = self.table()
        version, old = self.read(table)
        if version != before_version or not (inserts or updates):
            raise IntegrityError("Topología cambiada o escritura vacía")
        seen = set()
        for r in inserts:
            k = t.key(self.family, r)
            if k in old or k in seen:
                raise IntegrityError("Topología ya existente")
            seen.add(k)
        for r in updates:
            k = t.key(self.family, r)
            before = old.get(k)
            if (
                k in seen
                or before is None
                or before != r["before"]
                or before["vigente_hasta"] is not None
                or r["vigente_hasta"] is None
                or any(r[f] != before[f] for f in t.FIELDS[self.family] if f != "vigente_hasta")
            ):
                raise IntegrityError("Solo se permiten cierres de topología validados")
            seen.add(k)
        merged = {**old, **{t.key(self.family, r): r for r in [*inserts, *updates]}}
        if t.conflict_keys(self.family, merged):
            raise IntegrityError("Solapamiento antes de escribir topología")
        data = []
        for r in [*inserts, *updates]:
            v = {f: r[f] for f in t.FIELDS[self.family]}
            if v["vigente_hasta"] is not None and v["vigente_hasta"] <= v["vigente_desde"]:
                raise IntegrityError("Cierre de topología inválido")
            for f in ("vigente_desde", "vigente_hasta"):
                v[f] = datetime.fromisoformat(v[f]) if v[f] is not None else None
            data.append(v)
        arrow = pa.Table.from_pylist(data, schema=t.SCHEMAS[self.family])
        props = CommitProperties(
            custom_metadata={
                "ingesta_operation_id": operation,
                "ingesta_control_id": self.control_id,
                "ingesta_plant": self.plant,
                "ingesta_contract": self.contract.name,
                "ingesta_release": __version__,
            },
            app_transactions=application_transaction(self.control_id, before_version),
            max_commit_retries=0,
        )
        if table is None:
            write_deltalake(
                str(self.path),
                arrow,
                mode="error",
                configuration=delta_configuration(self.config),
                commit_properties=props,
            )
        else:
            keys = (
                ["id_maquina_fenix", "vigente_desde"]
                if self.family == t.MACHINES
                else ["origen_fenix", "id_linea_zf_fenix", "vigente_desde"]
            )
            metrics = (
                table.merge(
                    arrow,
                    " AND ".join("t." + f + "=s." + f for f in keys),
                    source_alias="s",
                    target_alias="t",
                    commit_properties=props,
                )
                .when_matched_update(
                    {"vigente_hasta": "s.vigente_hasta"},
                    predicate="t.vigente_hasta IS NULL AND s.vigente_hasta IS NOT NULL",
                )
                .when_not_matched_insert_all()
                .execute()
            )
            if (
                metrics["num_target_rows_inserted"] != len(inserts)
                or metrics["num_target_rows_updated"] != len(updates)
                or metrics["num_target_rows_deleted"]
            ):
                raise IntegrityError("MERGE de topología distinto de lo preparado")


def snapshot(config, family):
    cfg = replace(config, family=family)
    if not cfg.control_path.exists():
        if cfg.table_path.exists():
            raise IntegrityError("Falta control de topología existente")
        return (
            {},
            {"contract": cfg.contract.name, "version": None, "hash": t.content_hash(family, {})},
            [],
        )
    with closing(sqlite3.connect(cfg.control_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        if settings.get("binding") != canonical(
            {
                "plant": cfg.plant,
                "silver_root": str(cfg.silver_root),
                "bronze_root": str(cfg.bronze_root),
                "contract": cfg.contract.name,
            }
        ):
            raise IntegrityError("Topología de otro ámbito/contrato")
        if db.execute(
            "SELECT COUNT(*) FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
        ).fetchone()[0]:
            raise IntegrityError("Recuperar topología antes de consumir")
        version, rows = TopologyStore(cfg, settings["control_id"]).read()
        if settings.get("last_version", "None") != str(version):
            raise IntegrityError("Topología sin confirmación")
        cases = [
            {"issue_id": r[0], "reason": r[1], "status": r[2], "payload": json.loads(r[3])}
            for r in db.execute(
                "SELECT issue_id,reason,status,payload_json FROM issues "
                "WHERE status!='RESOLVED' ORDER BY issue_id"
            )
        ]
    return (
        rows,
        {
            "contract": cfg.contract.name,
            "version": version,
            "hash": t.content_hash(family, rows),
            "control_id": settings["control_id"],
            "table_path": str(cfg.table_path),
            "quality_hash": digest(cases),
        },
        cases,
    )
