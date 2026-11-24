"""Pinned, verified Delta inputs and recoverable multi-table Gold publication.

Only this producer writes metadata. Readers never connect to active ingestion SQLite.
Local atomic replacement and an OS-backed writer lock are prerequisites, not assumed
cross-volume or object-store transactions.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import portalocker
import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import CommitProperties, DeltaTable, write_deltalake
from deltalake.transaction import Transaction

from .common import (
    IntegrityError,
    atomic_json,
    canonical,
    digest,
    immutable_json,
    read_json,
    utc_now,
    within,
)
from .schemas import KEYS, SCHEMAS, check_row, make_table, partition_columns, schema_id
from .write_telemetry import (
    allocated_bytes,
    commit_metrics,
    legacy_scope_metrics,
    publication_metrics,
)

MANIFEST_SCHEMA = "quality-publication-v1"
POINTER_SCHEMA = "quality-pointer-v1"
RETENTION = {
    "delta.enableExpiredLogCleanup": "false",
    "delta.logRetentionDuration": "interval 36500 days",
    "delta.deletedFileRetentionDuration": "interval 36500 days",
}


def envelope(root: Path, publication_id: str | None = None) -> tuple[dict, dict]:
    pointer = read_json(root / "current.json")
    if pointer.get("schema_id") != POINTER_SCHEMA:
        raise IntegrityError("Unsupported publication pointer")
    relative = (
        pointer["manifest_uri"] if publication_id is None else f"publications/{publication_id}.json"
    )
    manifest = read_json(within(root, relative))
    stored = manifest.pop("manifest_hash", None)
    if manifest.get("schema_id") != MANIFEST_SCHEMA or digest(manifest) != stored:
        raise IntegrityError("Manifest hash/schema verification failed")
    manifest["manifest_hash"] = stored
    if publication_id is None and (
        stored != pointer["manifest_hash"]
        or manifest["publication_id"] != pointer["publication_id"]
    ):
        raise IntegrityError("Pointer and immutable manifest differ")
    return pointer, manifest


@dataclass(frozen=True)
class Snapshot:
    root: Path
    manifest: dict
    _verified_tables: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def open(
        cls, root: Path, *, publication_id: str | None = None, require_gold_contract: bool = False
    ) -> Snapshot:
        root = root.resolve(strict=True)
        _, manifest = envelope(root, publication_id)
        snapshot = cls(root, manifest)
        if require_gold_contract:
            from .contracts import CONTRACT_VERSION, CONTRACTS, contract_id

            if manifest.get("contract") != {"version": CONTRACT_VERSION, "id": contract_id()}:
                raise IntegrityError("Gold contract version/fingerprint is incompatible")
            if {r["name"] for r in manifest["tables"]} != set(CONTRACTS):
                raise IntegrityError("Gold contract requires every declared table")
        if len({r["name"] for r in manifest["tables"]}) != len(manifest["tables"]):
            raise IntegrityError("Duplicate table name in publication")
        versions = {r["name"]: r["delta_version"] for r in manifest["tables"]}
        for dependency in manifest.get("dependencies", []):
            if any(versions.get(k) != v for k, v in dependency["input_versions"].items()):
                raise IntegrityError("Derived dependencies belong to incompatible source versions")
        for ref in manifest["tables"]:
            snapshot.table(ref["name"])
        return snapshot

    @property
    def publication_id(self) -> str:
        return self.manifest["publication_id"]

    def ref(self, name: str) -> dict:
        result = next((r for r in self.manifest["tables"] if r["name"] == name), None)
        if result is None:
            raise IntegrityError(f"Missing confirmed source: {name}")
        return result

    def table(self, name: str) -> DeltaTable:
        if name in self._verified_tables:
            return self._verified_tables[name]
        ref = self.ref(name)
        table = DeltaTable(str(within(self.root, ref["uri"])), version=ref["delta_version"])
        if table.metadata().id != ref["table_id"]:
            raise IntegrityError(f"{name}: Delta table identity changed")
        if schema_id(pa.schema(table.schema().to_arrow())) != ref["schema_id"]:
            raise IntegrityError(f"{name}: Delta schema differs from publication")
        commit = table.history(1)[0]
        # history() may include later commits even on a version-pinned object.
        log = within(self.root, ref["uri"]) / "_delta_log" / f"{ref['delta_version']:020d}.json"
        if log.exists():
            with log.open(encoding="utf-8") as stream:
                commit = next(
                    (
                        obj["commitInfo"]
                        for line in stream
                        if "commitInfo" in (obj := json.loads(line))
                    ),
                    {},
                )
        elif commit.get("version") != ref["delta_version"]:
            raise IntegrityError(f"{name}: confirming commit metadata is unavailable")
        if commit.get(ref["operation_field"]) != ref["operation_id"]:
            raise IntegrityError(f"{name}: commit is not the confirmed operation")
        receipt = read_json(within(self.root, ref["receipt_reference"]))
        if (receipt.get("operation_id"), receipt.get("table_id"), receipt.get("delta_version")) != (
            ref["operation_id"],
            ref["table_id"],
            ref["delta_version"],
        ):
            raise IntegrityError(f"{name}: receipt does not confirm the committed version")
        for uri in table.file_uris():
            path = Path(uri).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise IntegrityError(f"{name}: referenced Delta data is missing/outside store")
        self._verified_tables[name] = table
        return table

    def batches(
        self,
        name: str,
        *,
        columns: list[str] | None = None,
        expression: ds.Expression | None = None,
        batch_size: int = 8192,
        max_rows: int = 250_000,
        file_pruning_predicate=None,
        on_scan=None,
        on_decode=None,
        pushdown: bool = False,
    ) -> Iterator[pa.RecordBatch]:
        count = 0
        # The predicate prunes by partition value and by file statistics before decoding.
        # Timestamp pushdown skips the rest of a table. String filters stay after the
        # read: Arrow 23 has no kernel for string_view comparisons written by Delta MERGE.
        scanner = (
            self.table(name)
            .to_pyarrow_dataset(file_pruning_predicate=file_pruning_predicate)
            .scanner(
                columns=columns if expression is None else None,
                filter=expression if pushdown else None,
                batch_size=batch_size,
                # Single-threaded on purpose: batch order feeds float sums and input hashes,
                # and parity with the frozen legacy reference depends on that order.
                use_threads=False,
            )
        )
        for batch in scanner.to_batches():
            if on_decode is not None:
                on_decode(batch.num_rows)
            # Arrow 23 cannot prune every statistics kind written by Delta MERGE.
            if expression is not None:
                batch = batch.filter(expression)
                if columns is not None:
                    batch = batch.select(columns)
            if batch.num_rows == 0:
                continue
            count += batch.num_rows
            if on_scan is not None:
                on_scan(batch.num_rows)
            if count > max_rows:
                raise IntegrityError(f"{name}: per-scope row budget exceeded; split the scope")
            yield batch

    def rows(self, name: str, **kwargs) -> list[dict]:
        return [r for batch in self.batches(name, **kwargs) for r in batch.to_pylist()]


def reference(
    root: Path,
    name: str,
    relative: str,
    version: int,
    operation_id: str,
    *,
    operation_field: str = "gold_operation_id",
) -> dict:
    """Producer-side receipt, only after its accepted commit has been confirmed.

    Ingestion must call this at its confirmation boundary; a consumer cannot infer
    accepted business data merely by finding the highest Delta version.
    """
    table = DeltaTable(str(within(root, relative)), version=version)
    receipt = {
        "operation_id": operation_id,
        "table_id": table.metadata().id,
        "delta_version": version,
    }
    uri = f"receipts/{digest([name, receipt])}.json"
    immutable_json(within(root, uri), receipt)
    return {
        "name": name,
        "table_id": table.metadata().id,
        "uri": relative,
        "delta_version": version,
        "schema_id": schema_id(pa.schema(table.schema().to_arrow())),
        "operation_id": operation_id,
        "operation_field": operation_field,
        "receipt_reference": uri,
    }


def promote_cas(root: Path, our_refs: list[dict], *, lock_timeout: float = 60, **kwargs) -> dict:
    """Point the current publication at our commits without hiding the other lane.

    The pointer is the registry. It is swapped only while this process holds it,
    for the write itself. If another lane committed a later version of a table we
    also wrote, the caller repeats the merge. The score is not rebuilt.
    """
    our = {ref["name"]: ref for ref in our_refs}
    declared_dependencies = kwargs.pop("dependencies", [])
    deadline = time.monotonic() + lock_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise portalocker.exceptions.LockException("The publication pointer stayed busy")
        with portalocker.Lock(root / "writer.lock", timeout=min(5, remaining)):
            kwargs.pop("generation", None)
            kwargs.pop("previous", None)
            if not (root / "current.json").exists():
                return promote(
                    root,
                    list(our.values()),
                    generation=1,
                    previous=None,
                    dependencies=declared_dependencies,
                    **kwargs,
                )
            pointer, manifest = envelope(root)
            latest = {ref["name"]: ref for ref in manifest["tables"]}
            merged = dict(latest)
            for name, ref in our.items():
                target = root / "tables" / name
                if (target / "_delta_log").exists() and DeltaTable(str(target)).version() != ref[
                    "delta_version"
                ]:
                    raise CommitConflict(name)
                merged[name] = ref
            from .contracts import CONTRACTS

            dependencies = [
                {
                    "table": name,
                    "input_versions": {
                        parent: merged[parent]["delta_version"]
                        for parent in CONTRACTS[name].parents
                        if parent in merged
                    },
                }
                for name in sorted(merged)
            ]
            return promote(
                root,
                list(merged.values()),
                generation=pointer["generation"] + 1,
                previous=pointer["publication_id"],
                dependencies=dependencies + [d for d in declared_dependencies if d.get("kind")],
                **kwargs,
            )


class CommitConflict(IntegrityError):
    """Our hour landed, then another commit moved that table. Repeat the merge."""


def promote(
    root: Path,
    refs: list[dict],
    *,
    environment: str,
    plant: str,
    input_id: str | None,
    operation_id: str,
    generation: int,
    previous: str | None,
    coverage: list[dict],
    dependencies: list[dict] | None = None,
    source_mode: str = "confirmed",
    contract: dict | None = None,
) -> dict:
    manifest: dict[str, Any] = {
        "schema_id": MANIFEST_SCHEMA,
        "publication_id": operation_id,
        "environment": environment,
        "plant": plant,
        "created_at": utc_now(),
        "previous_publication_id": previous,
        "input_publication_id": input_id,
        "tables": sorted(refs, key=lambda r: r["name"]),
        "coverage": coverage,
        "dependencies": dependencies or [],
        "source_mode": source_mode,
    }
    if contract is not None:
        manifest["contract"] = contract
    path = root / "publications" / f"{operation_id}.json"
    if path.exists():
        existing = read_json(path)
        for key in ("tables", "input_publication_id", "coverage", "dependencies", "contract"):
            if existing.get(key) != manifest.get(key):
                raise IntegrityError("Same publication ID has different contents")
        manifest = existing
    else:
        manifest["manifest_hash"] = digest(manifest)
        immutable_json(path, manifest)
    pointer = {
        "schema_id": POINTER_SCHEMA,
        "generation": generation,
        "publication_id": operation_id,
        "manifest_uri": f"publications/{operation_id}.json",
        "manifest_hash": manifest["manifest_hash"],
    }
    atomic_json(root / "current.json", pointer)
    return manifest


def commit_scope(
    replace_ids: set[str] | None,
    hours: tuple[tuple[str, datetime], ...] | None,
) -> dict:
    """Identity of a commit. A full publication is the empty scope."""
    scope: dict = {}
    if replace_ids:
        scope["productions"] = sorted(replace_ids)
    if hours:
        scope["hours"] = [
            f"{production_id}|{start.isoformat(timespec='seconds')}"
            for production_id, start in sorted(hours)
        ]
    return scope


# Facts are not keyed by the clock hour. Their own time column limits the delete
# to the hour being rewritten, so a previous hour stays stored.
_FACT_TIME = {
    "mia_rejections": "periodo_inicio",
    "mia_inspections": "periodo_inicio",
    "inspection_bases": "periodo_inicio",
    "ar_intervals": "fecha_on",
}


def _sql_in(column: str, values: list[str]) -> str:
    if any("'" in value or "\\" in value for value in values):
        raise IntegrityError("Identifier cannot be used as a delete predicate")
    quoted = ", ".join(f"'{value}'" for value in values)
    return f't."{column}" IN ({quoted})'


def chunk_delete_predicate(
    name: str,
    replace_ids: set[str] | None,
    hours: tuple[tuple[str, datetime], ...] | None = None,
    result_ids: tuple[str, ...] | None = None,
    bounds: tuple[tuple[str, datetime, datetime], ...] | None = None,
) -> str | None:
    """How a chunk removes its own stale rows and leaves every other hour stored.

    An empty string deletes every unmatched row, which is the full publication.
    None keeps unmatched rows: a fact table has no hour columns, so deleting by
    production would remove hours this commit did not recalculate.
    """
    columns = set(SCHEMAS[name].names)
    if bounds and name in _FACT_TIME and "id_produccion" in columns:
        column = _FACT_TIME[name]
        parts = []
        for production_id, left, right in bounds:
            opened = left.strftime("%Y-%m-%d %H:%M:%S")
            closed = right.strftime("%Y-%m-%d %H:%M:%S")
            parts.append(
                f"({_sql_in('id_produccion', [production_id])} "
                f"AND t.\"{column}\" >= timestamp '{opened}' "
                f"AND t.\"{column}\" < timestamp '{closed}')"
            )
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
    if bounds is not None and not hours:
        return None
    if hours and {"id_produccion", "inicio", "tipo_ventana"} <= columns:
        grouped: dict[datetime, list[str]] = {}
        for production_id, start in hours:
            grouped.setdefault(start, []).append(production_id)
        parts = []
        for start, identities in sorted(grouped.items()):
            stamp = start.strftime("%Y-%m-%d %H:%M:%S")
            parts.append(
                f"({_sql_in('id_produccion', sorted(set(identities)))} "
                f"AND t.\"inicio\" = timestamp '{stamp}')"
            )
        body = parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
        return f"{body} AND t.\"tipo_ventana\" = 'HOUR'"
    if hours and name in _FACT_TIME and "id_produccion" in columns:
        column = _FACT_TIME[name]
        parts = []
        for production_id, start in sorted(set(hours)):
            end = start + timedelta(hours=1)
            opened = start.strftime("%Y-%m-%d %H:%M:%S")
            closed = end.strftime("%Y-%m-%d %H:%M:%S")
            parts.append(
                f"({_sql_in('id_produccion', [production_id])} "
                f"AND t.\"{column}\" >= timestamp '{opened}' "
                f"AND t.\"{column}\" < timestamp '{closed}')"
            )
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
    if hours and result_ids and "id_resultado" in columns:
        return _sql_in("id_resultado", sorted(set(result_ids)))
    if not replace_ids or hours:
        return "" if not replace_ids and not hours else None
    if "id_produccion" not in columns:
        return None
    return _sql_in("id_produccion", sorted(replace_ids))


_SCOPE_MERGE_MAX_KEYS = 128


def _changed_scope_keys(table, rows, *, complete, telemetry=None):
    """Return a bounded set of upserts, or None when the existing writer is needed."""
    remaining = {}
    for row in rows:
        remaining[row["scope_id"]] = canonical(row)
    changed = set()
    if telemetry is not None:
        telemetry.update(rows_compared_unchanged=0, comparison_outcome="eligible")
    if not remaining and not complete:
        return changed
    scanner = table.to_pyarrow_dataset().scanner(use_threads=False)
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            key = row["scope_id"]
            expected = remaining.pop(key, None)
            if expected is None:
                if complete:
                    if telemetry is not None:
                        telemetry["comparison_outcome"] = "withdrawal"
                    return None  # An authoritative snapshot withdraws this key.
            elif expected != canonical(row):
                changed.add(key)
                if len(changed) > _SCOPE_MERGE_MAX_KEYS:
                    if telemetry is not None:
                        telemetry.update(comparison_outcome="limit", changed_keys_min=len(changed))
                    return None
            elif telemetry is not None:
                telemetry["rows_compared_unchanged"] += 1
        if not complete and not remaining:
            break
    if len(changed) + len(remaining) > _SCOPE_MERGE_MAX_KEYS:
        if telemetry is not None:
            telemetry.update(
                comparison_outcome="limit", changed_keys_min=len(changed) + len(remaining)
            )
        return None
    changed.update(remaining)
    if telemetry is not None:
        telemetry.update(
            changed_keys=len(changed),
            compared_inserted=len(remaining),
            compared_updated=len(changed) - len(remaining),
        )
    if any("'" in key or "\\" in key for key in changed):
        if telemetry is not None:
            telemetry["comparison_outcome"] = "unsupported_key"
        return None
    return changed


class Publisher:
    """One active writer. Journal contains receipts/hashes, never business rows."""

    def __init__(
        self,
        root: Path,
        *,
        environment: str,
        plant: str,
        failpoint: Callable[[str], None] | None = None,
        verified_base: Snapshot | None = None,
    ):
        self.root = root.resolve()
        self.environment, self.plant = environment, plant
        self.failpoint = failpoint or (lambda _: None)
        self.verified_base = verified_base

    def _verify_receipts(self, refs):
        base = self.verified_base
        verified = (
            {
                ref["name"]: ref
                for ref in base.manifest["tables"]
                if ref["name"] in base._verified_tables
            }
            if base and base.root == self.root
            else {}
        )
        for ref in refs:
            if verified.get(ref["name"]) != ref:
                Snapshot(self.root, {"tables": [ref]}).table(ref["name"])

    def publish(
        self,
        snapshot: Snapshot,
        tables: dict[str, list[dict]],
        *,
        config_id: str,
        cutoff: str,
        coverage: list[dict],
        replacement_scopes: dict[str, dict[str, list]] | None = None,
        replace_ids: set[str] | None = None,
        hours: tuple[tuple[str, datetime], ...] | None = None,
        result_ids: tuple[str, ...] | None = None,
        fact_bounds: tuple[tuple[str, datetime, datetime], ...] | None = None,
        lock_timeout: float = 0,
    ) -> dict:
        if (
            self.root == snapshot.root
            or self.root.is_relative_to(snapshot.root)
            or snapshot.root.is_relative_to(self.root)
        ):
            raise IntegrityError("Gold output and Silver input must be disjoint")
        if (
            snapshot.manifest["environment"] != self.environment
            or snapshot.manifest["plant"] != self.plant
        ):
            raise IntegrityError("Input environment/plant does not match Gold configuration")
        self.root.mkdir(parents=True, exist_ok=True)
        # Delta commits run outside this lock. Only the publication pointer is swapped here.
        return self._publish(
            snapshot,
            tables,
            config_id,
            cutoff,
            coverage,
            replacement_scopes or {},
            replace_ids,
            hours,
            result_ids,
            fact_bounds,
        )

    def _publish_stream(
        self,
        snapshot,
        rows,
        config_id,
        cutoff,
        coverage,
        replace_ids=None,
        hours=None,
        result_ids=None,
        fact_bounds=None,
    ):
        """Publish from the spill file, one batch of 1000 rows at a time."""
        from .contracts import CONTRACT_VERSION, CONTRACTS, contract_id, ordered, validate

        began = time.perf_counter()
        validate(rows, complete=set(rows) == set(CONTRACTS))
        validation_ms = round((time.perf_counter() - began) * 1000, 2)
        scope = commit_scope(replace_ids, hours)
        operation = digest(["gold-v1", snapshot.publication_id, config_id, cutoff, scope])
        pointer_path = self.root / "current.json"
        pointer, previous = envelope(self.root) if pointer_path.exists() else (None, None)
        if previous and (
            previous["environment"] != self.environment or previous["plant"] != self.plant
        ):
            raise IntegrityError("Existing Gold store belongs to a different environment/plant")
        expected_contract = {"version": CONTRACT_VERSION, "id": contract_id()}
        if previous and previous.get("contract") != expected_contract:
            raise IntegrityError(
                "Existing Gold contract differs; use an isolated output and validate migration"
            )
        prior_names = {r["name"] for r in previous["tables"]} if previous else set()
        affected = set(rows)
        for name in ordered():
            if any(parent in affected for parent in CONTRACTS[name].parents):
                affected.add(name)
        omitted = (affected & prior_names) - rows.keys()
        if omitted:
            raise IntegrityError(
                f"Dependent tables must be recalculated together: {sorted(omitted)}"
            )
        previous_scores = {}
        if previous and any(r["name"] == "quality_scores" for r in previous["tables"]):
            previous_scores = {
                r["id_resultado"]: r for r in Snapshot(self.root, previous).rows("quality_scores")
            }

        def prepared(name):
            source = rows.store.iter_rows(name) if name in rows.spilled else rows[name]
            for raw in source:
                row = dict(raw)
                if name == "quality_scores":
                    if row.get("basis_publication_id") is None:
                        row["basis_publication_id"] = operation
                    accepted = previous_scores.get(row["id_resultado"])
                    if accepted and all(
                        row.get(k) == v for k, v in accepted.items() if k != "fecha_calculo"
                    ):
                        row["fecha_calculo"] = accepted["fecha_calculo"]
                yield check_row(name, row)

        def content_hash(name) -> str:
            hasher = hashlib.sha256()
            count = 0
            for row in prepared(name):
                hasher.update(canonical(row))
                hasher.update(b"\0")
                count += 1
            hasher.update(str(count).encode())
            return hasher.hexdigest()

        names = ordered(rows)
        hashes = {name: content_hash(name) for name in names}
        if pointer and pointer["publication_id"] == operation:
            journal = self.root / "operations" / f"{operation}.json"
            if journal.exists():
                state = read_json(journal)
                if state["table_hashes"] != hashes:
                    raise IntegrityError("Repeated operation produced different data")
                state["state"] = "ACKNOWLEDGED"
                atomic_json(journal, state)
            return {"status": "UNCHANGED", "publication_id": operation}
        journal = self.root / "operations" / f"{operation}.json"
        if journal.exists():
            state = read_json(journal)
            if state["table_hashes"] != hashes:
                raise IntegrityError("Recovery inputs or transformation changed")
            if state["state"] == "ACKNOWLEDGED":
                raise IntegrityError(
                    "Operation was already published and has been superseded; "
                    "replaying it would roll the current pointer back"
                )
        else:
            pending = [
                p
                for p in (self.root / "operations").glob("*.json")
                if read_json(p)["state"] != "ACKNOWLEDGED"
            ]
            if pending and hours is None and fact_bounds is None:
                raise IntegrityError(
                    "Recover the pending operation before publishing another input"
                )
            state = {
                "operation_id": operation,
                "input_publication_id": snapshot.publication_id,
                "state": "PREPARED",
                "table_hashes": hashes,
                "receipts": {},
            }
            atomic_json(journal, state)
        self.failpoint("prepared")
        prior_refs = {r["name"]: r for r in previous["tables"]} if previous else {}
        writes = {}
        for name in names:
            table_start = time.perf_counter()
            timings = {}
            strategy = "recovered"
            target = self.root / "tables" / name
            scoped_hash = digest([hashes[name], scope])
            ref = state["receipts"].get(name)
            if ref is None:
                current = DeltaTable(str(target)) if (target / "_delta_log").exists() else None
                last = current.history(1)[0] if current else {}
                delete_predicate = chunk_delete_predicate(
                    name, replace_ids, hours, result_ids, fact_bounds
                )
                full_scope_snapshot = not scope and fact_bounds is None
                if current is not None and last.get("gold_operation_id") != operation:
                    accepted = prior_refs.get(name)
                    if accepted is None:
                        raise IntegrityError(
                            "Existing output has no published owner; "
                            "restore its pointer before writing"
                        )
                    same_version = current.version() == accepted["delta_version"]
                    if not same_version and hours is None and fact_bounds is None:
                        raise IntegrityError(
                            "Output changed outside the pending confirmed operation"
                        )
                changed_keys = None
                if (
                    name == "publication_scope_status"
                    and current is not None
                    and last.get("gold_operation_id") != operation
                    and current.version() == prior_refs[name]["delta_version"]
                    and (full_scope_snapshot or delete_predicate in (None, ""))
                ):
                    compare_start = time.perf_counter()
                    changed_keys = _changed_scope_keys(
                        current,
                        prepared(name),
                        complete=full_scope_snapshot or delete_predicate == "",
                        telemetry=timings,
                    )
                    timings["compare_ms"] = round((time.perf_counter() - compare_start) * 1000, 2)
                if last.get("gold_operation_id") == operation:
                    if last.get("gold_content_hash") != scoped_hash:
                        raise IntegrityError("Existing commit has different transformation content")
                elif (
                    name != "publication_scope_status"
                    and last.get("gold_content_hash") == scoped_hash
                ):
                    if name not in prior_refs:
                        raise IntegrityError(
                            "Unreferenced existing output requires explicit recovery"
                        )
                    ref = prior_refs[name]
                    strategy = "reuse"
                elif changed_keys is not None and not changed_keys:
                    ref = prior_refs[name]
                    strategy = "reuse"
                else:
                    properties = CommitProperties(
                        custom_metadata={
                            "gold_operation_id": operation,
                            "gold_content_hash": scoped_hash,
                        },
                        app_transactions=[Transaction(f"gold:{operation}:{name}", 1)],
                    )
                    if current is None:
                        strategy = "create"
                        write_deltalake(
                            target,
                            _batch_reader(name, prepared(name)),
                            mode="error",
                            partition_by=partition_columns(name),
                            configuration=RETENTION,
                            commit_properties=properties,
                        )
                    elif (
                        name == "publication_scope_status"
                        and full_scope_snapshot
                        and changed_keys is None
                    ):
                        strategy = "overwrite"
                        # A full scope snapshot is authoritative. Stream its replacement directly.
                        write_deltalake(
                            target,
                            _batch_reader(name, prepared(name)),
                            mode="overwrite",
                            commit_properties=properties,
                        )
                    else:
                        strategy = "sparse_merge" if changed_keys is not None else "merge"
                        join = " AND ".join(f't."{k}" = s."{k}"' for k in KEYS[name])
                        changed = " OR ".join(
                            f'(t."{k}" IS DISTINCT FROM s."{k}")' for k in SCHEMAS[name].names
                        )
                        before_version = current.version()
                        sparse_data = None
                        if changed_keys is not None:
                            sparse_data = make_table(
                                name, [r for r in prepared(name) if r["scope_id"] in changed_keys]
                            )
                            join += " AND " + _sql_in("scope_id", sorted(changed_keys))
                            delete_predicate = None
                        from deltalake.exceptions import CommitFailedError

                        merge_start = time.perf_counter()
                        for _attempt in range(8):
                            data = (
                                sparse_data
                                if sparse_data is not None
                                else _batch_reader(name, prepared(name))
                            )
                            merge = (
                                current.merge(
                                    data,
                                    join,
                                    source_alias="s",
                                    target_alias="t",
                                    commit_properties=properties,
                                    streamed_exec=sparse_data is None,
                                )
                                .when_matched_update_all(predicate=changed)
                                .when_not_matched_insert_all()
                            )
                            if delete_predicate is not None:
                                delete = merge.when_not_matched_by_source_delete
                                merge = (
                                    delete()
                                    if delete_predicate == ""
                                    else delete(predicate=delete_predicate)
                                )
                            try:
                                merge.execute()
                                break
                            except CommitFailedError:
                                if _attempt == 7:
                                    raise
                                current = DeltaTable(str(target))
                                if sparse_data is not None and current.version() != before_version:
                                    raise
                        timings["merge_wall_ms"] = round(
                            (time.perf_counter() - merge_start) * 1000, 2
                        )
                        after = DeltaTable(str(target))
                        if after.version() == before_version:
                            if name not in prior_refs:
                                raise IntegrityError("No-op commit has no confirmed output receipt")
                            ref = prior_refs[name]
                    self.failpoint(f"commit:{name}")
                if ref is None:
                    current = DeltaTable(str(target))
                    ref = reference(self.root, name, f"tables/{name}", current.version(), operation)
                state["receipts"][name] = ref
                atomic_json(journal, state)
            writes[name] = {
                **commit_metrics(target, ref, prior_refs.get(name), operation),
                **timings,
                "strategy": strategy,
                "table_ms": round((time.perf_counter() - table_start) * 1000, 2),
            }
            prior_refs[name] = ref
        state["state"] = "VERIFIED"
        atomic_json(journal, state)
        self._verify_receipts(prior_refs.values())
        self.failpoint("verified")
        evidence = {}
        manifest = promote_cas(
            self.root,
            list(prior_refs.values()),
            environment=self.environment,
            plant=self.plant,
            input_id=snapshot.publication_id,
            operation_id=operation,
            generation=pointer["generation"] + 1 if pointer else 1,
            previous=pointer["publication_id"] if pointer else None,
            coverage=coverage,
            dependencies=[
                {
                    "table": name,
                    "input_versions": {
                        parent: prior_refs[parent]["delta_version"]
                        for parent in CONTRACTS[name].parents
                        if parent in prior_refs
                    },
                }
                for name in sorted(prior_refs)
            ]
            + independent_dependencies(self.root, snapshot, telemetry=evidence),
            contract=expected_contract,
        )
        self.failpoint("promoted")
        state["state"] = "ACKNOWLEDGED"
        atomic_json(journal, state)
        return {
            "status": "PUBLISHED",
            "publication_id": manifest["publication_id"],
            "tables": len(prior_refs),
            "write_telemetry": publication_metrics(
                writes,
                evidence,
                round((time.perf_counter() - began) * 1000, 2),
                validation_ms=validation_ms,
            ),
            **(
                {
                    "publication_scope_status_write": legacy_scope_metrics(
                        writes["publication_scope_status"]
                    )
                }
                if "publication_scope_status" in writes
                else {}
            ),
        }

    def _publish(
        self,
        snapshot,
        rows,
        config_id,
        cutoff,
        coverage,
        scopes,
        replace_ids=None,
        hours=None,
        result_ids=None,
        fact_bounds=None,
    ):
        from .contracts import CONTRACT_VERSION, CONTRACTS, contract_id, ordered, validate

        if getattr(rows, "spilled", None):
            if scopes:
                raise IntegrityError("A spilled publication has no partial replacement scope")
            return self._publish_stream(
                snapshot,
                rows,
                config_id,
                cutoff,
                coverage,
                replace_ids,
                hours,
                result_ids,
                fact_bounds,
            )
        began = time.perf_counter()
        ordered(rows)
        for name, values in rows.items():
            make_table(name, values)
        operation = digest(["gold-v1", snapshot.publication_id, config_id, cutoff, scopes])
        pointer_path = self.root / "current.json"
        pointer, previous = envelope(self.root) if pointer_path.exists() else (None, None)
        if previous and (
            previous["environment"] != self.environment or previous["plant"] != self.plant
        ):
            raise IntegrityError("Existing Gold store belongs to a different environment/plant")
        expected_contract = {"version": CONTRACT_VERSION, "id": contract_id()}
        if previous and previous.get("contract") != expected_contract:
            raise IntegrityError(
                "Existing Gold contract differs; use an isolated output and validate migration"
            )
        _validate_scopes(rows, scopes)
        prior_names = {r["name"] for r in previous["tables"]} if previous else set()
        # A parent revision cannot silently relabel unchanged derived data as current.
        affected = set(rows)
        for name in ordered():
            if any(parent in affected for parent in CONTRACTS[name].parents):
                affected.add(name)
        omitted = (affected & prior_names) - rows.keys()
        if omitted:
            raise IntegrityError(
                f"Dependent tables must be recalculated together: {sorted(omitted)}"
            )
        candidate = {}
        old_snapshot = Snapshot(self.root, previous) if previous else None
        for name in prior_names | rows.keys():
            if name not in rows or name in scopes:
                old_rows = old_snapshot.rows(name) if name in prior_names else []
                if name in rows:
                    scope = scopes[name]
                    old_rows = [r for r in old_rows if not all(r[k] in v for k, v in scope.items())]
                candidate[name] = [*old_rows, *rows.get(name, [])]
            else:
                candidate[name] = rows[name]
        validation_start = time.perf_counter()
        validate(candidate, complete=set(candidate) == set(CONTRACTS))
        validation_ms = round((time.perf_counter() - validation_start) * 1000, 2)
        if previous:
            prior_by_name = {r["name"]: r for r in previous["tables"]}
            if "quality_scores" in rows and "quality_scores" in prior_by_name:
                old = Snapshot(self.root, previous).rows("quality_scores")
                by_id = {r["id_resultado"]: r for r in old}
                for row in rows["quality_scores"]:
                    accepted = by_id.get(row["id_resultado"])
                    if accepted and all(
                        row.get(k) == v for k, v in accepted.items() if k != "fecha_calculo"
                    ):
                        row["fecha_calculo"] = accepted["fecha_calculo"]
        # Validate all tables before allowing the first write.
        tables = {name: make_table(name, values) for name, values in rows.items()}
        hashes = {name: digest(values) for name, values in rows.items()}
        if pointer and pointer["publication_id"] == operation:
            journal = self.root / "operations" / f"{operation}.json"
            if journal.exists():
                state = read_json(journal)
                if state["table_hashes"] != hashes:
                    raise IntegrityError("Repeated operation produced different data")
                state["state"] = "ACKNOWLEDGED"
                atomic_json(journal, state)
            return {"status": "UNCHANGED", "publication_id": operation}
        journal = self.root / "operations" / f"{operation}.json"
        if journal.exists():
            state = read_json(journal)
            if state["table_hashes"] != hashes:
                raise IntegrityError("Recovery inputs or transformation changed")
            if state["state"] == "ACKNOWLEDGED":
                raise IntegrityError(
                    "Operation was already published and has been superseded; "
                    "replaying it would roll the current pointer back"
                )
        else:
            pending = [
                p
                for p in (self.root / "operations").glob("*.json")
                if read_json(p)["state"] != "ACKNOWLEDGED"
            ]
            if pending and not (hours or fact_bounds):
                raise IntegrityError(
                    "Recover the pending operation before publishing another input"
                )
            state = {
                "operation_id": operation,
                "input_publication_id": snapshot.publication_id,
                "state": "PREPARED",
                "table_hashes": hashes,
                "receipts": {},
            }
            atomic_json(journal, state)
        self.failpoint("prepared")
        prior_refs = {r["name"]: r for r in previous["tables"]} if previous else {}
        writes = {}
        for name in ordered(tables):
            table_start = time.perf_counter()
            strategy = "recovered"
            timings = {}
            table = tables[name]
            target = self.root / "tables" / name
            scope = scopes.get(name, {})
            predicate = None
            if scope:
                parts = []
                for column, values in sorted(scope.items()):
                    if column not in SCHEMAS[name].names or not values:
                        raise IntegrityError(
                            "Replacement scope needs known columns and explicit values"
                        )

                    def literal(value):
                        if type(value) is int:
                            return str(value)
                        if isinstance(value, str):
                            return "'" + value.replace("'", "''") + "'"
                        raise IntegrityError("Scope values must be exact strings or integers")

                    parts.append(f'"{column}" IN ({", ".join(literal(v) for v in values)})')
                predicate = " AND ".join(parts)
                if any(any(r[k] not in v for k, v in scope.items()) for r in rows[name]):
                    raise IntegrityError("Output row falls outside its declared replacement scope")
            scoped_hash = digest([hashes[name], scope])
            ref = state["receipts"].get(name)
            if ref is None:
                current = DeltaTable(str(target)) if (target / "_delta_log").exists() else None
                last = current.history(1)[0] if current else {}
                if current is not None and last.get("gold_operation_id") != operation:
                    accepted = prior_refs.get(name)
                    if accepted is None:
                        raise IntegrityError(
                            "Existing output has no published owner; "
                            "restore its pointer before writing"
                        )
                    same_version = current.version() == accepted["delta_version"]
                    if not same_version and not (hours or fact_bounds):
                        raise IntegrityError(
                            "Output changed outside the pending confirmed operation"
                        )
                if last.get("gold_operation_id") == operation:
                    if last.get("gold_content_hash") != scoped_hash:
                        raise IntegrityError("Existing commit has different transformation content")
                elif last.get("gold_content_hash") == scoped_hash:
                    # Unchanged data reuses its receipt/version without another Delta commit.
                    if name not in prior_refs:
                        raise IntegrityError(
                            "Unreferenced existing output requires explicit recovery"
                        )
                    ref = prior_refs[name]
                    strategy = "reuse"
                else:
                    properties = CommitProperties(
                        custom_metadata={
                            "gold_operation_id": operation,
                            "gold_content_hash": scoped_hash,
                        },
                        app_transactions=[Transaction(f"gold:{operation}:{name}", 1)],
                    )
                    if current is None:
                        strategy = "create"
                        write_deltalake(
                            target,
                            table,
                            mode="error",
                            partition_by=partition_columns(name),
                            configuration=RETENTION,
                            commit_properties=properties,
                        )
                    else:
                        strategy = "merge"
                        join = " AND ".join(f't."{k}" = s."{k}"' for k in KEYS[name])
                        changed = " OR ".join(
                            f'(t."{k}" IS DISTINCT FROM s."{k}")' for k in SCHEMAS[name].names
                        )
                        delete_scope = predicate
                        # Full accepted source within this explicit scope. Equal rows are
                        # not updated; obsolete derived rows are withdrawn in the same commit.
                        before_version = current.version()
                        merger = (
                            current.merge(
                                table,
                                join,
                                source_alias="s",
                                target_alias="t",
                                commit_properties=properties,
                            )
                            .when_matched_update_all(predicate=changed)
                            .when_not_matched_insert_all()
                        )
                        if delete_scope:
                            for column in scope:
                                delete_scope = delete_scope.replace(f'"{column}"', f't."{column}"')
                        merge_start = time.perf_counter()
                        merger.when_not_matched_by_source_delete(predicate=delete_scope).execute()
                        timings["merge_wall_ms"] = round(
                            (time.perf_counter() - merge_start) * 1000, 2
                        )
                        after = DeltaTable(str(target))
                        if after.version() == before_version:
                            if name not in prior_refs:
                                raise IntegrityError("No-op commit has no confirmed output receipt")
                            ref = prior_refs[name]
                    self.failpoint(f"commit:{name}")
                if ref is None:
                    current = DeltaTable(str(target))
                    ref = reference(self.root, name, f"tables/{name}", current.version(), operation)
                state["receipts"][name] = ref
                atomic_json(journal, state)
            writes[name] = {
                **commit_metrics(target, ref, prior_refs.get(name), operation),
                **timings,
                "strategy": strategy,
                "table_ms": round((time.perf_counter() - table_start) * 1000, 2),
            }
            prior_refs[name] = ref
        state["state"] = "VERIFIED"
        atomic_json(journal, state)
        self._verify_receipts(prior_refs.values())
        self.failpoint("verified")
        evidence = {}
        manifest = promote_cas(
            self.root,
            list(prior_refs.values()),
            environment=self.environment,
            plant=self.plant,
            input_id=snapshot.publication_id,
            operation_id=operation,
            generation=pointer["generation"] + 1 if pointer else 1,
            previous=pointer["publication_id"] if pointer else None,
            coverage=coverage,
            dependencies=[
                {
                    "table": name,
                    "input_versions": {
                        parent: prior_refs[parent]["delta_version"]
                        for parent in CONTRACTS[name].parents
                        if parent in prior_refs
                    },
                }
                for name in sorted(prior_refs)
            ]
            + independent_dependencies(self.root, snapshot, telemetry=evidence),
            contract=expected_contract,
        )
        self.failpoint("promoted")
        state["state"] = "ACKNOWLEDGED"
        atomic_json(journal, state)
        return {
            "status": "PUBLISHED",
            "publication_id": manifest["publication_id"],
            "tables": len(prior_refs),
            "write_telemetry": publication_metrics(
                writes,
                evidence,
                round((time.perf_counter() - began) * 1000, 2),
                validation_ms=validation_ms,
            ),
        }


def _batch_reader(name: str, rows: Iterator[dict]):
    """Arrow batches of 1000 prepared rows, or an empty table when the family is empty."""
    iterator = iter(rows)
    try:
        first = next(iterator)
    except StopIteration:
        return pa.Table.from_pylist([], schema=SCHEMAS[name])

    def batches():
        buffer = [first]
        for row in iterator:
            buffer.append(row)
            if len(buffer) == 1000:
                yield pa.RecordBatch.from_pylist(buffer, schema=SCHEMAS[name])
                buffer = []
        if buffer:
            yield pa.RecordBatch.from_pylist(buffer, schema=SCHEMAS[name])

    return pa.RecordBatchReader.from_batches(SCHEMAS[name], batches())


def _validate_scopes(rows, scopes):
    """Check every scope before the first commit, including tables ordered last."""
    if not set(scopes).issubset(rows):
        raise IntegrityError("Replacement scope has no corresponding output table")
    for name, scope in scopes.items():
        if not scope:
            raise IntegrityError("Replacement scope cannot be empty")
        for column, values in scope.items():
            if column not in SCHEMAS[name].names or not values:
                raise IntegrityError("Replacement scope needs known columns and explicit values")
            if any(type(value) not in {str, int} for value in values):
                raise IntegrityError("Scope values must be exact strings or integers")
        if any(any(r[k] not in v for k, v in scope.items()) for r in rows[name]):
            raise IntegrityError("Output row falls outside its declared replacement scope")


def independent_dependencies(root, snapshot, *, telemetry=None):
    """A Gold pointer can refer only to evidence already copied and verified locally."""
    if (
        not hasattr(snapshot, "document")
        or snapshot.document.get("schema_id") != "quality-independent-input-v1"
    ):
        return []
    from .evidence import MAX_MANIFEST_BYTES, SCHEMA, Evidence, evidence_files
    from .evidence_storage import publish_reference

    target = root / "evidence"
    target.mkdir(parents=True, exist_ok=True)
    seen = set()
    if telemetry is not None:
        telemetry.update(
            new_links=0, existing_links=0, new_copies=0, existing_copies=0, new_physical_bytes=0
        )

    def copy(item):
        uri = item["uri"]
        if uri in seen:
            return
        seen.add(uri)
        kind = publish_reference(snapshot.root, target, item)
        if telemetry is None:
            return
        if kind.endswith("_link"):
            telemetry[f"{kind}s"] += 1
        else:
            telemetry[f"{kind.removesuffix('_copy')}_copies"] += 1
        if kind == "new_copy" and telemetry["new_physical_bytes"] is not None:
            try:
                size = allocated_bytes(target / uri)
            except OSError:
                size = None
            telemetry["new_physical_bytes"] = (
                None if size is None else telemetry["new_physical_bytes"] + size
            )

    def copy_tree(ref):
        from ingesta_delta_lake.association_cas import sync

        from .evidence import checked_file

        marker = within(target, f"index/ack/{ref['hash']}.json")
        if marker.exists():
            if read_json(marker) != ref:
                raise IntegrityError("Gold fragment accreditation differs")
            checked_file(target, ref)
            return
        page = snapshot.index.page(ref)
        for child in page.get("children", {}).values():
            copy_tree(child)
        for part in page.get("parts", []):
            copy(part)
        copy(ref)
        immutable_json(marker, ref)
        sync(marker.parent)
        sync(marker.parent.parent)

    for ref in snapshot.document["tables"]:
        if "parts_root" in ref or "family_roots" in ref:
            roots = [ref["parts_root"]] if "parts_root" in ref else ref["family_roots"].values()
            for node in roots:
                if node:
                    copy_tree(node)
        else:
            for item in evidence_files(ref):
                copy(item)
    for ref in snapshot.wire_document["tables"]:
        for page in ref.get("parts_pages", []):
            publish_reference(snapshot.root, target, page)
    immutable_json(
        target / f"{snapshot.publication_id}.json",
        snapshot.wire_document,
        max_bytes=MAX_MANIFEST_BYTES,
    )
    copied = Evidence.open(target, snapshot.publication_id)
    if copied.document["schema_id"] != SCHEMA:
        raise IntegrityError("Gold copied evidence schema differs")
    return [
        {
            "kind": "independent-input",
            "input_versions": {},
            "evidence_id": snapshot.publication_id,
            "lane": snapshot.document["lane"],
        }
    ]
