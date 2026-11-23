"""Publicar episodios revisados y un ledger reproducible, sin conectar todavía la app."""

import json
import sqlite3
from contextlib import closing

from deltalake import DeltaTable

from . import __version__, cont_zc, physical_assignments
from . import associations as a
from .association_storage import (
    PHYSICAL_SOURCES,
    AssociationStore,
    artifact,
    load_bundle,
    read_artifact,
    source_snapshot,
    verify_artifacts,
)
from .common import ConfigurationError, DependencyPending, IntegrityError, canonical, digest
from .service import _apply

PUBLICATION_POLICY = "streamed-publication-v5"


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
        scope = tuple(payload["scope"])
        accepted = {
            identity: row for identity, row in registry.items() if a.decision_scope(row) == scope
        }
        # The incoming proposal can be accepted verbatim once the outgoing interval changes.
        if scope not in a.validate_registry(accepted) and all(
            (identity := a.decision_identity(candidate)) in current_ids
            and identity in accepted
            and accepted[identity]["revision"] >= candidate["revision"]
            for candidate in variants
        ):
            return True
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


def _review_hash(control, omit=()):
    return digest(
        [
            list(row)
            for row in control.db.execute(
                "SELECT issue_id,reason,status,payload_json FROM issues "
                "WHERE status!='RESOLVED' ORDER BY issue_id"
            )
            if row[0] not in omit
        ]
    )


def process(config, control, attempt):
    # Commit small, independent topology changes before computing the observation
    # ledger. A failure of that heavier stage cannot undo an accepted episode.
    physical = None
    if config.topology_enabled:
        physical = _process(config, control, attempt, physical=True)
        from .supervision_export import export

        export(control, config, physical, force=True)
    result = _process(config, control, attempt)
    if physical:
        for key in ("inserted_this_attempt", "updated_this_attempt"):
            result[key] += physical.get(key, 0)
        result["physical_publication"] = {
            key: physical[key]
            for key in ("status", "delta_version", "accepted_associations", "complete")
        }
    return result


def _frozen_observations(config, control, bundle, floor):
    existing = control.latest("full")
    prior = {}
    if existing:
        publication = json.loads(existing["payload_json"])
        prior = {
            r["source_id"]: r for r in read_artifact(config, publication["artifacts"]["ledger"])
        }
    frozen = {}
    for family in ("LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC"):
        for row in bundle["sources"][family]:
            end = row["FECHA_OFF"] if family == "LISTA_AR" else row["FECHA"]
            if end is None or end > floor:
                continue
            identity = a.source_id(family, row)
            frozen[identity] = prior.get(identity) or {
                "family": family,
                "source_id": identity,
                "key": a.SOURCE_KEYS[family](row),
                "origin": row["origen_fenix"],
                "status": "PENDING_ASSOCIATION",
                "productions": [],
                "episode_ids": [],
                "reason": "Observación anterior al inicio de la nueva política Live",
                "rule": a.RULE,
            }
    return frozen


