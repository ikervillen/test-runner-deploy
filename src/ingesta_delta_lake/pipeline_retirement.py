"""Only the live owner may retire rows, after exact immutable historical copy proof."""

import hashlib
import json
import os
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import portalocker
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from deltalake import CommitProperties

from . import __version__
from .association_storage import STORES, AssociationStore
from .commit_protocol import application_transaction
from .common import IntegrityError, canonical, digest, now
from .contracts import CONTRACTS
from .pipeline_publications import SilverSnapshot
from .pipeline_receipts import ArchiveReceipts
from .pipeline_retired import register, synchronize
from .pipeline_rows import business_key, payload_hash
from .pipeline_view import ArchiveSnapshot
from .runtime import atomic_json


def journal_path(config):
    return config.control_root / "retirement" / (config.family + ".json")


def store_for(config, control):
    cls = (
        AssociationStore
        if config.family == "ASIGNACION_PRODUCCION_RAMAL"
        else STORES[config.family]
    )
    return cls(config, control.control_id)


def expression(rows):
    selected = ds.scalar(False)
    for row in rows:
        same = ds.scalar(True)
        for field, value in row.items():
            same &= ds.field(field).is_null() if value is None else ds.field(field) == value
        selected |= same
    return selected


def recover(config, control):
    path = journal_path(config)
    if not path.exists():
        return None
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan["control_id"] != control.control_id or plan["family"] != config.family:
        raise IntegrityError("Retirement journal belongs to another owner")
    return _execute(config, control, plan)


def _execute(config, control, plan):
    cfg = replace(
        config,
        archive_root=Path(plan["archive_context"]["root"]),
        archive_manifest=Path(plan["archive_context"]["manifest"]),
        pipeline_lane="live",
    )
    artifact = journal_path(config).parent / (plan["operation_id"] + ".parquet")
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != plan["artifact_hash"]:
        raise IntegrityError("Retirement intention artifact changed")
    data = pq.read_table(artifact)
    if data.schema != CONTRACTS[config.family].schema:
        raise IntegrityError("Retirement intention differs from its business schema")
    rows = data.to_pylist()
    store = store_for(cfg, control)
    table = store.table()
    operation = plan["operation_id"]
    fields = config.contract.schema.names
    commits = [h for h in table.history() if h.get("ingesta_operation_id") == operation]
    if not commits:
        if table.version() != plan["before_version"] or control.setting("last_version") != str(
            table.version()
        ):
            raise IntegrityError("Retirement destination changed before its prepared operation")
        predicate = " AND ".join(
            f"(t.`{f}` = s.`{f}` OR (t.`{f}` IS NULL AND s.`{f}` IS NULL))" for f in fields
        )
        metadata = {
            "ingesta_operation_id": operation,
            "ingesta_control_id": control.control_id,
            "ingesta_contract": config.contract.name,
            "ingesta_plant": config.plant,
            "ingesta_release": __version__,
            "ingesta_maintenance": "archive-retirement",
        }
        table.merge(
            data,
            predicate,
            source_alias="s",
            target_alias="t",
            commit_properties=CommitProperties(
                custom_metadata=metadata,
                app_transactions=application_transaction(
                    control.control_id, plan["before_version"]
                ),
                max_commit_retries=0,
            ),
        ).when_matched_delete().execute()
        table = store.table()
        commits = [h for h in table.history() if h.get("ingesta_operation_id") == operation]
    expected = plan["before_version"] + 1
    if len(commits) != 1 or commits[0]["version"] != expected or table.version() != expected:
        raise IntegrityError("Retirement commit cannot be reconciled")
    events = (
        pa.RecordBatchReader.from_stream(table.load_cdf(expected, expected)).read_all().to_pylist()
    )
    deleted = [
        {field: event[field] for field in fields}
        for event in events
        if event["_change_type"] == "delete"
    ]
    if len(events) != len(rows) or sorted(map(payload_hash, deleted)) != sorted(
        map(payload_hash, rows)
    ):
        raise IntegrityError("Retirement commit differs from its exact copy proof")
    # Preserve MIA's logical index; only the live owner advances its version.
    with control.db:
        register(control, rows, expected)
        control.db.execute(
            "INSERT OR IGNORE INTO batches VALUES(?,?,'COMPLETED',?,?,?,?)",
            ("retirement:" + operation, operation, canonical(plan), now(), now(), expected),
        )
        control.db.execute(
            "INSERT INTO settings VALUES('last_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(expected),),
        )
        control.db.execute(
            "UPDATE settings SET value=? WHERE key='inspection_index_version'", (str(expected),)
        )
    journal_path(config).unlink()
    artifact.unlink(missing_ok=True)
    return {
        "status": "RETIRED",
        "delta_version": expected,
        "retired_rows": len(rows),
        "retirement_receipt": plan["receipt"],
    }


