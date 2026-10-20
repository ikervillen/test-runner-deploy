"""Unidades MP9 independientes: cierres, dependencias reintentables y revisión persistente."""

import hashlib
from pathlib import Path

from . import __version__, mp9
from .common import IntegrityError, digest
from .mp9_control import CONFLICTS, DEPENDENCIES
from .mp9_storage import Mp9Store, load_models
from .service import _apply
from .sources import capture_inputs


def process(config, control, attempt):
    models, model_snapshot = load_models(config)
    store = Mp9Store(config, control.control_id)
    recovered, inserted_recovery, updated_recovery = [], 0, 0
    for pending in control.pending():
        prepared = control.prepared(pending)
        version, wrote = _apply(config, control, store, pending)
        inserts, updates = len(prepared["inserts"]), len(prepared["updates"])
        inserted_recovery += inserts if wrote else 0
        updated_recovery += updates if wrote else 0
        recovered.append(
            {
                "operation_id": pending["operation_id"],
                "delta_version": version,
                "confirmed_rows": inserts,
                "confirmed_updates": updates,
                "wrote_this_attempt": wrote,
            }
        )
    version, existing = store.read()
    recorded = control.setting("last_version")
    if recorded is not None and recorded != str(version):
        raise IntegrityError("El snapshot MP9 no coincide con su control")
    snapshots = capture_inputs(config, control, attempt)
    parsed = mp9.parse(snapshots, config.encoding)
    input_variants = len(parsed.entries)
    descriptors = [s.descriptor() for s in snapshots]
    restored = 0
    for pending in control.dependency_candidates():
        for ref in pending["evidence"]:
            path = Path(ref["bronze_path"]).resolve()
            if (
                not path.is_relative_to(config.bronze_root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]
            ):
                raise IntegrityError("Evidencia de dependencia MP9 alterada o fuera de Bronze")
            descriptors.append(
                {k: ref[k] for k in ("bronze_path", "sha256", "origin", "source_path")}
            )
        parsed.add(pending["candidate"], pending["evidence"])
        restored += 1
    inserts, updates, issues, matches = mp9.decide(parsed, existing, set(models), control.held())
    identities = {(e[0], e[1]) for e in parsed.entries} | parsed.invalid_keys
    stats = {
        "rows_read": parsed.rows_read,
        "unique_records": len(identities),
        "normalized_variants": len(parsed.entries),
        "consolidated_repetitions": max(0, parsed.rows_read - parsed.invalid_rows - input_variants),
        "invalid_rows": parsed.invalid_rows,
        "restored_dependencies": restored,
        "new_entries": len(inserts),
        "closures": len(updates),
        "matched_entries": matches,
        "conflict_entries": sum(i["reason"] in CONFLICTS for i in issues),
        "pending_entries": sum(i["reason"] in DEPENDENCIES for i in issues),
        "issues": len(issues),
    }
    merged = {
        **existing,
        **{mp9.key(r): {f: r[f] for f in mp9.SCHEMA.names} for r in [*inserts, *updates]},
    }
    batch_id = digest(
        [
            mp9.CONTRACT,
            __version__,
            config.plant,
            config.encoding,
            config.timestamp_policy,
            version,
            model_snapshot,
            sorted({(d["origin"], d["sha256"]) for d in descriptors}),
            sorted((e[0], e[1], digest(e)) for e in parsed.entries),
        ]
    )
    state = "PARTIAL" if issues else "COMPLETED"
    prepared = {
        "contract": mp9.CONTRACT,
        "transformer_version": __version__,
        "timestamp_policy": config.timestamp_policy,
        "snapshots": descriptors,
        "model_snapshot": model_snapshot,
        "before_version": version,
        "before_hash": mp9.content_hash(existing),
        "after_hash": mp9.content_hash(merged),
        "inserts": inserts,
        "updates": updates,
        "outcome_state": state,
        "stats": stats,
    }
    control.observe(batch_id, prepared, issues, config.plant)
    prior = control.batch(batch_id)
    wrote = False
    if prior is None:
        operation = digest([batch_id, "mp9_insert_close_v1"])
        control.prepare(batch_id, operation, prepared)
        version, wrote = _apply(config, control, store, control.batch(batch_id))
    elif inserts or updates:
        raise IntegrityError("Un lote MP9 confirmado exige escrituras inesperadas")
    _, accepted = store.read()
    control.resolve_dependencies(accepted, batch_id)
    counts = control.counts()
    complete = not (counts["open_reviews"] or counts["open_dependencies"])
    if not complete:
        status = (
            "PARTIAL"
            if accepted
            else "WAITING_DEPENDENCIES"
            if not counts["open_reviews"]
            else "BLOCKED"
        )
    else:
        status = "APPLIED" if wrote else "NO_CHANGE"
    if not snapshots and not restored and complete:
        status = "NO_INPUTS"
    return {
        "attempt_id": attempt,
        "batch_id": batch_id,
        "contract": mp9.CONTRACT,
        "plant": config.plant,
        "source_files": len(snapshots),
        "table_path": str(config.table_path),
        "control_path": str(config.control_path),
        "timestamp_policy": config.timestamp_policy,
        "model_snapshot": model_snapshot,
        "delta_version": version,
        "status": status,
        "complete": complete,
        "published_records": len(accepted),
        "recovered_operations": recovered,
        "inserted_this_attempt": inserted_recovery + (len(inserts) if wrote else 0),
        "updated_this_attempt": updated_recovery + (len(updates) if wrote else 0),
        **stats,
        **counts,
    }
