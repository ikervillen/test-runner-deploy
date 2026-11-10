"""Pinned, verified Delta inputs and recoverable multi-table Gold publication.

Only this producer writes metadata. Readers never connect to active ingestion SQLite.
Local atomic replacement and an OS-backed writer lock are prerequisites, not assumed
cross-volume or object-store transactions.
"""

from __future__ import annotations

import hashlib
import json
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
    ) -> Iterator[pa.RecordBatch]:
        count = 0
        # The predicate prunes by partition value and by file statistics before decoding.
        scanner = (
            self.table(name)
            .to_pyarrow_dataset(file_pruning_predicate=file_pruning_predicate)
            .scanner(
                columns=columns if expression is None else None,
                batch_size=batch_size,
                # Single-threaded on purpose: batch order feeds float sums and input hashes,
                # and parity with the frozen legacy reference depends on that order.
                use_threads=False,
            )
        )
        for batch in scanner.to_batches():
            count += batch.num_rows
            if on_scan is not None:
                on_scan(batch.num_rows)
            if count > max_rows:
                raise IntegrityError(f"{name}: per-scope row budget exceeded; split the scope")
            # Arrow 23 cannot prune string_view statistics written by Delta MERGE.
            # Delta prunes eligible files; filter bounded, schema-cast batches here.
            if expression is not None:
                batch = batch.filter(expression)
                if columns is not None:
                    batch = batch.select(columns)
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
) -> str | None:
    """How a chunk removes its own stale rows and leaves every other hour stored.

    An empty string deletes every unmatched row, which is the full publication.
    None keeps unmatched rows: a fact table has no hour columns, so deleting by
    production would remove hours this commit did not recalculate.
    """
    columns = set(SCHEMAS[name].names)
    if hours and {"id_produccion", "inicio", "tipo_ventana"} <= columns:
        grouped: dict[datetime, list[str]] = {}
        for production_id, start in hours:
            grouped.setdefault(start, []).append(production_id)
        parts = []
        for start, identities in sorted(grouped.items()):
            stamp = start.strftime("%Y-%m-%d %H:%M:%S")
            parts.append(
                f'({_sql_in("id_produccion", sorted(set(identities)))} '
                f"AND t.\"inicio\" = timestamp '{stamp}')"
            )
        body = parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
        return f'{body} AND t."tipo_ventana" = \'HOUR\''
    if hours and name in _FACT_TIME and "id_produccion" in columns:
        column = _FACT_TIME[name]
        parts = []
        for production_id, start in sorted(set(hours)):
            end = start + timedelta(hours=1)
            opened = start.strftime("%Y-%m-%d %H:%M:%S")
            closed = end.strftime("%Y-%m-%d %H:%M:%S")
            parts.append(
                f'({_sql_in("id_produccion", [production_id])} '
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


class Publisher:
    """One active writer. Journal contains receipts/hashes, never business rows."""

    def __init__(
        self,
        root: Path,
        *,
        environment: str,
        plant: str,
        failpoint: Callable[[str], None] | None = None,
    ):
        self.root = root.resolve()
        self.environment, self.plant = environment, plant
        self.failpoint = failpoint or (lambda _: None)

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
        with portalocker.Lock(self.root / "writer.lock", timeout=lock_timeout):
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
    ):
        """Publish from the spill file, one batch of 1000 rows at a time."""
        from .contracts import CONTRACT_VERSION, CONTRACTS, contract_id, ordered, validate

        validate(rows, complete=set(rows) == set(CONTRACTS))
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
            if pending:
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
        for name in names:
            target = self.root / "tables" / name
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
                    if current.version() != accepted["delta_version"]:
                        raise IntegrityError(
                            "Output changed outside the pending confirmed operation"
                        )
                if last.get("gold_operation_id") == operation:
                    if last.get("gold_content_hash") != scoped_hash:
                        raise IntegrityError("Existing commit has different transformation content")
                elif last.get("gold_content_hash") == scoped_hash:
                    if name not in prior_refs:
                        raise IntegrityError(
                            "Unreferenced existing output requires explicit recovery"
                        )
                    ref = prior_refs[name]
                else:
                    properties = CommitProperties(
                        custom_metadata={
                            "gold_operation_id": operation,
                            "gold_content_hash": scoped_hash,
                        },
                        app_transactions=[Transaction(f"gold:{operation}:{name}", 1)],
                    )
                    data = _batch_reader(name, prepared(name))
                    if current is None:
                        write_deltalake(
                            target,
                            data,
                            mode="error",
                            partition_by=partition_columns(name),
                            configuration=RETENTION,
                            commit_properties=properties,
                        )
                    else:
                        join = " AND ".join(f't."{k}" = s."{k}"' for k in KEYS[name])
                        changed = " OR ".join(
                            f'(t."{k}" IS DISTINCT FROM s."{k}")' for k in SCHEMAS[name].names
                        )
                        before_version = current.version()
                        merge = (
                            current.merge(
                                data,
                                join,
                                source_alias="s",
                                target_alias="t",
                                commit_properties=properties,
                                streamed_exec=True,
                            )
                            .when_matched_update_all(predicate=changed)
                            .when_not_matched_insert_all()
                        )
                        predicate = chunk_delete_predicate(name, replace_ids, hours, result_ids)
                        if predicate is not None:
                            delete = merge.when_not_matched_by_source_delete
                            merge = delete() if predicate == "" else delete(predicate=predicate)
                        merge.execute()
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
            prior_refs[name] = ref
        state["state"] = "VERIFIED"
        atomic_json(journal, state)
        for ref in prior_refs.values():
            Snapshot(self.root, {"tables": [ref]}).table(ref["name"])
        self.failpoint("verified")
        manifest = promote(
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
            ],
            contract=expected_contract,
        )
        self.failpoint("promoted")
        state["state"] = "ACKNOWLEDGED"
        atomic_json(journal, state)
        return {
            "status": "PUBLISHED",
            "publication_id": manifest["publication_id"],
            "tables": len(prior_refs),
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
    ):
        from .contracts import CONTRACT_VERSION, CONTRACTS, contract_id, ordered, validate

        if getattr(rows, "spilled", None):
            if scopes:
                raise IntegrityError("A spilled publication has no partial replacement scope")
            return self._publish_stream(
                snapshot, rows, config_id, cutoff, coverage, replace_ids, hours, result_ids
            )
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
        validate(candidate, complete=set(candidate) == set(CONTRACTS))
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
            if pending:
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
        for name in ordered(tables):
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
                    if current.version() != accepted["delta_version"]:
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
                else:
                    properties = CommitProperties(
                        custom_metadata={
                            "gold_operation_id": operation,
                            "gold_content_hash": scoped_hash,
                        },
                        app_transactions=[Transaction(f"gold:{operation}:{name}", 1)],
                    )
                    if current is None:
                        write_deltalake(
                            target,
                            table,
                            mode="error",
                            partition_by=partition_columns(name),
                            configuration=RETENTION,
                            commit_properties=properties,
                        )
                    else:
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
                        merger.when_not_matched_by_source_delete(predicate=delete_scope).execute()
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
            prior_refs[name] = ref
        state["state"] = "VERIFIED"
        atomic_json(journal, state)
        for ref in prior_refs.values():
            Snapshot(self.root, {"tables": [ref]}).table(ref["name"])
        self.failpoint("verified")
        manifest = promote(
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
            ],
            contract=expected_contract,
        )
        self.failpoint("promoted")
        state["state"] = "ACKNOWLEDGED"
        atomic_json(journal, state)
        return {
            "status": "PUBLISHED",
            "publication_id": manifest["publication_id"],
            "tables": len(prior_refs),
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