def retire(config, live_storage, historical, head, *, policy, technical_now):
    """One bounded idle turn. Never called by a historical writer."""
    if config.pipeline_lane != "live" or not config.archive_context:
        raise IntegrityError("Retirement requires the live owner's pinned archive context")
    from .service import control_type

    lock = config.silver_root / f".writer-{config.plant}-{config.family}.lock"
    with (
        portalocker.Lock(str(lock), mode="a", timeout=0),
        closing(control_type(config)(config)) as control,
    ):
        recovered = recover(config, control)
        if recovered:
            return recovered
        if control.pending():
            return {"status": "RETIREMENT_WAITING_RECOVERY"}
        reader = ArchiveReceipts(historical)
        receipt = reader.read(head, max_rows=policy.archive_batch_rows)
        archive = ArchiveSnapshot(historical, manifest_path=config.archive_manifest)
        if receipt["owner"] != archive.manifest["owner"] or receipt["family"] != config.family:
            raise IntegrityError("Retirement receipt belongs to a different owner or family")
        ref = archive.manifest["tables"].get(config.family, {})
        if (
            ref.get("archive_table_id") != receipt["archive_table_id"]
            or ref.get("archive_version", -1) < receipt["archive_version"]
        ):
            raise IntegrityError("Retirement copy is not in its pinned archive snapshot")
        published = SilverSnapshot.open(live_storage, publication_id=receipt["source_publication"])
        source = published.manifest["tables"][config.family]
        store = store_for(config, control)
        table = store.table()
        if (
            table is None
            or source["table_id"] != table.metadata().id
            or source["control_id"] != control.control_id
        ):
            raise IntegrityError("Retirement source differs from the live owner")
        if control.setting("last_version") != str(table.version()):
            raise IntegrityError("Retirement requires a confirmed live table")
        synchronize(control, table)
        candidates = []
        for value in reader.rows(receipt):
            stamp = datetime.fromisoformat(value["__archive_confirmed_at"])
            if stamp.tzinfo is None or technical_now.tzinfo is None:
                raise IntegrityError("Retirement requires aware publication clocks")
            if (technical_now - stamp).total_seconds() < policy.retention_seconds:
                return {"status": "RETIREMENT_NOT_DUE"}
            if value["__archive_source_table"] == table.metadata().id and value[
                "__archive_change_type"
            ] in {"insert", "update_postimage"}:
                candidates.append(value)
        if candidates:
            earliest = min(value["__archive_source_version"] for value in candidates)
            revisions, scanned = set(), 0
            maintenance = {
                h["version"]
                for h in table.history()
                if h.get("ingesta_maintenance") == "archive-retirement"
            }
            if earliest < table.version():
                for batch in pa.RecordBatchReader.from_stream(
                    table.load_cdf(earliest + 1, table.version())
                ):
                    scanned += batch.num_rows
                    if scanned > policy.read_max_rows:
                        raise IntegrityError(
                            "Retirement revision check exceeds its explicit row budget"
                        )
                    for event in batch.to_pylist():
                        if event["_commit_version"] not in maintenance:
                            row = {field: event[field] for field in config.contract.schema.names}
                            revisions.add(
                                (business_key(config.family, row), event["_commit_version"])
                            )
            candidates = [
                value
                for value in candidates
                if not any(
                    key == value["__archive_key"] and version > value["__archive_source_version"]
                    for key, version in revisions
                )
            ]
        rows = [
            {field: value[field] for field in config.contract.schema.names} for value in candidates
        ]
        if rows:
            rows = table.to_pyarrow_dataset().to_table(filter=expression(rows)).to_pylist()
        if not rows:
            return {"status": "RETIREMENT_UNCHANGED", "retirement_receipt": head}
        operation = digest(
            [
                "retirement-v1",
                control.control_id,
                head,
                table.version(),
                sorted(map(payload_hash, rows)),
            ]
        )
        path = journal_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        artifact = path.parent / (operation + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=config.contract.schema), artifact)
        with artifact.open("r+b") as stream:
            os.fsync(stream.fileno())
        plan = {
            "operation_id": operation,
            "family": config.family,
            "control_id": control.control_id,
            "before_version": table.version(),
            "archive_context": config.archive_context,
            "receipt": head,
            "artifact_hash": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
        atomic_json(path, plan)
        return _execute(config, control, plan)
