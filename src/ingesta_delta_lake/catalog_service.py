"""Construcción explícita y búsqueda de solo lectura por código de modelo."""

import json
import sqlite3
from contextlib import closing

from deltalake import DeltaTable

from . import __version__, catalog
from .catalog_storage import CatalogStore, load_sources, verify_manifest
from .common import ConfigurationError, IntegrityError, canonical, digest
from .service import _apply


def process(config, control, attempt):
    store = CatalogStore(config, control.control_id)
    recovered, inserted_recovery, updated_recovery = [], 0, 0
    for pending in control.pending():
        prepared = control.prepared(pending)
        version, wrote = _apply(config, control, store, pending)
        inserted_recovery += len(prepared["inserts"]) if wrote else 0
        updated_recovery += len(prepared["updates"]) if wrote else 0
        recovered.append(
            {
                "operation_id": pending["operation_id"],
                "delta_version": version,
                "wrote_this_attempt": wrote,
                "confirmed_rows": len(prepared["inserts"]),
                "confirmed_updates": len(prepared["updates"]),
            }
        )
    version, previous = store.read()
    if control.setting("last_version") not in (None, str(version)):
        raise IntegrityError("El catálogo y su control no coinciden")
    models, records, manifest, cases = load_sources(config)
    lines = dict(config.line_by_origin)
    if {r["origen_fenix"] for r in records.values()} - set(lines):
        raise ConfigurationError("Falta una correspondencia explícita de origen a línea")
    if any(r["ID_MODELO"] not in models for r in records.values()):
        raise IntegrityError("Un modelo MP9 aceptado ya no está en el diccionario")
    prior_observations = control.observations()
    projected, observations, issues, pending_roots = catalog.project(
        config.plant, lines, records, cases, previous, prior_observations
    )
    for case in control.db.execute("SELECT payload_json FROM issues WHERE status!='RESOLVED'"):
        identity = json.loads(case[0])["id_produccion"]
        if identity in observations:
            observations[identity]["coverage"] = "REVIEW"
            if "CATALOG_REVIEW" not in observations[identity]["reasons"]:
                observations[identity]["reasons"].append("CATALOG_REVIEW")
            if identity in previous:
                projected[identity] = previous[identity]
                observations[identity]["component_ids"] = prior_observations[identity][
                    "component_ids"
                ]
    blocked = {
        r["production_id"]
        for r in control.db.execute("SELECT DISTINCT production_id FROM composition")
        if r["production_id"] not in projected
    }
    if blocked:
        raise IntegrityError("Se ha perdido la composición histórica del catálogo")
    inserts = [
        {**row, "evidence": observations[identity]}
        for identity, row in projected.items()
        if identity not in previous
    ]
    updates = [
        {**row, "before": previous[identity], "evidence": observations[identity]}
        for identity, row in projected.items()
        if identity in previous and row != previous[identity]
    ]
    coverage_pending = sum(o["coverage"] != "VALID" for o in observations.values())
    open_reviews = control.db.execute(
        "SELECT COUNT(*) FROM issues WHERE status!='RESOLVED'"
    ).fetchone()[0]
    missing_sources = [name for name, snapshot in manifest.items() if snapshot["version"] is None]
    complete = not (
        missing_sources or cases or pending_roots or coverage_pending or issues or open_reviews
    )
    diagnostics = {
        "source_cases": cases,
        "pending_candidates": pending_roots,
        "candidate_productions": len(projected) + len(pending_roots),
        "coverage_pending": coverage_pending,
        "catalog_issues": issues,
        "mapping": lines,
        "rule": catalog.CONTRACT,
        "topology_enabled": config.topology_enabled,
        "line_codes": dict(config.line_code_by_origin),
        "missing_sources": missing_sources,
    }
    batch_id = digest(
        [
            catalog.CONTRACT,
            __version__,
            config.plant,
            config.timestamp_policy,
            version,
            manifest,
            diagnostics,
            catalog.content_hash(projected),
        ]
    )
    prepared = {
        "contract": catalog.CONTRACT,
        "transformer_version": __version__,
        "snapshots": [],
        "source_manifest": manifest,
        "before_version": version,
        "before_hash": catalog.content_hash(previous),
        "after_hash": catalog.content_hash(projected),
        "inserts": inserts,
        "updates": updates,
        "diagnostics": diagnostics,
        "observations": observations,
        "complete": complete,
    }
    control.rejected(batch_id, prepared, issues, config.plant, create_batch=False)
    prior = control.batch(batch_id)
    wrote = False
    if prior is None:
        control.prepare(batch_id, digest([batch_id, "derive_catalog_v1"]), prepared)
        version, wrote = _apply(config, control, store, control.batch(batch_id))
    elif inserts or updates:
        raise IntegrityError("Una derivación confirmada exige escrituras inesperadas")
    status = (
        ("PARTIAL" if projected else "WAITING_DEPENDENCIES")
        if not complete
        else ("APPLIED" if wrote else "NO_CHANGE" if projected else "NO_INPUTS")
    )
    if not records and missing_sources and not cases:
        status = "NO_INPUTS"
    return {
        "attempt_id": attempt,
        "batch_id": batch_id,
        "contract": catalog.CONTRACT,
        "status": status,
        "complete": complete,
        "plant": config.plant,
        "delta_version": version,
        "table_path": str(config.table_path),
        "control_path": str(config.control_path),
        "source_manifest": manifest,
        "candidate_productions": diagnostics["candidate_productions"],
        "published_productions": len(projected),
        "pending_candidates": pending_roots,
        "coverage_pending": coverage_pending,
        "source_issues": len(cases),
        "closed_productions": sum(r["fecha_fin_zf"] is not None for r in projected.values()),
        "without_recorded_end": sum(r["fecha_fin_zf"] is None for r in projected.values()),
        "inserted_this_attempt": inserted_recovery + (len(inserts) if wrote else 0),
        "updated_this_attempt": updated_recovery + (len(updates) if wrote else 0),
        "recovered_operations": recovered,
    }


