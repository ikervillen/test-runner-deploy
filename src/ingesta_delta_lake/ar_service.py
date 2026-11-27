"""Ingesta de tramos AR; referencias persistentes y cierres sin cálculo de minutos."""

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from . import lista_ar as ar
from .ar_storage import ArStore, load_references
from .common import IntegrityError, digest, slot_stamp
from .service import _apply
from .sources import capture_identity, capture_inputs


def _confirmed_inputs(snapshots, cases, invalid_references):
    """Certify each delivery only when unresolved AR could not affect its slot."""
    confirmed = []
    for snapshot in snapshots:
        slot = slot_stamp(snapshot.producer_time)
        if slot is None:
            continue
        end = datetime.fromisoformat(slot)
        start = end - timedelta(minutes=10)
        if any(_blocks_slot(case, snapshot, start, end) for case in cases) or any(
            _blocks_slot(row, snapshot, start, end) for row in invalid_references
        ):
            continue
        confirmed.append(capture_identity("LISTA_AR", snapshot))
    return confirmed


def _blocks_slot(case, snapshot, start, end):
    evidence = case.get("evidence", ())
    if isinstance(evidence, dict):
        evidence = [evidence]
    origins = {item.get("origin") for item in evidence}
    # Even a malformed event dated outside its delivery cannot certify that delivery.
    if any(
        item.get("origin") == snapshot.origin and item.get("sha256") == snapshot.sha256
        for item in evidence
    ):
        return True
    for row in case.get("candidates") or [case.get("candidate", case)]:
        origin = row.get("origen_fenix")
        if origin is not None and origin != snapshot.origin:
            continue
        if origin is None and origins and None not in origins and snapshot.origin not in origins:
            continue
        on, off = row.get("FECHA_ON"), row.get("FECHA_OFF")
        if not on or (
            datetime.fromisoformat(on) < end and (not off or datetime.fromisoformat(off) > start)
        ):
            return True
    return False


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
    snapshots = capture_inputs(config, control, attempt)
    parsed = ar.parse(snapshots, config.encoding)
    input_variants = len(parsed.entries)
    descriptors = [s.descriptor() for s in snapshots]
    restored = 0
    checked = set()
    for pending in control.dependency_candidates():
        if config.scheduled_lot_attempt and ar.reference_gaps(pending["candidate"], references):
            continue
        for ref in pending["evidence"]:
            path = Path(ref["bronze_path"]).resolve()
            identity = path, ref["sha256"]
            if identity not in checked:
                if (
                    not path.is_relative_to(config.bronze_root)
                    or hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]
                ):
                    raise IntegrityError("Evidencia de dependencia AR alterada o fuera de Bronze")
                checked.add(identity)
            descriptors.append(
                {k: ref[k] for k in ("origin", "source_path", "bronze_path", "sha256")}
            )
        parsed.add(pending["candidate"], pending["evidence"])
        restored += 1
    from .review_adapters import filter_rejected

    filter_rejected(control, parsed)
    inserts, updates, issues, matches = ar.decide(parsed, existing, references, control.held())
    merged = {
        **existing,
        **{ar.key(r): {f: r[f] for f in ar.SCHEMA.names} for r in [*inserts, *updates]},
    }
    invalid_references = [r for r in merged.values() if ar.reference_gaps(r, references)]
    reference_pending = len(invalid_references)
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
    confirmed_inputs = (
        _confirmed_inputs(
            snapshots, [payload for _, _, _, payload in control.cases()], invalid_references
        )
        if config.scheduled_lot_attempt
        else []
    )
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
        "confirmed_input_ids": confirmed_inputs,
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
