"""Shared accepted-control projection, exported only by its owning writer."""

import json

from .common import IntegrityError, digest


def confirmed_control(cfg, db, settings, version, *, expected=None, allow_empty=False):
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
            raise IntegrityError("Construir el catálogo antes de asociar")
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
        latest = db.execute(
            "SELECT payload_json FROM publications WHERE "
            "coalesce(json_extract(payload_json,'$.scope'),'full')='full' "
            "ORDER BY no DESC LIMIT 1"
        ).fetchone()
        extra["association"] = json.loads(latest[0]) if latest else None
    return cases, meta, extra
