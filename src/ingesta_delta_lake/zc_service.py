"""Ingesta CONT_ZC y diagnóstico de rendimiento en snapshots confirmados, sin score."""

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime

from . import __version__
from . import cont_zc as zc
from .common import (
    ConfigurationError,
    IntegrityError,
    canonical,
    digest,
    horizon_marks,
    live_slot_marks,
    newest_slot,
    parse_int64,
)
from .service import _apply
from .sources import capture_inputs
from .zc_storage import ZcStore


def process(config, control, attempt):
    store = ZcStore(config, control.control_id)
    recovered, recovered_count = [], 0
    for pending in control.pending():
        count = len(control.prepared(pending)["inserts"])
        version, wrote = _apply(config, control, store, pending)
        recovered_count += count if wrote else 0
        recovered.append(
            {
                "operation_id": pending["operation_id"],
                "delta_version": version,
                "confirmed_rows": count,
                "wrote_this_attempt": wrote,
            }
        )
    version, _existing = store.read()
    if control.setting("last_version") not in (None, str(version)):
        raise IntegrityError("Snapshot CONT_ZC distinto de su control")
    snapshots = capture_inputs(config, control, attempt)
    # The newest ten-minute slot is its own commit. October does not share it.
    live = newest_slot(snapshots)
    live_ids = {(item.origin, item.sha256) for item in live}
    # A repeated file is the same observation. It stays with the newest slot
    # so the backlog does not count those rows a second time.
    live = [item for item in snapshots if (item.origin, item.sha256) in live_ids]
    backlog = [item for item in snapshots if (item.origin, item.sha256) not in live_ids]
    phases = [batch for batch in (live, backlog) if batch] or [[]]
    result = None
    inserted = 0
    wrote_any = False
    for index, batch in enumerate(phases):
        result = _commit_batch(
            config,
            control,
            attempt,
            store,
            batch,
            recovered if index == 0 else [],
            recovered_count if index == 0 else 0,
        )
        inserted += result["inserted_this_attempt"]
        wrote_any = wrote_any or result["status"] == "APPLIED"
        if batch is live:
            control.advance_live_until(live_slot_marks(batch))
    result["inserted_this_attempt"] = inserted
    if wrote_any and result["status"] == "NO_CHANGE":
        result["status"] = "APPLIED"
    return result


def _commit_batch(config, control, attempt, store, snapshots, recovered, recovered_count):
    version, existing = store.read()
    parsed = zc.parse(snapshots, config.encoding)
    from .review_adapters import filter_rejected

    filter_rejected(control, parsed)
    inserts, issues, matches = zc.decide(parsed, existing, control.held())
    merged = {**existing, **{zc.key(r): {f: r[f] for f in zc.SCHEMA.names} for r in inserts}}
    batch_id = digest(
        [
            zc.CONTRACT,
            __version__,
            config.plant,
            config.encoding,
            config.timestamp_policy,
            version,
            sorted({(s.origin, s.sha256) for s in snapshots}),
            digest(control.cases()),
        ]
    )
    control.rejected(batch_id, {}, issues, config.plant, create_batch=False)
    cases = control.cases()
    summary = zc.yield_summary(merged.values(), cases)
    complete = not cases
    stats = {
        "rows_read": parsed.rows_read,
        "normalized_rows": len(parsed.entries),
        "invalid_rows": parsed.invalid_rows,
        "consolidated_repetitions": max(
            0, parsed.rows_read - parsed.invalid_rows - len(parsed.entries)
        ),
        "new_entries": len(inserts),
        "matched_entries": matches,
        "conflict_entries": sum(i["reason"] in {"ROW_CONFLICT", "REVIEW_REQUIRED"} for i in issues),
    }
    prepared = {
        "contract": zc.CONTRACT,
        "transformer_version": __version__,
        "timestamp_policy": config.timestamp_policy,
        "snapshots": [s.descriptor() for s in snapshots],
        "before_version": version,
        "before_hash": zc.content_hash(existing),
        "after_hash": zc.content_hash(merged),
        "inserts": inserts,
        "stats": stats,
        "yield_summary": summary,
        "outcome_state": "COMPLETED" if complete else "PARTIAL",
    }
    prior, wrote = control.batch(batch_id), False
    if prior is None:
        control.prepare(batch_id, digest([batch_id, "cont_zc_insert_v1"]), prepared)
        version, wrote = _apply(config, control, store, control.batch(batch_id))
    elif inserts:
        raise IntegrityError("Un lote CONT_ZC confirmado exige nuevas inserciones")
    control.advance_observed_until(horizon_marks(parsed.observed, inserts, wrote=wrote))
    return {
        "attempt_id": attempt,
        "batch_id": batch_id,
        "contract": zc.CONTRACT,
        "plant": config.plant,
        "table_path": str(config.table_path),
        "control_path": str(config.control_path),
        "source_files": len(snapshots),
        "delta_version": version,
        "status": ("PARTIAL" if merged else "BLOCKED")
        if not complete
        else "APPLIED"
        if wrote
        else "NO_CHANGE"
        if snapshots
        else "NO_INPUTS",
        "complete": complete,
        "published_records": len(merged),
        "open_reviews": len(cases),
        "recovered_operations": recovered,
        "inserted_this_attempt": recovered_count + (len(inserts) if wrote else 0),
        "yield_summary": summary,
        **stats,
    }


