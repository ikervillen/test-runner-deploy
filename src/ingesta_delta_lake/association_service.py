"""Publicar episodios revisados y un ledger reproducible, sin conectar todavía la app."""

import json
import sqlite3
from contextlib import closing

from deltalake import DeltaTable

from . import __version__, cont_zc, physical_assignments
from . import associations as a
from .association_storage import (
    AssociationStore,
    artifact,
    load_bundle,
    read_artifact,
    source_snapshot,
)
from .common import ConfigurationError, IntegrityError, canonical, digest
from .service import _apply


def decisions(config):
    if config.association_decisions is None:
        return {"version": 1, "decisions": []}
    try:
        doc = json.loads(config.association_decisions.read_text(encoding="utf-8-sig"))
        if (
            set(doc) != {"version", "decisions"}
            or type(doc["version"]) is not int
            or doc["version"] != 1
            or not isinstance(doc["decisions"], list)
            or not all(isinstance(d, dict) for d in doc["decisions"])
        ):
            raise ValueError("Formato de decisiones incompatible")
        return doc
    except (ValueError, OSError) as exc:
        raise ConfigurationError(str(exc)) from exc


def _can_resolve(payload, registry, current_ids):
    if payload["reason"] == "DECISION_REVIEW":
        variants = [payload["candidate"]]
        old = {a.decision_identity(variants[0]): payload.get("accepted")}
    else:
        variants = payload["candidates"]
        old = payload["accepted"]
    changed = [c for c in variants if old.get(a.decision_identity(c)) != c]
    if not changed:
        return False
    for c in changed:
        identity = a.decision_identity(c)
        d = registry.get(identity)
        prior = old.get(identity)
        if (
            identity not in current_ids
            or d is None
            or digest(d) == digest(c)
            or (prior and d["revision"] <= prior["revision"])
        ):
            return False
    return True


def process(config, control, attempt):
    store = AssociationStore(config, control.control_id)
    recovered = []
    for pending in control.pending():
        version, wrote = _apply(config, control, store, pending)
        recovered.append(
            {
                "operation_id": pending["operation_id"],
                "delta_version": version,
                "wrote_this_attempt": wrote,
            }
        )
    version, previous = store.read()
    if control.setting("last_version") not in (None, str(version)):
        raise IntegrityError("Asociación y control no coinciden")
    latest = control.latest()
    prior_pub = json.loads(latest["payload_json"]) if latest else None
    prior_bundle = read_artifact(config, prior_pub["artifacts"]["bundle"]) if prior_pub else None
    old_registry = prior_bundle["registry"] if prior_bundle else {}
    bundle = load_bundle(config)
    doc = decisions(config)
    lines = dict(config.line_by_origin)
    effective = list(doc["decisions"])
    if config.topology_enabled:
        automatic = physical_assignments.decisions(
            bundle, old_registry, dict(config.line_code_by_origin)
        )
        manual_ids = {a.decision_identity(d) for d in effective}
        effective = [d for d in automatic if d["id"] not in manual_ids] + effective
    registry, issues = a.propose_registry(effective, old_registry, bundle, config.plant, lines)
    projected = a.project(registry, config.plant, lines)
    if set(previous) - set(projected):
        raise IntegrityError("Una ausencia no puede borrar asociaciones")
    index = a.prepare_context(bundle)
    proposals = a.candidates(bundle, index)
    current_ids = {a.decision_identity(d) for d in effective}
    prior_cases = [
        json.loads(r[0])
        for r in control.db.execute("SELECT payload_json FROM issues WHERE status!='RESOLVED'")
    ]
    review_scopes = {
        a.review_scope(p)
        for p in [*prior_cases, *issues]
        if not _can_resolve(p, registry, current_ids)
    } - {None}
    ledger = a.assign(bundle, registry, index, review_scopes)
    counts = a.diagnostics(ledger)
    bundle.update(
        registry=registry,
        decision_input=doc,
        effective_decisions=effective,
        lines=lines,
        proposals=proposals,
    )
    artifacts = {"bundle": artifact(config, bundle), "ledger": artifact(config, ledger)}
    batch_id = digest([a.CONTRACT, __version__, version, bundle["manifest"], artifacts])
    control.rejected(
        batch_id,
        {},
        [{**i, "evidence": {"decision_input_hash": digest(doc)}} for i in issues],
        config.plant,
        create_batch=False,
    )
    cases = [
        (r[0], json.loads(r[1]))
        for r in control.db.execute(
            "SELECT issue_id,payload_json FROM issues WHERE status!='RESOLVED'"
        )
    ]
    resolved = [i for i, p in cases if _can_resolve(p, registry, current_ids)]
    remaining = [i for i, _ in cases if i not in resolved]
    pending = sum(
        r["status"]
        not in {
            "ASSIGNED",
            "OLD_PLUS_PREPARATION",
            "NEW_PLUS_PREPARATION",
            "SHARED_MACHINE",
            "PREPARATION",
            "TRANSITION_TO_OUTGOING",
        }
        for r in ledger
    )
    complete = not (
        remaining
        or pending
        or any(bundle["cases"].values())
        or bundle["catalog_diagnostics"]["pending_candidates"]
    )
    inserts = [r for identity, r in projected.items() if identity not in previous]
    updates = [
        {**r, "before": previous[identity]}
        for identity, r in projected.items()
        if identity in previous and r != previous[identity]
    ]
    changes = [
        {
            "id_asignacion": r["id_asignacion"],
            "before": r.get("before"),
            "after": {f: r[f] for f in a.FIELDS},
        }
        for r in [*inserts, *updates]
    ]
    publication = {
        "artifacts": artifacts,
        "manifest": bundle["manifest"],
        "decision_hash": digest(doc),
        "lines": lines,
        "complete": complete,
        "counts": counts,
        "pending_rows": pending,
        "open_reviews": len(remaining),
        "changes": changes,
        "recomputed_source_rows": len(ledger),
        "rule": a.RULE,
    }
    prepared = {
        "contract": a.CONTRACT,
        "transformer_version": __version__,
        "snapshots": [],
        "artifacts": artifacts,
        "before_version": version,
        "before_hash": a.content_hash(previous),
        "after_hash": a.content_hash(projected),
        "inserts": inserts,
        "updates": updates,
        "resolved_issue_ids": resolved,
        "publication": publication,
    }
    prior = control.batch(batch_id)
    wrote = False
    if prior is None:
        control.prepare(batch_id, digest([batch_id, "association_publication_v1"]), prepared)
        version, wrote = _apply(config, control, store, control.batch(batch_id))
    elif inserts or updates:
        raise IntegrityError("Publicación confirmada exige nuevas escrituras")
    return {
        "status": ("APPLIED" if wrote else "NO_CHANGE") if complete else "PARTIAL",
        "complete": complete,
        "batch_id": batch_id,
        "attempt_id": attempt,
        "delta_version": version,
        "accepted_associations": len(projected),
        "approved_operational_decisions": len(registry),
        "candidate_associations": len(proposals),
        "pending_rows": pending,
        "counts": counts,
        "inserted_this_attempt": len(inserts) if wrote else 0,
        "updated_this_attempt": len(updates) if wrote else 0,
        "recovered_operations": recovered,
        "artifacts": artifacts,
        "open_reviews": len(remaining),
    }


