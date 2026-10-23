"""Snapshots aceptados, evidencia inmutable y publicación Delta de episodios revisados."""

import gzip
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pyarrow as pa
from deltalake import write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__, topology
from . import associations as a
from .ar_storage import ArStore
from .catalog_storage import CatalogStore
from .commit_protocol import application_transaction
from .common import IntegrityError, canonical, digest, now
from .control import Control
from .defects_storage import DefectsStore
from .delta import DeltaStore
from .mp9_storage import Mp9Store
from .rejections_storage import RejectionsStore
from .topology_storage import TopologyStore
from .zc_storage import ZcStore

STORES = {
    topology.MACHINES: TopologyStore,
    topology.BRANCHES: TopologyStore,
    "SIP_MODELO": DeltaStore,
    "SIP_DEFECTO": DeltaStore,
    "SIP_DEFECTO_STATUS": DeltaStore,
    "SIP_DEFECTO_AR": DeltaStore,
    "MP9_CAMBIO_MODELO": Mp9Store,
    "CATALOGO_PRODUCCIONES": CatalogStore,
    "LISTA_AR": ArStore,
    "LISTA_DEFECTOS": DefectsStore,
    "LISTA_RECH": RejectionsStore,
    "CONT_ZC": ZcStore,
}


def source_snapshot(config, family, read=True):
    cfg = replace(config, family=family)
    if not cfg.control_path.exists():
        raise IntegrityError(f"Falta fuente aceptada {family}")
    with closing(sqlite3.connect(cfg.control_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        binding = canonical(
            {
                "plant": cfg.plant,
                "silver_root": str(cfg.silver_root),
                "bronze_root": str(cfg.bronze_root),
                "contract": cfg.contract.name,
            }
        )
        if settings.get("binding") != binding:
            raise IntegrityError("Fuente de otro ámbito")
        if db.execute(
            "SELECT COUNT(*) FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
        ).fetchone()[0]:
            raise IntegrityError(f"Recuperar primero {family}")
        store = STORES[family](cfg, settings["control_id"])
        table = store.table()
        version = table.version() if table is not None else None
        if settings.get("last_version", "None") != str(version):
            raise IntegrityError("Fuente sin confirmación")
        cases = [
            {"issue_id": r[0], "reason": r[1], "status": r[2], "payload": json.loads(r[3])}
            for r in db.execute(
                "SELECT issue_id,reason,status,payload_json FROM issues "
                "WHERE status!='RESOLVED' ORDER BY issue_id"
            )
        ]
        rows = []
        if read:
            _, data = store.read(table)
            if STORES[family] is DeltaStore:
                rows = [
                    dict(
                        zip(
                            cfg.contract.schema.names,
                            (k, *(v if isinstance(v, tuple) else (v,))),
                            strict=True,
                        )
                    )
                    for k, v in data.items()
                ]
            else:
                rows = list(data.values())
        meta = {
            "family": family,
            "contract": cfg.contract.name,
            "version": version,
            "control_id": settings["control_id"],
            "quality_hash": digest(cases),
        }
        extra = {}
        if family == "CATALOGO_PRODUCCIONES":
            publication = db.execute(
                "SELECT batch_id,source_manifest_json,diagnostics_json FROM publications "
                "ORDER BY publication_no DESC LIMIT 1"
            ).fetchone()
            if publication is None:
                raise IntegrityError("Construir el catálogo antes de asociar")
            extra = {
                "manifest": json.loads(publication[1]),
                "diagnostics": json.loads(publication[2]),
                "composition": {
                    r[0]: json.loads(r[1])
                    for r in db.execute(
                        "SELECT production_id,observation_json FROM composition WHERE batch_id=?",
                        (publication[0],),
                    )
                },
            }
    return rows, cases, meta, extra


def load_bundle(config):
    sources, cases, manifest = {}, {}, {}
    extra = {}
    for family in STORES:
        if family in topology.FAMILIES and not config.topology_enabled:
            continue
        rows, issues, meta, more = source_snapshot(config, family)
        sources[family] = sorted(rows, key=lambda r: canonical(a.SOURCE_KEYS[family](r)))
        cases[family] = issues
        manifest[family] = meta
        if more:
            extra = more
    for f, descriptor in extra["manifest"].items():
        current = manifest[f]
        if (
            descriptor["version"] != current["version"]
            or descriptor.get("control_id") != current["control_id"]
            or (
                "quality_hash" in descriptor
                and descriptor["quality_hash"] != current["quality_hash"]
            )
        ):
            raise IntegrityError("Actualizar el catálogo: sus fuentes han cambiado")
    if extra["diagnostics"]["mapping"] != dict(config.line_by_origin):
        raise IntegrityError("Correspondencia de líneas distinta del catálogo")
    if extra["diagnostics"].get("topology_enabled", False) != config.topology_enabled or extra[
        "diagnostics"
    ].get("line_codes", {}) != dict(config.line_code_by_origin):
        raise IntegrityError("Actualizar catálogo: cambió la política de topología")
    if cases["SIP_MODELO"]:
        for obs in extra["composition"].values():
            obs["coverage"] = "REVIEW"
    return {
        "contract": a.CONTRACT,
        "rule": a.RULE,
        "plant": config.plant,
        "topology_enabled": config.topology_enabled,
        "line_codes": dict(config.line_code_by_origin),
        "manifest": manifest,
        "sources": sources,
        "cases": cases,
        "catalog_composition": extra["composition"],
        "catalog_diagnostics": extra["diagnostics"],
    }


def artifact(config, payload):
    root = config.control_root / "association-artifacts"
    root.mkdir(parents=True, exist_ok=True)
    temp = root / ("." + uuid.uuid4().hex + ".tmp")
    payload_hash = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        with temp.open("xb") as f:
            with gzip.GzipFile(filename="", fileobj=f, mode="wb", compresslevel=6, mtime=0) as out:
                parts, size = [], 0
                for text in encoder.iterencode(payload):
                    parts.append(text)
                    size += len(text)
                    if size >= 65536:
                        data = "".join(parts).encode("utf-8")
                        payload_hash.update(data)
                        out.write(data)
                        parts, size = [], 0
                if parts:
                    data = "".join(parts).encode("utf-8")
                    payload_hash.update(data)
                    out.write(data)
            f.flush()
            os.fsync(f.fileno())
        with temp.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        path = root / (sha + ".json.gz")
        try:
            os.link(temp, path)
        except FileExistsError:
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != sha:
                    raise IntegrityError("Artefacto inmutable alterado") from None
        if os.name != "nt":
            for directory in (root, config.control_root):
                handle = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(handle)
                finally:
                    os.close(handle)
        return {"path": str(path), "sha256": sha, "payload_hash": payload_hash.hexdigest()}
    finally:
        temp.unlink(missing_ok=True)


def verify_artifact(config, descriptor):
    path = Path(descriptor["path"]).resolve()
    if not path.is_relative_to(config.control_root):
        raise IntegrityError("Artefacto fuera de control")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != descriptor["sha256"]:
            raise IntegrityError("Artefacto de asociación alterado")
    with gzip.open(path, "rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != descriptor["payload_hash"]:
            raise IntegrityError("Payload de asociación alterado")
    return path


def read_artifact(config, descriptor):
    path = verify_artifact(config, descriptor)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def verify_artifacts(config, prepared):
    for descriptor in prepared["artifacts"].values():
        verify_artifact(config, descriptor)


class AssociationControl(Control):
    def __init__(self, config):
        super().__init__(config)
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS association_intents(
            operation_id TEXT NOT NULL, association_id TEXT NOT NULL, before_json TEXT,
            after_json TEXT NOT NULL, before_hash TEXT, after_hash TEXT NOT NULL,
            state TEXT NOT NULL, delta_version INTEGER, PRIMARY KEY(operation_id,association_id));
          CREATE TABLE IF NOT EXISTS publications(
            no INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT UNIQUE NOT NULL,
            delta_version INTEGER, payload_json TEXT NOT NULL, confirmed_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS review_events(
            issue_id TEXT NOT NULL,batch_id TEXT NOT NULL,event TEXT NOT NULL,at TEXT NOT NULL,
            PRIMARY KEY(issue_id,batch_id,event));
        """)

    def latest(self):
        row = self.db.execute("SELECT * FROM publications ORDER BY no DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def prepare(self, batch_id, operation_id, prepared):
        if self.prepare_compact(batch_id, operation_id, prepared):
            return
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,'PREPARED',?,?,NULL,NULL)",
                (batch_id, operation_id, canonical(prepared), now()),
            )
            self.db.executemany(
                "INSERT INTO association_intents VALUES(?,?,?,?,?,?,'PREPARED',NULL)",
                [
                    (
                        operation_id,
                        r["id_asignacion"],
                        canonical(r.get("before")) if "before" in r else None,
                        canonical({f: r[f] for f in a.FIELDS}),
                        a.row_hash(r["before"]) if "before" in r else None,
                        a.row_hash(r),
                    )
                    for r in [*prepared["inserts"], *prepared["updates"]]
                ],
            )

    def confirm(self, batch_id, version):
        with self.db:
            b = self.batch(batch_id)
            p = self.prepared(b)
            self.db.execute(
                "UPDATE batches SET state='COMPLETED',confirmed_at=?,delta_version=? "
                "WHERE batch_id=?",
                (now(), version, batch_id),
            )
            self.db.execute(
                "UPDATE association_intents SET state='CONFIRMED',delta_version=? "
                "WHERE operation_id=?",
                (version, b["operation_id"]),
            )
            self.db.execute(
                "INSERT INTO settings VALUES('last_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO publications"
                "(batch_id,delta_version,payload_json,confirmed_at) "
                "VALUES(?,?,?,?)",
                (batch_id, version, canonical(p["publication"]), now()),
            )
            for issue_id in p.get("resolved_issue_ids", []):
                self.db.execute("UPDATE issues SET status='RESOLVED' WHERE issue_id=?", (issue_id,))
                self.db.execute(
                    "INSERT OR IGNORE INTO review_events VALUES(?,?,'RESOLVED_BY_DECISION',?)",
                    (issue_id, batch_id, now()),
                )


class AssociationStore(DeltaStore):
    def read(self, table=None):
        table = table if table is not None else self.table()
        if table is None:
            return None, {}
        arrow = table.to_pyarrow_table()
        if arrow.schema != a.SCHEMA:
            raise IntegrityError("Esquema de asociación incompatible")
        rows = {}
        for row in arrow.to_pylist():
            for f in ("vigente_desde", "vigente_hasta"):
                if row[f] is not None:
                    if row[f].tzinfo is not None or row[f].microsecond:
                        raise IntegrityError("Fecha de asociación fuera de la política de ensayo")
                    row[f] = row[f].isoformat(timespec="seconds")
            identity = row["id_asignacion"]
            if (
                identity in rows
                or any(row[f] is None for f in a.FIELDS if f != "vigente_hasta")
                or row["planta"] != self.plant
                or row["zona"] != "ZF"
                or (
                    row["vigente_hasta"] is not None
                    and row["vigente_desde"] >= row["vigente_hasta"]
                )
            ):
                raise IntegrityError("Asociación publicada inválida")
            rows[identity] = row
        self.validate(rows.values())
        return table.version(), rows

    @staticmethod
    def validate(rows):
        groups = {}
        for r in rows:
            groups.setdefault(
                (r["planta"], r["origen_fenix"], r["linea"], r["ramal"], r["zona"]), []
            ).append(r)
        for items in groups.values():
            items.sort(key=lambda r: r["vigente_desde"])
            for x, y in zip(items, items[1:], strict=False):
                if a.overlaps(
                    x["vigente_desde"], x["vigente_hasta"], y["vigente_desde"], y["vigente_hasta"]
                ):
                    raise IntegrityError("Solapamiento ZF antes de publicar")

    def apply_changes(self, inserts, updates, operation, before_version):
        table = self.table()
        version, old = self.read(table)
        if version != before_version or not (inserts or updates):
            raise IntegrityError("Snapshot de asociación cambiado o vacío")
        seen = set()
        for r in inserts:
            if r["id_asignacion"] in old or r["id_asignacion"] in seen:
                raise IntegrityError("Asociación duplicada")
            seen.add(r["id_asignacion"])
        for r in updates:
            before = old.get(r["id_asignacion"])
            if (
                before is None
                or before != r["before"]
                or r["id_asignacion"] in seen
                or r["revision_decision"] <= before["revision_decision"]
            ):
                raise IntegrityError("Corrección de asociación no autorizada")
            seen.add(r["id_asignacion"])
        self.validate({**old, **{r["id_asignacion"]: r for r in [*inserts, *updates]}}.values())
        projected = []
        for r in [*inserts, *updates]:
            item = {f: r[f] for f in a.FIELDS}
            for f in ("vigente_desde", "vigente_hasta"):
                item[f] = datetime.fromisoformat(item[f]) if item[f] is not None else None
            projected.append(item)
        arrow = pa.Table.from_pylist(projected, schema=a.SCHEMA)
        props = CommitProperties(
            custom_metadata={
                "ingesta_operation_id": operation,
                "ingesta_control_id": self.control_id,
                "ingesta_plant": self.plant,
                "ingesta_contract": a.CONTRACT,
                "ingesta_release": __version__,
            },
            app_transactions=application_transaction(self.control_id, before_version),
            max_commit_retries=0,
        )
        if table is None:
            write_deltalake(str(self.path), arrow, mode="error", commit_properties=props)
        else:
            m = (
                table.merge(
                    arrow,
                    "t.id_asignacion=s.id_asignacion",
                    source_alias="s",
                    target_alias="t",
                    commit_properties=props,
                )
                .when_matched_update({f: "s." + f for f in a.FIELDS if f != "id_asignacion"})
                .when_not_matched_insert_all()
                .execute()
            )
            if (
                m["num_target_rows_inserted"] != len(inserts)
                or m["num_target_rows_updated"] != len(updates)
                or m["num_target_rows_deleted"]
            ):
                raise IntegrityError("MERGE distinto de las decisiones")
