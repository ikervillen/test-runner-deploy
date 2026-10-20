"""Protocolo de observaciones MIA; contratos/adaptadores separados, sin cambiar el score."""

import json
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import replace

from . import __version__
from . import lista_defectos as defects
from .common import ConfigurationError, IntegrityError, canonical, digest
from .defects_storage import DefectsStore
from .service import _apply
from .sources import capture, discover, orphan_captures

BATCH_FILES = 8
BATCH_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
POLICY = "bounded-input-v1"


def chunks(snapshots):
    """Bound parsed input; a single file remains subject to the capture size limit."""
    batch, size = [], 0
    for snapshot in snapshots:
        if batch and (len(batch) >= BATCH_FILES or size + snapshot.size > BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(snapshot)
        size += snapshot.size
    if batch:
        yield batch


def process(
    config,
    control,
    attempt,
    *,
    model=defects,
    store_class=DefectsStore,
    operation_kind="lista_defectos_insert_v2",
    stop_requested=None,
):
    captured = control.captured_inputs()
    snapshots = []
    for origin, path, stamp in discover(config):
        snapshot = capture(
            replace(config, max_file_bytes=min(config.max_file_bytes, MAX_FILE_BYTES)),
            origin,
            path,
            stamp,
            known=captured,
        )
        # Durable as soon as captured: a later capture error cannot lose this input.
        control.receipts(attempt, [snapshot])
        snapshots.append(snapshot)
        captured.setdefault((snapshot.origin, snapshot.sha256), snapshot)
    for snapshot in orphan_captures(config, captured):
        control.receipts(attempt, [snapshot])
        captured[(snapshot.origin, snapshot.sha256)] = snapshot
    consumed = control.consumed_inputs()
    pending = [s for key, s in captured.items() if key not in consumed]
    if any(s.size > MAX_FILE_BYTES for s in pending):
        raise IntegrityError("Captured MIA file exceeds 8 MiB; split-file support is required")
    # Export timestamps order versions, never determine production or sample time.
    pending.sort(
        key=lambda s: (s.producer_time or "", s.mtime_ns, s.origin, s.source_path, s.sha256)
    )
    result, totals, completed, batch_count = None, {}, 0, 0
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
    wrote = False
    stopped = False
    recovered = []
    # An empty batch still reconciles old uncertain commits and reports current coverage.
    for batch in chunks(pending) if pending else [[]]:
        if result is not None and stop_requested is not None and stop_requested():
            stopped = True
            break
        started = time.monotonic()
        result = process_batch(
            config,
            control,
            attempt,
            snapshots=batch,
            model=model,
            store_class=store_class,
            operation_kind=operation_kind,
        )
        control.acknowledge_inputs(result["batch_id"], batch)
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
    status = result["status"]
    if result["complete"]:
        status = "APPLIED" if wrote else "NO_CHANGE" if snapshots else "NO_INPUTS"
    if stopped:
        status = "STOPPED_AFTER_BATCH"
    return {
        **result,
        **totals,
        "status": status,
        "complete": result["complete"] and not stopped,
        "source_files": len(snapshots),
        "processed_versions": completed,
        "remaining_versions": len(pending) - completed,
        "completed_batches": batch_count,
        "recovered_operations": recovered,
        "stats_scope": "sum_of_processed_batches",
    }


def process_batch(
    config,
    control,
    attempt,
    *,
    snapshots,
    model=defects,
    store_class=DefectsStore,
    operation_kind="lista_defectos_insert_v2",
):
    store = store_class(config, control.control_id)
    recovered, recovery_count = [], 0
    for pending in control.pending():
        version, wrote = _apply(config, control, store, pending)
        count = len(control.prepared(pending)["inserts"])
        recovery_count += count if wrote else 0
        recovered.append(
            {
                "operation_id": pending["operation_id"],
                "delta_version": version,
                "confirmed_rows": count,
                "wrote_this_attempt": wrote,
            }
        )
    version, existing = store.read()
    if control.setting("last_version") not in (None, str(version)):
        raise IntegrityError(f"El snapshot {config.family} no coincide con su control")
    parsed = model.parse(snapshots, config.encoding)
    inserts, issues, matches = model.decide(parsed, existing, *control.held())
    merged = {
        **existing,
        **{model.key(r): {f: r[f] for f in model.SCHEMA.names} for r in inserts},
    }
    batch_id = digest(
        [
            model.CONTRACT,
            POLICY,
            control.setting("requeue_generation"),
            __version__,
            config.plant,
            config.encoding,
            config.timestamp_policy,
            version,
            sorted({(s.origin, s.sha256) for s in snapshots}),
            digest(control.cases()),
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
    groups = model.inspection_groups(merged, cases)
    complete = not cases and all(g["status"] == "RESOLVED" for g in groups)
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
        "inspection_groups": groups,
    }
    prior = control.batch(batch_id)
    wrote = False
    if prior is None:
        control.prepare(batch_id, digest([batch_id, operation_kind]), prepared)
        version, wrote = _apply(config, control, store, control.batch(batch_id))
    elif inserts:
        raise IntegrityError(f"Un lote {config.family} confirmado exige nuevas inserciones")
    status = (
        ("PARTIAL" if merged else "BLOCKED")
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
        "published_rows": len(merged),
        "open_reviews": len(cases),
        "resolved_groups": sum(g["status"] == "RESOLVED" for g in groups),
        "unresolved_groups": sum(g["status"] != "RESOLVED" for g in groups),
        "resolved_inspection_total": sum(g["INSPECC"] for g in groups if g["status"] == "RESOLVED"),
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
