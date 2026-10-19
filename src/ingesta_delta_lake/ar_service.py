"""Ingesta de tramos AR; referencias persistentes y cierres sin cálculo de minutos."""

import hashlib
from pathlib import Path

from . import __version__
from . import lista_ar as ar
from .ar_storage import ArStore, load_references
from .common import IntegrityError, digest
from .service import _apply
from .sources import capture, discover


def process(config, control, attempt):
    store = ArStore(config, control.control_id)
    recovered, recovered_inserts, recovered_updates = [], 0, 0
    for pending in control.pending():
        prepared = control.prepared(pending)
        version, wrote = _apply(config, control, store, pending)
        n, c = len(prepared["inserts"]), len(prepared["updates"])
        recovered_inserts += n if wrote else 0
        recovered_updates += c if wrote else 0
        recovered.append(
            {
                "operation_id": pending["operation_id"],
                "delta_version": version,
                "confirmed_rows": n,
                "confirmed_updates": c,
                "wrote_this_attempt": wrote,
            }
        )
    references, manifest = load_references(config)
    version, existing = store.read()
    if control.setting("last_version") not in (None, str(version)):
        raise IntegrityError("Snapshot LISTA_AR distinto de su control")
    snapshots = [capture(config, origin, path, stamp) for origin, path, stamp in discover(config)]
    control.receipts(attempt, snapshots)
    parsed = ar.parse(snapshots, config.encoding)
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
                raise IntegrityError("Evidencia de dependencia AR alterada o fuera de Bronze")
            descriptors.append(
                {k: ref[k] for k in ("origin", "source_path", "bronze_path", "sha256")}
            )
        parsed.add(pending["candidate"], pending["evidence"])
        restored += 1
    inserts, updates, issues, matches = ar.decide(parsed, existing, references, control.held())
    merged = {
        **existing,
        **{ar.key(r): {f: r[f] for f in ar.SCHEMA.names} for r in [*inserts, *updates]},
    }
    reference_pending = sum(bool(ar.reference_gaps(r, references)) for r in merged.values())
    stats = {
        "rows_read": parsed.rows_read,
        "normalized_variants": len(parsed.entries),
        "consolidated_repetitions": max(0, parsed.rows_read - parsed.invalid_rows - input_variants),
        "invalid_rows": parsed.invalid_rows,
        "restored_dependencies": restored,
        "new_entries": len(inserts),
        "closures": len(updates),
        "matched_entries": matches,
        "conflict_entries": sum(
            i["reason"] in {"CLOSURE_CONFLICT", "REVIEW_REQUIRED"} for i in issues
        ),
        "pending_entries": sum(i["reason"] == "REFERENCE_PENDING" for i in issues),
    }
    batch_id = digest(
        [
            ar.CONTRACT,
            __version__,
            config.plant,
            config.encoding,
            config.timestamp_policy,
            version,
            manifest,
            sorted({(d["origin"], d["sha256"]) for d in descriptors}),
            sorted(digest(e) for e in parsed.entries),
            digest(control.cases()),
        ]
    )
    control.observe(batch_id, issues, config.plant)
    counts = control.counts()
    prepared = {
        "contract": ar.CONTRACT,
        "transformer_version": __version__,
        "timestamp_policy": config.timestamp_policy,
        "snapshots": descriptors,
        "source_manifest": manifest,
        "before_version": version,
        "before_hash": ar.content_hash(existing),
        "after_hash": ar.content_hash(merged),
        "inserts": inserts,
        "updates": updates,
        "stats": stats,
        "accepted_reference_pending": reference_pending,
        "outcome_state": "PARTIAL"
        if counts["open_reviews"] or issues or reference_pending
        else "COMPLETED",
    }
    prior, wrote = control.batch(batch_id), False
    if prior is None:
        control.prepare(batch_id, digest([batch_id, "lista_ar_insert_close_v1"]), prepared)
        version, wrote = _apply(config, control, store, control.batch(batch_id))
    elif inserts or updates:
        raise IntegrityError("Lote AR confirmado exige nuevas escrituras")
    control.resolve_dependencies(merged, references, batch_id)
    counts = control.counts()
    complete = not (counts["open_dependencies"] or counts["open_reviews"] or reference_pending)
    status = (
        ("APPLIED" if wrote else "NO_CHANGE" if snapshots or restored else "NO_INPUTS")
        if complete
        else "PARTIAL"
        if merged
        else "WAITING_DEPENDENCIES"
        if not counts["open_reviews"]
        else "BLOCKED"
    )
    return {
        "attempt_id": attempt,
        "batch_id": batch_id,
        "contract": ar.CONTRACT,
        "plant": config.plant,
        "table_path": str(config.table_path),
        "control_path": str(config.control_path),
        "source_files": len(snapshots),
        "reference_snapshots": manifest,
        "delta_version": version,
        "status": status,
        "complete": complete,
        "published_records": len(merged),
        "accepted_reference_pending": reference_pending,
        "published_with_group": sum(r["ID_Agrupación"] is not None for r in merged.values()),
        "published_without_group": sum(r["ID_Agrupación"] is None for r in merged.values()),
        "recovered_operations": recovered,
        "inserted_this_attempt": recovered_inserts + (len(inserts) if wrote else 0),
        "updated_this_attempt": recovered_updates + (len(updates) if wrote else 0),
        **stats,
        **counts,
    }
