"""Ingesta explícita de catálogos físicos; dependencias y recuperación duraderas."""

import hashlib
from pathlib import Path

from . import __version__
from . import topology as t
from .common import IntegrityError, digest
from .service import _apply
from .sources import capture_inputs
from .topology_storage import TopologyStore, snapshot


def process(config, control, attempt):
    family = config.family
    store = TopologyStore(config, control.control_id)
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
    version, old = store.read()
    if control.setting("last_version") not in (None, str(version)):
        raise IntegrityError("Topología y control no coinciden")
    captures = capture_inputs(config, control, attempt)
    parsed = t.parse(family, captures, config.encoding)
    descriptors = [s.descriptor() for s in captures]
    for case in control.cases():
        if case["reason"] != "MACHINE_PENDING":
            continue
        p = case["payload"]
        for ref in p["evidence"]:
            path = Path(ref["bronze_path"]).resolve()
            if (
                not path.is_relative_to(config.bronze_root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]
            ):
                raise IntegrityError("Dependencia de topología alterada")
            descriptors.append(
                {k: ref[k] for k in ("bronze_path", "sha256", "origin", "source_path")}
            )
        for row in p["candidates"]:
            parsed.entries.setdefault(t.row_hash(family, row), (row, p["evidence"]))
    machines, manifest, machine_cases = {}, {}, []
    if family == t.BRANCHES:
        machines, m, machine_cases = snapshot(config, t.MACHINES)
        manifest[t.MACHINES] = m
    cases = [c for c in control.cases() if c["reason"] != "MACHINE_PENDING"]
    inserts, updates, issues = t.decide(
        family, parsed, old, cases, machines, machine_cases, dict(config.line_code_by_origin)
    )
    merged = {
        **old,
        **{t.key(family, r): {f: r[f] for f in t.FIELDS[family]} for r in [*inserts, *updates]},
    }
    batch_id = digest(
        [
            config.contract.name,
            __version__,
            version,
            manifest,
            sorted({(s["origin"], s["sha256"]) for s in descriptors}),
            digest(control.cases()),
            t.content_hash(family, merged),
            issues,
        ]
    )
    prepared = {
        "contract": config.contract.name,
        "transformer_version": __version__,
        "snapshots": descriptors,
        "source_manifest": manifest,
        "before_version": version,
        "before_hash": t.content_hash(family, old),
        "after_hash": t.content_hash(family, merged),
        "inserts": inserts,
        "updates": updates,
    }
    control.rejected(batch_id, prepared, issues, config.plant, create_batch=False)
    prior = control.batch(batch_id)
    wrote = False
    if prior is None:
        control.prepare(batch_id, digest([batch_id, "topology_v1"]), prepared)
        version, wrote = _apply(config, control, store, control.batch(batch_id))
    elif inserts or updates:
        raise IntegrityError("Topología confirmada exige escrituras nuevas")
    control.resolve_dependencies(merged, batch_id)
    complete = not control.cases()
    return {
        "status": ("APPLIED" if wrote else "NO_CHANGE" if captures else "NO_INPUTS")
        if complete
        else "PARTIAL"
        if merged
        else "WAITING_DEPENDENCIES"
        if all(c["reason"] == "MACHINE_PENDING" for c in control.cases())
        else "BLOCKED",
        "contract": config.contract.name,
        "complete": complete,
        "attempt_id": attempt,
        "batch_id": batch_id,
        "delta_version": version,
        "published_records": len(merged),
        "source_files": len(captures),
        "rows_read": parsed.count,
        "open_reviews": len(control.cases()),
        "source_manifest": manifest,
        "inserted_this_attempt": len(inserts) if wrote else 0,
        "updated_this_attempt": len(updates) if wrote else 0,
        "recovered_operations": recovered,
    }
