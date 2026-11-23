"""Shared accepted-control projection, exported only by its owning writer."""

import json
import sqlite3
from contextlib import closing

from .common import DependencyPending, IntegrityError, digest


def check_source_version(cfg, settings, version, message, *, dependency=True):
    pinned = settings.get("last_version", "None")
    if pinned == str(version):
        return
    # A commit can appear in Delta after the SQLite reader acquired its snapshot.
    # Only classify it as transient if the same owner can explain the advance.
    if dependency and version is not None and (pinned == "None" or version > int(pinned)):
        with closing(sqlite3.connect(cfg.control_path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            current = dict(
                db.execute(
                    "SELECT key,value FROM settings WHERE key IN ('last_version','control_id')"
                )
            )
            if current.get("control_id") == settings.get("control_id") and (
                current.get("last_version") == str(version)
                or db.execute(
                    "SELECT 1 FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN') LIMIT 1"
                ).fetchone()
            ):
                raise DependencyPending(f"{cfg.family} avanzó durante la lectura de su control")
    raise IntegrityError(message)


def confirmed_control(
    cfg, db, settings, version, *, expected=None, allow_empty=False, live_registry=False
):
    family = cfg.family
    cases = [
        {"issue_id": r[0], "reason": r[1], "status": r[2], "payload": json.loads(r[3])}
        for r in db.execute(
            "SELECT issue_id,reason,status,payload_json FROM issues "
            "WHERE status!='RESOLVED' ORDER BY issue_id"
        )
    ]

    def _mark(name):
        try:
            value = json.loads(settings[name]) if settings.get(name) else {}
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    meta = {
        "family": family,
        "contract": cfg.contract.name,
        "version": version,
        "control_id": settings["control_id"],
        "quality_hash": digest(cases),
        "observed_until": _mark("observed_until"),
        "live_until": _mark("live_until"),
    }
    extra = {}
    if family == "CATALOGO_PRODUCCIONES" and (version is not None or not allow_empty):
        query = "SELECT batch_id,source_manifest_json,diagnostics_json FROM publications "
        pinned = expected.get("publication_id") if expected else None
        publication = db.execute(
            query + ("WHERE batch_id=?" if pinned else "ORDER BY publication_no DESC LIMIT 1"),
            (pinned,) if pinned else (),
        ).fetchone()
        if publication is None:
            if pinned or version is not None:
                raise IntegrityError("Falta la publicación confirmada del catálogo")
            raise DependencyPending("Construir el catálogo antes de asociar")
        meta["publication_id"] = publication[0]
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
    if family == "ASIGNACION_PRODUCCION_RAMAL":
        where = (
            ""
            if live_registry
            else "WHERE coalesce(json_extract(payload_json,'$.scope'),'full')='full' "
        )
        latest = db.execute(
            "SELECT payload_json,delta_version FROM publications "
            + where
            + "ORDER BY no DESC LIMIT 1"
        ).fetchone()
        if live_registry and latest and latest[1] != version:
            raise IntegrityError(
                "Live attribution registry differs from the confirmed Silver version"
            )
        extra["association"] = json.loads(latest[0]) if latest else None
    return cases, meta, extra