def _process(config, control, attempt, *, physical=False):
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
    scope = "physical" if physical else "full"
    latest = control.latest(scope) or control.latest()
    prior_pub = json.loads(latest["payload_json"]) if latest else None
    current_publication = control.latest()
    current_pub = json.loads(current_publication["payload_json"]) if current_publication else None
    doc = decisions(config)
    live = config.pipeline_lane == "live" and config.topology_enabled
    floor = control.setting("live_handover_from") if live else None
    if live and floor is None:
        if config.processing_window:
            floor = config.processing_window[0]
        elif not physical and config.runtime_root is not None:
            raise DependencyPending("Esperando el primer corte Live para publicar atribuciones")
    observation_window = (
        tuple(config.processing_window)
        if config.pipeline_lane == "live" and not physical and config.processing_window
        else None
    )
    if observation_window and floor and observation_window[0] < floor:
        if observation_window[1] <= floor:
            raise DependencyPending("El corte seleccionado precede al comienzo prospectivo Live")
        observation_window = (floor, observation_window[1])
    if (
        config.pipeline_lane == "live"
        and not physical
        and config.runtime_root
        and not observation_window
    ):
        raise DependencyPending("Esperando un corte Live confirmado para construir el ledger")
    lines = dict(config.line_by_origin)
    fingerprint = {
        "contract": a.CONTRACT,
        "scope": scope,
        "rule": a.RULE,
        "policy": PUBLICATION_POLICY,
        **({"observation_window": list(observation_window)} if observation_window else {}),
        **({"live_handover": "mp9_topology_v1", "live_handover_from": floor} if live else {}),
        "ar_interpretation_rules": a.ar_interpretation_rules(),
        "transformer_version": __version__,
        "timestamp_policy": config.timestamp_policy,
        "manifest": {
            family: source_snapshot(config, family, read=False)[2]
            for family in (
                f
                for f in a.SOURCE_KEYS
                if config.topology_enabled or f not in {"SIP_MAQUINA_MOLDEO", "SIP_LINEA_ZF"}
                if not physical or f in PHYSICAL_SOURCES
            )
        },
        "decision_hash": digest(doc),
        "lines": lines,
        "topology_enabled": config.topology_enabled,
        "line_codes": dict(config.line_code_by_origin),
    }
    old_registry = None
    if current_pub and "registry" in current_pub:
        old_registry = current_pub["registry"]
        if digest(old_registry) != current_pub["registry_hash"]:
            raise IntegrityError("Registro de decisiones de la publicación alterado")
    if (
        prior_pub
        and prior_pub.get("input_fingerprint") == fingerprint
        and prior_pub.get("review_hash") == _review_hash(control)
        and prior_pub.get("assignment_hash") == a.content_hash(previous)
    ):
        verify_artifacts(config, prior_pub)
        return {
            "status": "NO_CHANGE" if prior_pub["complete"] else "PARTIAL",
            "complete": prior_pub["complete"],
            "batch_id": latest["batch_id"],
            "attempt_id": attempt,
            "delta_version": version,
            "accepted_associations": len(previous),
            "approved_operational_decisions": len(prior_pub["registry"]),
            "candidate_associations": prior_pub["candidate_associations"],
            "pending_rows": prior_pub["pending_rows"],
            "counts": prior_pub["counts"],
            "inserted_this_attempt": 0,
            "updated_this_attempt": 0,
            "recovered_operations": recovered,
            "artifacts": prior_pub["artifacts"],
            "open_reviews": prior_pub["open_reviews"],
            "recomputed_source_rows": 0,
        }
    if old_registry is None:
        old_registry = (
            read_artifact(config, current_pub["artifacts"]["bundle"])["registry"]
            if current_pub
            else {}
        )
    bundle = load_bundle(
        config,
        physical=physical,
        manifest=fingerprint["manifest"],
        observation_window=observation_window,
    )
    if fingerprint["manifest"] != bundle["manifest"]:
        raise DependencyPending("Las fuentes cambiaron durante la preparación de asociaciones")
    if live:
        bundle["live_handover"] = True
        if floor is not None and control.setting("live_handover_from") is None:
            with control.db:
                control.db.execute("INSERT INTO settings VALUES('live_handover_from',?)", (floor,))
    effective = list(doc["decisions"])
    if physical:
        effective = [
            d
            for d in effective
            if all(
                e.get("family") in PHYSICAL_SOURCES or "external" in e
                for e in d.get("evidence", [])
            )
        ]
    if config.topology_enabled:
        automatic = physical_assignments.decisions(
            bundle,
            old_registry,
            dict(config.line_code_by_origin),
            live=config.pipeline_lane == "live",
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
    # Earlier cuts remain in their own immutable ledgers. The bounded cut needs
    # no prior ledger read; clamp the scope to the prospective activation.
    frozen = (
        _frozen_observations(config, control, bundle, floor)
        if floor and not physical and not observation_window
        else {}
    )
    ledger = a.assign(bundle, registry, index, review_scopes, frozen=frozen)
    counts = a.diagnostics(ledger)
    bundle.update(
        **({"observation_window": list(observation_window)} if observation_window else {}),
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
    pending = sum(r["status"] not in a.ACCEPTED_STATUSES for r in ledger)
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
        "scope": scope,
        **({"observation_window": list(observation_window)} if observation_window else {}),
        "observation_status": "NOT_EVALUATED" if physical else "EVALUATED",
        "artifacts": artifacts,
        "manifest": bundle["manifest"],
        "decision_hash": digest(doc),
        "lines": lines,
        "complete": complete,
        "counts": counts,
        "pending_rows": pending,
        "open_reviews": len(remaining),
        "changes": changes,
        "recomputed_source_rows": len(ledger) - len(frozen),
        "rule": a.RULE,
        "registry": registry,
        "registry_hash": digest(registry),
        "input_fingerprint": fingerprint,
        "assignment_hash": a.content_hash(projected),
        "review_hash": _review_hash(control, resolved),
        "candidate_associations": len(proposals),
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
        "publication_scope": scope,
        "observation_status": publication["observation_status"],
        "recomputed_source_rows": publication["recomputed_source_rows"],
        "source_manifest": bundle["manifest"],
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
        "complete": fresh and pub["complete"] and pub.get("scope", "full") == "full",
        "publication_scope": pub.get("scope", "full"),
        "observation_status": pub.get("observation_status", "EVALUATED"),
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
