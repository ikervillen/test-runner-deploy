"""Delta compaction and VACUUM as Gold operations, so the publisher stays the only author.

Every commit in a Gold table must belong to a publication, or the next publication
refuses to write. Maintenance commits carry their own operation id and the content
hash of the version they rewrite; a maintenance publication then pins the new versions,
whose rows are identical. VACUUM deletes a file only when neither the current
publication nor any publication younger than the retention window pins it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import portalocker
from deltalake import CommitProperties, DeltaTable

from .common import IntegrityError, atomic_json, digest, read_json, utc_now, within
from .publications import Snapshot, envelope, promote, reference

SCHEMA = "gold-maintenance-v1"
MAX_KEEP_DAYS = 3650


def maintain(
    root: Path,
    *,
    keep_days: int,
    now: datetime | None = None,
    failpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if type(keep_days) is not int or not 1 <= keep_days <= MAX_KEEP_DAYS:
        raise IntegrityError(f"Publication retention must be 1-{MAX_KEEP_DAYS} days")
    root = Path(root).resolve(strict=True)
    with (
        portalocker.Lock(root / "pipeline.lock", timeout=0),
        portalocker.Lock(root / "writer.lock", timeout=0),
    ):
        identity = root.parent / "checkpoint" / "identity.json"
        if identity.is_file() and read_json(identity).get("lane") == "live":
            raise IntegrityError("Gold Live Delta versions are retained by the cumulative archive")
        result = _maintain(root, keep_days, now or datetime.now(UTC), failpoint or (lambda _: None))
        atomic_json(root / "maintenance-status.json", {**result, "last_success_at": utc_now()})
    return result


def _maintain(root, keep_days, now, failpoint):
    pointer, manifest = envelope(root)
    path, journal = _journal(root, manifest["publication_id"], keep_days, now)
    operation = journal["operation_id"]
    if manifest["publication_id"] == operation:
        return _acknowledge(root, path, journal, "MAINTAINED", {})
    if manifest["publication_id"] != journal["base_publication_id"]:
        raise IntegrityError("Pending maintenance belongs to another publication")
    Snapshot.open(root, require_gold_contract=True)
    horizon = now - timedelta(days=journal["keep_days"])
    retained = _retained_versions(root, horizon, manifest["publication_id"])
    refs, tables = {}, {}
    for ref in manifest["tables"]:
        name = ref["name"]
        refs[name], tables[name] = _maintain_table(root, ref, operation, retained.get(name, set()))
        failpoint(f"table:{name}")
    if all(refs[r["name"]] == r for r in manifest["tables"]):
        return _acknowledge(root, path, journal, "UNCHANGED", tables)
    promote(
        root,
        list(refs.values()),
        environment=manifest["environment"],
        plant=manifest["plant"],
        input_id=manifest["input_publication_id"],
        operation_id=operation,
        generation=pointer["generation"] + 1,
        previous=pointer["publication_id"],
        coverage=manifest["coverage"],
        dependencies=[
            {
                "table": d["table"],
                "input_versions": {p: refs[p]["delta_version"] for p in d["input_versions"]},
            }
            for d in manifest["dependencies"]
        ],
        contract=manifest["contract"],
    )
    Snapshot.open(root, require_gold_contract=True)
    return _acknowledge(root, path, journal, "MAINTAINED", tables)


def _journal(root, base, keep_days, now):
    pending = [
        (path, state)
        for path in sorted((root / "operations").glob("*.json"))
        if (state := read_json(path))["state"] != "ACKNOWLEDGED"
    ]
    if len(pending) == 1 and pending[0][1].get("schema_id") == SCHEMA:
        return pending[0]
    if pending:
        raise IntegrityError("Recover the pending publication before maintenance")
    operation = digest([SCHEMA, base, keep_days, now.isoformat()])
    state = {
        "schema_id": SCHEMA,
        "operation_id": operation,
        "base_publication_id": base,
        "keep_days": keep_days,
        "state": "PREPARED",
    }
    path = root / "operations" / f"{operation}.json"
    atomic_json(path, state)
    return path, state


def _acknowledge(root, path, journal, status, tables):
    journal["state"] = "ACKNOWLEDGED"
    atomic_json(path, journal)
    return {
        "status": status,
        "publication_id": envelope(root)[1]["publication_id"],
        "tables": tables,
    }


def _retained_versions(root: Path, horizon: datetime, current: str) -> dict[str, set[int]]:
    keep: dict[str, set[int]] = {}
    for path in (root / "publications").glob("*.json"):
        _, manifest = envelope(root, path.stem)
        if path.stem != current and datetime.fromisoformat(manifest["created_at"]) < horizon:
            continue
        for ref in manifest["tables"]:
            keep.setdefault(ref["name"], set()).add(ref["delta_version"])
    return keep


def _maintain_table(root, ref, operation, keep_versions):
    path = within(root, ref["uri"])
    table = DeltaTable(str(path))
    if (
        table.version() != ref["delta_version"]
        and table.history(1)[0].get("gold_operation_id") != operation
    ):
        raise IntegrityError(f"{ref['name']}: table has commits outside any publication")
    metadata = {"gold_operation_id": operation}
    content = _commit(path, ref["delta_version"]).get("gold_content_hash")
    if content is not None:
        metadata["gold_content_hash"] = content
    properties = CommitProperties(custom_metadata=metadata)
    before = len(table.file_uris())
    compacted = table.optimize.compact(commit_properties=properties)
    vacuumed = DeltaTable(str(path)).vacuum(
        retention_hours=0,
        dry_run=False,
        enforce_retention_duration=False,
        keep_versions=sorted(keep_versions),
        commit_properties=properties,
    )
    table = DeltaTable(str(path))
    stats = {
        "files_before": before,
        "files_after": len(table.file_uris()),
        "files_compacted": compacted["numFilesRemoved"],
        "files_vacuumed": len(vacuumed),
    }
    if table.version() == ref["delta_version"]:
        return ref, stats
    return reference(root, ref["name"], ref["uri"], table.version(), operation), stats


def _commit(path, version):
    log = path / "_delta_log" / f"{version:020d}.json"
    with log.open(encoding="utf-8") as stream:
        for line in stream:
            if "commitInfo" in (entry := json.loads(line)):
                return entry["commitInfo"]
    return {}