def inspect(config, source_id=None, productions=()):
    config.validate()
    if config.family != "ASIGNACION_PRODUCCION_RAMAL":
        raise ConfigurationError("Se requiere family=ASIGNACION_PRODUCCION_RAMAL")
    if not config.control_path.exists():
        return {"status": "NO_ASSOCIATIONS", "fresh": False, "complete": False}
    with closing(sqlite3.connect(config.control_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        expected = canonical(
            {
                "plant": config.plant,
                "silver_root": str(config.silver_root),
                "bronze_root": str(config.bronze_root),
                "contract": a.CONTRACT,
            }
        )
        if settings.get("binding") != expected:
            raise IntegrityError("Control de otro ámbito")
        row = db.execute(
            "SELECT delta_version,payload_json FROM publications ORDER BY no DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {"status": "NO_ASSOCIATIONS", "fresh": False, "complete": False}
        version, pub = row[0], json.loads(row[1])
        pending = db.execute(
            "SELECT COUNT(*) FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
        ).fetchone()[0]
    bundle = read_artifact(config, pub["artifacts"]["bundle"])
    ledger = read_artifact(config, pub["artifacts"]["ledger"])
    store = AssociationStore(config, settings["control_id"])
    latest = store.table()
    accepted = (
        store.read(DeltaTable(str(config.table_path), version=version))[1]
        if version is not None
        else {}
    )
    stale = []
    for family, old in pub["manifest"].items():
        try:
            _, _, current, _ = source_snapshot(config, family, read=False)
            if current != old:
                stale.append(family)
        except IntegrityError:
            stale.append(family)
    if digest(decisions(config)) != pub["decision_hash"]:
        stale.append("decisions")
    if dict(config.line_by_origin) != pub["lines"]:
        stale.append("lines")
    if bundle.get("topology_enabled", False) != config.topology_enabled or bundle.get(
        "line_codes", {}
    ) != dict(config.line_code_by_origin):
        stale.append("topology_configuration")
    fresh = not (stale or pending or (latest.version() if latest else None) != version)
    result = {
        "status": "STALE" if not fresh else "OK" if pub["complete"] else "PARTIAL",
        "fresh": fresh,
        "complete": fresh and pub["complete"],
        "delta_version": version,
        "stale_sources": stale,
        "associations": list(accepted.values()),
        "candidates": bundle["proposals"],
        "counts": pub["counts"],
        "pending_rows": pub["pending_rows"],
        "open_reviews": pub["open_reviews"],
        "rule": a.RULE,
    }
    if source_id is not None:
        entries = [r for r in ledger if r["source_id"] == source_id]
        result["explanation"] = [
            {**r, "decisions": [bundle["registry"][i] for i in r["episode_ids"]]} for r in entries
        ]
    if productions:
        selected = [r for r in ledger if set(r["productions"]) & set(productions)]
        physical = {r["source_id"] for r in selected if r["family"] == "CONT_ZC"}
        result["selection"] = {
            "productions": list(productions),
            "physical_rows": len(selected),
            "physical_zc_rows": len(physical),
            "zc_yield": cont_zc.yield_summary(
                [r for r in bundle["sources"]["CONT_ZC"] if a.source_id("CONT_ZC", r) in physical]
            ),
        }
    return result
