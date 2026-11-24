"""Protocolo de observaciones MIA; contratos/adaptadores separados, sin cambiar el score."""

import json
import sqlite3
import sys
import time
from collections import Counter
from contextlib import closing
from dataclasses import replace

from . import __version__, rech_plan
from . import lista_defectos as defects
from .common import (
    ConfigurationError,
    IntegrityError,
    canonical,
    digest,
    history_yields,
    horizon_marks,
    shared_bronze_window,
    slot_stamp,
    split_live_backlog,
)
from .compact_control import PreparedArtifactTooLarge
from .defects_storage import DefectsStore
from .mia_scope import files, group_holds, scope_for
from .service import _apply
from .sources import available_captures, discover, input_scan, orphan_captures, stable_capture

MAX_FILE_BYTES = 8 * 1024 * 1024
POLICY = "bounded-context-v2"
OVERSIZED_RECH = "lista_rech_oversized_preparation_v1"


def slot_commits(snapshots):
    """One commit is every file Bronze copied in the same ten-minute slot.

    The filename clock decides the slot. A file count or a byte size does not.
    Files with no clock stay together so a capture without a time is still stored.
    """
    groups = {}
    loose = []
    for snapshot in snapshots:
        slot = slot_stamp(getattr(snapshot, "producer_time", None))
        if slot is None:
            loose.append(snapshot)
            continue
        groups.setdefault(slot, []).append(snapshot)
    for slot in sorted(groups):
        yield groups[slot]
    if loose:
        yield loose


def reconcile_pending(config, control, store_class):
    store = store_class(config, control.control_id)
    recovered, count = [], 0
    for pending in control.pending():
        version, wrote = _apply(config, control, store, pending)
        entries = len(control.prepared(pending)["inserts"])
        count += entries if wrote else 0
        recovered.append(
            {
                "operation_id": pending["operation_id"],
                "delta_version": version,
                "confirmed_rows": entries,
                "wrote_this_attempt": wrote,
            }
        )
    return recovered, count