def search(config, model_code: str):
    config.validate()
    if config.family != "CATALOGO_PRODUCCIONES":
        raise ConfigurationError("La consulta requiere family=CATALOGO_PRODUCCIONES")
    if not config.control_path.exists():
        return {"status": "NO_CATALOG", "complete": False, "fresh": False, "productions": []}
    with closing(sqlite3.connect(f"{config.control_path.as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        expected = canonical(
            {
                "plant": config.plant,
                "silver_root": str(config.silver_root),
                "bronze_root": str(config.bronze_root),
                "contract": catalog.CONTRACT,
            }
        )
        if settings.get("binding") != expected:
            raise IntegrityError("El catálogo pertenece a otra planta o rutas")
        publication = db.execute(
            "SELECT * FROM publications ORDER BY publication_no DESC LIMIT 1"
        ).fetchone()
        if publication is None:
            return {"status": "NO_CATALOG", "complete": False, "fresh": False, "productions": []}
        observations = {
            r["production_id"]: json.loads(r["observation_json"])
            for r in db.execute(
                "SELECT * FROM composition WHERE batch_id=?", (publication["batch_id"],)
            )
        }
    manifest = json.loads(publication["source_manifest_json"])
    pinned = verify_manifest(config, manifest)
    store = CatalogStore(config, settings["control_id"])
    latest = store.table()
    version = publication["delta_version"]
    if version is None:
        return {"status": "NO_CATALOG", "complete": False, "fresh": False, "productions": []}
    rows = (
        {}
        if version is None
        else store.read(DeltaTable(str(config.table_path), version=version))[1]
    )
    try:
        _, _, current, _ = load_sources(config)
        diagnostics = json.loads(publication["diagnostics_json"])
        fresh = (
            current == manifest
            and (latest.version() if latest else None) == version
            and diagnostics["mapping"] == dict(config.line_by_origin)
            and diagnostics["rule"] == catalog.CONTRACT
            and diagnostics.get("topology_enabled", False) == config.topology_enabled
            and diagnostics.get("line_codes", {}) == dict(config.line_code_by_origin)
        )
    except IntegrityError:
        fresh = False
    models = pinned["SIP_MODELO"]
    ids = {identifier for identifier, code in models.items() if code == model_code}
    result = [
        {
            **row,
            "codigo_modelo_botella": models[row["id_modelo_fenix"]],
            "coverage": observations[identity]["coverage"],
            "component_ids": observations[identity]["component_ids"],
            "review_reasons": observations[identity]["reasons"],
            "production_state": "CERRADA"
            if row["fecha_fin_zf"] is not None
            else "ABIERTA_EN_ZC"
            if observations[identity]["root"]["FECHA_OFF"] is None
            else "PENDIENTE_CIERRE_ZF",
            "nombre_maquina": next(
                (
                    m["nombre_maquina"]
                    for m in pinned.get("SIP_MAQUINA_MOLDEO", {}).values()
                    if m["id_maquina_fenix"] == row["ID_MAQUINA"]
                    and m["vigente_desde"] <= row["fecha_inicio_zc"]
                    and (m["vigente_hasta"] is None or row["fecha_inicio_zc"] < m["vigente_hasta"])
                ),
                None,
            ),
        }
        for identity, row in rows.items()
        if row["id_modelo_fenix"] in ids
    ]
    result.sort(key=lambda r: (r["fecha_inicio_zc"], r["id_produccion"]), reverse=True)
    return {
        "status": "STALE" if not fresh else "OK" if publication["complete"] else "PARTIAL",
        "complete": bool(publication["complete"]) and fresh,
        "fresh": fresh,
        "model_code": model_code,
        "delta_version": version,
        "source_manifest": manifest,
        "diagnostics": json.loads(publication["diagnostics_json"]),
        "productions": result,
    }