def _bound(text):
    if text is None:
        return None
    if not isinstance(text, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", text
    ):
        raise ConfigurationError("Límite ZC requiere YYYY-MM-DDTHH:MM:SS local sin zona")
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConfigurationError("Límite ZC inválido") from exc
    return text


def yield_report(config, origin=None, machine=None, start=None, end=None):
    """Ventana de finales de muestra (start,end]; no atribuye producciones ni imputa huecos."""
    config.validate()
    if config.family != "CONT_ZC":
        raise ConfigurationError("zc-yield requiere family=CONT_ZC")
    start, end = _bound(start), _bound(end)
    if start is not None and end is not None and start >= end:
        raise ConfigurationError("La ventana ZC requiere start < end")
    if machine is not None:
        try:
            machine = parse_int64(str(machine))
        except ValueError as exc:
            raise ConfigurationError("Máquina ZC inválida") from exc
    if not config.control_path.exists():
        return {"status": "NO_INPUTS", "source_complete": False, "metric_evaluable": False}
    with closing(sqlite3.connect(config.control_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        binding = canonical(
            {
                "plant": config.plant,
                "silver_root": str(config.silver_root),
                "bronze_root": str(config.bronze_root),
                "contract": zc.CONTRACT,
            }
        )
        if settings.get("binding") != binding:
            raise IntegrityError("Control CONT_ZC de otro ámbito")
        if db.execute(
            "SELECT COUNT(*) FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
        ).fetchone()[0]:
            raise IntegrityError("Recuperar CONT_ZC antes de consultar rendimiento")
        version, rows = ZcStore(config, settings["control_id"]).read()
        if settings.get("last_version", "None") != str(version):
            raise IntegrityError("Snapshot CONT_ZC no confirmado")
        cases = [
            json.loads(r[0])
            for r in db.execute("SELECT payload_json FROM issues WHERE status!='RESOLVED'")
        ]
    selected = [
        r
        for r in rows.values()
        if (origin is None or r["origen_fenix"] == origin)
        and (machine is None or r["MAQ_IS"] == machine)
        and (start is None or start < r["FECHA"])
        and (end is None or r["FECHA"] <= end)
    ]
    summary = zc.yield_summary(selected, cases)
    grouped = defaultdict(list)
    for row in selected:
        grouped[(row["origen_fenix"], row["MAQ_IS"])].append(row)
    return {
        "status": "PARTIAL"
        if cases
        else "NO_OBSERVATIONS"
        if not selected
        else "NOT_EVALUABLE"
        if summary["rate_percent"] is None
        else "OK",
        "source_complete": not cases,
        "metric_evaluable": summary["rate_percent"] is not None,
        "delta_version": version,
        "open_reviews": len(cases),
        "window": {"start_exclusive": start, "end_inclusive": end},
        "coverage_scope": "observed_samples_only_not_guaranteed_continuous",
        "metric_scope": "machine_period_not_individual_production",
        "summary": summary,
        "per_machine": [
            {"origen_fenix": o, "MAQ_IS": m, **zc.yield_summary(items, cases)}
            for (o, m), items in sorted(grouped.items())
        ],
    }