def process(
    config,
    control,
    attempt,
    *,
    model=defects,
    store_class=DefectsStore,
    operation_kind="lista_defectos_insert_v2",
    stop_requested=None,
    lane="all",
):
    # Technical recovery has priority over intake; an unavailable new CSV must
    # never prevent reconciliation of an already prepared/committed operation.
    recovered, recovery_count = reconcile_pending(config, control, store_class)
    captured = control.captured_inputs()
    snapshots, verified = [], set()
    with input_scan(config, control) as due:
        if due:
            for snapshot in available_captures(
                replace(
                    config,
                    max_file_bytes=config.max_file_bytes
                    if control.compact
                    else min(config.max_file_bytes, MAX_FILE_BYTES),
                ),
                known=captured,
                verified=verified,
                discoverer=discover,
                capturer=stable_capture,
            ):
                # Durable as soon as captured: a later capture error cannot lose this input.
                control.receipts(attempt, [snapshot])
                snapshots.append(snapshot)
                captured.setdefault((snapshot.origin, snapshot.sha256), snapshot)
            if config.intake_path is None:
                for snapshot in orphan_captures(config, captured):
                    control.receipts(attempt, [snapshot])
                    captured[(snapshot.origin, snapshot.sha256)] = snapshot
    consumed = control.consumed_inputs()
    scoped_live = lane == "live" and bool(config.intake_ids)
    # The independent live scheduler already chose the Bronze captures for this turn.
    # In particular, captures with no filename hour still need a turn of their own.
    queue = captured
    if scoped_live:
        queue = {(s.origin, s.sha256): s for s in snapshots if (s.origin, s.sha256) not in consumed}
    parents = set()
    if control.compact:
        from .input_fragments import expand

        parents = expand(control, queue, max_file_bytes=MAX_FILE_BYTES)
    pending = [s for key, s in queue.items() if key not in consumed and key not in parents]
    if any(s.size > MAX_FILE_BYTES for s in pending):
        raise IntegrityError("Captured MIA file exceeds 8 MiB; split-file support is required")
    # Export timestamps order versions, never determine production or sample time.
    pending.sort(
        key=lambda s: (s.producer_time or "", s.mtime_ns, s.origin, s.source_path, s.sha256)
    )
    result, totals, completed, batch_count = None, {"inserted_this_attempt": recovery_count}, 0, 0
    cumulative = (
        "rows_read",
        "normalized_rows",
        "invalid_rows",
        "consolidated_repetitions",
        "new_entries",
        "matched_entries",
        "conflicts",
        "counter_revisions",
        "inspection_conflicts",
        "inserted_this_attempt",
    )
    wrote = recovery_count > 0
    stopped = False
    # One clock window for every line. An older file on one line is backlog.
    window, origins = shared_bronze_window(
        config.control_root, selected_window=config.processing_window
    )
    if scoped_live:
        live, backlog = pending, []
        bronze_now = {origin: window[0] for origin in origins} if window else {}
    elif window is None:
        live, backlog, bronze_now = [], list(pending), {}
    else:
        live, backlog, bronze_now = split_live_backlog(
            pending, list(captured.values()), window, origins
        )
    if lane != "history" and not live:
        control.set_live_slot(bronze_now)
    if lane == "history" and config.intake_path is None and history_yields():
        remaining = list(backlog)
        return {
            "status": "BACKLOG_PENDING" if remaining else "NO_INPUTS",
            "complete": not remaining,
            "batch_id": None,
            "remaining_versions": len(remaining),
            "processed_versions": 0,
            "recovered_operations": recovered,
            "inserted_this_attempt": recovery_count,
            "source_files": len(snapshots),
            "pending_by_origin": dict(Counter(s.origin for s in remaining)),
            "pending_bytes": sum(s.size for s in remaining),
            "oldest_pending_received_at": min((s.received_at for s in remaining), default=None),
            **control.progress(0, len(remaining)),
            "completed_batches": 0,
            "stats_scope": "sum_of_processed_batches",
        }
    phases = []
    if lane != "history" and live:
        phases.append((True, [live]))
    if lane != "live" and backlog:
        phases.append((False, slot_commits(backlog)))
    if not phases:
        phases = [(False, [[]])]
    bounded_rech = control.compact and config.family == "LISTA_RECH"
    for is_live, selected in phases:
        for batch in selected:
            if result is not None and (
                (stop_requested is not None and stop_requested())
                or (lane == "history" and config.intake_path is None and history_yields())
            ):
                stopped = True
                break
            started = time.monotonic()
            kwargs = dict(model=model, store_class=store_class, operation_kind=operation_kind)
            plan = None
            if bounded_rech and sum(s.size for s in batch) > rech_plan.PLAN_THRESHOLD:
                plan = rech_plan.InspectionPlan(control, model, batch)
                result = process_inspection_plan(config, control, attempt, batch, plan, kwargs)
            else:
                result = process_batch(config, control, attempt, snapshots=batch, **kwargs)
            control.acknowledge_inputs(result["batch_id"], batch)
            if plan is not None:
                plan.discard()
            if is_live:
                control.set_live_slot(bronze_now)
            recovered.extend(result["recovered_operations"])
            batch_count += bool(batch)
            wrote = wrote or result["status"] == "APPLIED"
            completed += len(batch)
            print(
                canonical(
                    {
                        "event": "INPUT_BATCH_CONFIRMED",
                        "family": config.family,
                        "batch_id": result["batch_id"],
                        "processed_versions": completed,
                        "remaining_versions": len(pending) - completed,
                        "input_bytes": sum(s.size for s in batch),
                        "delta_version": result["delta_version"],
                        "seconds": round(time.monotonic() - started, 3),
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
            for key in cumulative:
                totals[key] = totals.get(key, 0) + result[key]
        if stopped:
            break
    status = result["status"]
    if result["complete"]:
        status = "APPLIED" if wrote else "NO_CHANGE" if snapshots else "NO_INPUTS"
    if stopped:
        status = "STOPPED_AFTER_BATCH"
    acknowledged = control.consumed_inputs()
    # The independent scheduler publishes only the selected captures. Other
    # windows in this family's control queue cannot delay that confirmation.
    counted = snapshots if scoped_live else pending
    remaining = [s for s in counted if (s.origin, s.sha256) not in acknowledged]
    return {
        **result,
        **totals,
        "status": status,
        "complete": result["complete"] and not stopped,
        "quality_complete": result["complete"],
        "source_files": len(snapshots),
        "processed_versions": completed,
        "remaining_versions": len(remaining) if scoped_live else len(pending) - completed,
        "pending_by_origin": dict(Counter(s.origin for s in remaining)),
        "pending_bytes": sum(s.size for s in remaining),
        "oldest_pending_received_at": min((s.received_at for s in remaining), default=None),
        **control.progress(len(acknowledged), len(remaining)),
        "completed_batches": batch_count,
        "recovered_operations": recovered,
        "stats_scope": "sum_of_processed_batches",
    }


def process_inspection_plan(config, control, attempt, snapshots, plan, kwargs):
    plan.stage(snapshots, config.encoding)
    cursor, recorded = plan.state()
    prior = json.loads(recorded) if recorded else None
    totals = prior["totals"] if prior else {}
    stats = (
        "rows_read",
        "normalized_rows",
        "invalid_rows",
        "consolidated_repetitions",
        "new_entries",
        "matched_entries",
        "conflicts",
        "counter_revisions",
        "inspection_conflicts",
        "inserted_this_attempt",
    )
    result = prior["result"] if prior else None
    while True:
        keys = plan.next_unit(cursor)
        if not keys and result is not None:
            control.advance_observed_until(plan.observed())
            break
        result = process_batch(
            config,
            control,
            attempt,
            snapshots=snapshots,
            parsed_unit=plan.load(keys),
            plan_scope=(plan.identity, cursor),
            **kwargs,
        )
        for name in stats:
            totals[name] = totals.get(name, 0) + result[name]
        cursor = keys[-1] if keys else cursor
        plan.checkpoint(cursor, {"totals": totals, "result": result})
    if result["complete"] and totals["inserted_this_attempt"]:
        result["status"] = "APPLIED"
    return {**result, **totals, "attempt_id": attempt}


def process_batch(
    config,
    control,
    attempt,
    *,
    snapshots,
    model=defects,
    store_class=DefectsStore,
    operation_kind="lista_defectos_insert_v2",
    parsed_unit=None,
    plan_scope=None,
):
    store = store_class(config, control.control_id)
    recovered, recovery_count = reconcile_pending(config, control, store_class)
    # Already under this family's writer lock. Give decisions a turn between
    # bounded MIA batches, before reading the next snapshot or parsing new rows.
    from .reviews import process as process_reviews

    process_reviews(config, control)
    bounded_rech = control.compact and config.family == "LISTA_RECH"
    inputs = sorted({(s.origin, s.sha256) for s in snapshots})

    def recovery_fingerprint(cases):
        return digest(
            [
                __version__,
                config.encoding,
                config.timestamp_policy,
                config.archive_context,
                control.setting("last_version"),
                inputs,
                plan_scope,
                cases,
            ]
        )

    blocked = control.setting(OVERSIZED_RECH) if bounded_rech else None
    if blocked and blocked == recovery_fingerprint(control.cases()):
        raise PreparedArtifactTooLarge(
            "Prepared artifact exceeds recovery budget (unchanged input)"
        )
    parsed = parsed_unit if parsed_unit is not None else model.parse(snapshots, config.encoding)
    from .review_adapters import filter_rejected

    filter_rejected(control, parsed)
    scope = scope_for(
        parsed, model, exact_periods=bounded_rech, inspection_signatures=plan_scope is not None
    )
    store.set_scope(scope)
    before_files = files(store.table())
    from .pipeline_retired import logical_rows

    published_rows = logical_rows(control, store.table())
    control.ensure_inspection_index(
        store.table().version() if store.table() is not None else None, published_rows
    )
    version, existing = store.read()
    if control.setting("last_version") not in (None, str(version)):
        raise IntegrityError(f"El snapshot {config.family} no coincide con su control")
    inserts, issues, matches = model.decide(parsed, existing, *control.held())
    merged = {
        **existing,
        **{model.key(r): {f: r[f] for f in model.SCHEMA.names} for r in inserts},
    }
    batch_id = digest(
        [
            model.CONTRACT,
            POLICY,
            __version__,
            config.plant,
            config.encoding,
            config.timestamp_policy,
            version,
            inputs,
            digest(control.cases()),
            *([plan_scope] if plan_scope else []),
        ]
    )
    stats = {
        "rows_read": parsed.rows_read,
        "normalized_rows": len(parsed.entries),
        "invalid_rows": parsed.invalid_rows,
        "consolidated_repetitions": max(
            0, parsed.rows_read - parsed.invalid_rows - len(parsed.entries)
        ),
        "new_entries": len(inserts),
        "matched_entries": matches,
        "conflicts": sum(i["reason"] in {"ROW_CONFLICT", "REVIEW_REQUIRED"} for i in issues),
        "counter_revisions": sum(i["reason"] == "COUNTER_REVISION" for i in issues),
        "inspection_conflicts": sum(i["reason"] == "INSPECTION_GROUP_CONFLICT" for i in issues),
    }
    control.rejected(batch_id, {}, issues, config.plant, create_batch=False)
    cases = control.cases()
    groups = None if bounded_rech else model.inspection_groups(merged, cases)
    # LISTA_RECH admits at most one accepted row per signature. Pending groups
    # come from open cases, and the confirmed scoped read enforces uniqueness.
    complete = not cases and (groups is None or all(g["status"] == "RESOLVED" for g in groups))
    prepared = {
        "contract": model.CONTRACT,
        "transformer_version": __version__,
        "timestamp_policy": config.timestamp_policy,
        "snapshots": [s.descriptor() for s in snapshots],
        "before_version": version,
        "before_hash": model.content_hash(existing),
        "after_hash": model.content_hash(merged),
        "inserts": inserts,
        "outcome_state": "COMPLETED" if complete else "PARTIAL",
        "stats": stats,
        "read_scope": scope,
        "inspection_holds": group_holds(cases),
    }
    if plan_scope is not None:
        prepared["inspection_plan"] = list(plan_scope)
    if bounded_rech:
        prepared["before_files_hash"] = digest(before_files)
        prepared["inspection_groups_from_confirmed_rows"] = True
    else:
        prepared.update(before_files=before_files, inspection_groups=groups)
    prior = control.batch(batch_id)
    wrote = False
    scoped_rows = len(merged)
    blank_marks = dict(getattr(parsed, "observed", {}))
    del existing, merged, parsed
    if prior is None:
        try:
            control.prepare(batch_id, digest([batch_id, operation_kind]), prepared)
        except PreparedArtifactTooLarge:
            if bounded_rech:
                with control.db:
                    control.db.execute(
                        "INSERT INTO settings VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (OVERSIZED_RECH, recovery_fingerprint(cases)),
                    )
            raise
        version, wrote = _apply(config, control, store, control.batch(batch_id))
    elif inserts:
        raise IntegrityError(f"Un lote {config.family} confirmado exige nuevas inserciones")
    control.advance_observed_until(horizon_marks(blank_marks, inserts, wrote=wrote))
    status = (
        ("PARTIAL" if published_rows + len(inserts) else "BLOCKED")
        if not complete
        else ("APPLIED" if wrote else "NO_CHANGE" if snapshots else "NO_INPUTS")
    )
    return {
        "attempt_id": attempt,
        "batch_id": batch_id,
        "contract": model.CONTRACT,
        "plant": config.plant,
        "status": status,
        "complete": complete,
        "source_files": len(snapshots),
        "table_path": str(config.table_path),
        "control_path": str(config.control_path),
        "delta_version": version,
        "inserted_this_attempt": recovery_count + (len(inserts) if wrote else 0),
        "published_rows": published_rows + len(inserts),
        "context_rows": scoped_rows,
        "open_reviews": len(cases),
        **control.inspection_summary(),
        "inspection_metric": "inspections_by_context_not_unique_bottles",
        "recovered_operations": recovered,
        **stats,
    }


def inspection_bases(
    config,
    origin=None,
    *,
    model=defects,
    store_class=DefectsStore,
    family="LISTA_DEFECTOS",
    denominator_source="LISTA_RECH_separate_source",
):
    config.validate()
    if config.family != family:
        raise ConfigurationError(f"Las bases requieren family={family}")
    if not config.control_path.exists():
        return {"status": "NO_INPUTS", "complete": False, "groups": []}
    with closing(sqlite3.connect(f"{config.control_path.as_uri()}?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        expected = canonical(
            {
                "plant": config.plant,
                "silver_root": str(config.silver_root),
                "bronze_root": str(config.bronze_root),
                "contract": model.CONTRACT,
            }
        )
        if settings.get("binding") != expected:
            raise IntegrityError("El control de inspecciones pertenece a otro ámbito")
        if db.execute(
            "SELECT COUNT(*) FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
        ).fetchone()[0]:
            raise IntegrityError(f"Recuperar {family} antes de consultar sus bases")
        cases = [
            json.loads(r[0])
            for r in db.execute("SELECT payload_json FROM issues WHERE status!='RESOLVED'")
        ]
        version, rows = store_class(config, settings["control_id"]).read()
        if settings.get("last_version", "None") != str(version):
            raise IntegrityError(
                "La lectura de inspecciones no coincide con un snapshot confirmado"
            )
    if origin is not None:
        rows = {k: r for k, r in rows.items() if r["origen_fenix"] == origin}
    groups = model.inspection_groups(rows, cases)
    complete = not cases and all(g["status"] == "RESOLVED" for g in groups)
    return {
        "status": "OK" if complete else "PARTIAL",
        "complete": complete,
        "delta_version": version,
        "groups": groups,
        "resolved_inspection_total": sum(g["INSPECC"] for g in groups if g["status"] == "RESOLVED"),
        "inspection_metric": "inspections_by_context_not_unique_bottles",
        "denominator_source": denominator_source,
    }
