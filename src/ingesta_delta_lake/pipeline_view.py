"""Read confirmed lane snapshots without locks, silently choosing no conflicting fact."""

import json
from collections import defaultdict
from dataclasses import dataclass, field

import pyarrow as pa
from deltalake import DeltaTable

from .common import IntegrityError, digest
from .contracts import CONTRACTS
from .pipeline_archive import META
from .pipeline_publications import _inside, schema_hash
from .pipeline_rows import business_key, conflicting_keys, payload_hash, reconcile, review_hash


class ArchiveSnapshot:
    def __init__(self, storage, *, manifest_path=None):
        self.storage = storage
        from pathlib import Path

        path = (
            Path(manifest_path)
            if manifest_path is not None
            else storage.exchange / "archive" / "current.json"
        )
        self.silver_root = Path(storage.silver)
        with path.open(encoding="utf-8") as stream:
            self.manifest = json.load(stream)
        payload = {k: v for k, v in self.manifest.items() if k != "publication_id"}
        if self.manifest.get("schema_id") != "fenix-archive-snapshot-v1" or (
            digest(payload) != self.manifest.get("publication_id")
        ):
            raise IntegrityError("Archive snapshot hash or schema differs")

    def records(self, family, *, batch_size, expression=None):
        ref = self.manifest["tables"].get(family)
        if ref is None:
            return
        table = DeltaTable(
            str(_inside(self.silver_root, ref["archive_uri"])), version=ref["archive_version"]
        )
        if table.metadata().id != ref["archive_table_id"] or (
            schema_hash(pa.schema(table.schema().to_arrow())) != ref["schema_id_hash"]
        ):
            raise IntegrityError("Archive table identity differs")
        for batch in (
            table.to_pyarrow_dataset()
            .scanner(filter=expression, batch_size=batch_size, use_threads=False)
            .to_batches()
        ):
            for value in batch.to_pylist():
                row = {k: v for k, v in value.items() if k not in META.names}
                if payload_hash(row) != value["__archive_hash"] or (
                    business_key(family, row) != value["__archive_key"]
                ):
                    raise IntegrityError("Archived row differs from its confirmed hash")
                yield {
                    "row": row,
                    "source_table": value["__archive_source_table"],
                    "source_version": value["__archive_source_version"],
                    "change_type": value["__archive_change_type"],
                }


@dataclass(frozen=True)
class ViewResult:
    rows: list[dict]
    conflicts: list[dict]
    lineages: dict = field(default_factory=dict)


class SilverView:
    def __init__(self, *, live=None, history=None, archive=None):
        self.live, self.history, self.archive = live, history, archive

    def rows(self, family, *, max_rows, batch_size, expression=None):
        grouped, scanned = defaultdict(list), 0
        rejected = set()
        for snapshot in (self.live, self.history):
            if snapshot is not None:
                rejected.update(
                    snapshot.manifest["tables"].get(family, {}).get("rejected_hashes", [])
                )
        if self.archive is not None:
            rejected.update(self.archive.manifest.get("rejections", {}).get(family, []))

        def accept(records):
            nonlocal scanned
            for record in records:
                scanned += 1
                if scanned > max_rows:
                    raise IntegrityError("Combined Silver query exceeds its explicit row budget")
                if review_hash(family, record["row"]) in rejected:
                    continue
                grouped[business_key(family, record["row"])].append(record)

        for snapshot in (self.live, self.history):
            if snapshot is None or family not in snapshot.manifest["tables"]:
                continue
            ref = snapshot.manifest["tables"][family]
            accept(
                {"row": row, "source_table": ref["table_id"], "source_version": ref["version"]}
                for batch in snapshot.batches(family, batch_size=batch_size, expression=expression)
                for row in batch.to_pylist()
            )
            if self.archive is not None:
                start = self.archive.manifest["completed"].get(ref["table_id"], -1) + 1
                if start <= ref["version"]:
                    table = snapshot.table(family)
                    maintenance = {
                        h["version"]
                        for h in table.history()
                        if h.get("ingesta_maintenance") == "archive-retirement"
                    }
                    for batch in pa.RecordBatchReader.from_stream(
                        table.load_cdf(start, ref["version"])
                    ):
                        # CDF evidence is bounded even when a caller filter excludes all rows.
                        scanned += batch.num_rows
                        if scanned > max_rows:
                            raise IntegrityError(
                                "Combined Silver query exceeds its explicit row budget"
                            )
                        for event in batch.to_pylist():
                            if (
                                event["_change_type"] not in {"delete", "update_preimage"}
                                or event["_commit_version"] in maintenance
                            ):
                                continue
                            row = {
                                k: v
                                for k, v in event.items()
                                if k in CONTRACTS[family].schema.names
                            }
                            if review_hash(family, row) in rejected:
                                continue
                            grouped[business_key(family, row)].append(
                                {
                                    "row": row,
                                    "source_table": ref["table_id"],
                                    "source_version": event["_commit_version"],
                                    "change_type": event["_change_type"],
                                }
                            )
        if self.archive is not None:
            accept(self.archive.records(family, batch_size=batch_size, expression=expression))
        rows, conflicts = {}, []
        for key, versions in grouped.items():
            try:
                row = reconcile(family, versions)
                if row is not None:
                    rows[key] = row
            except IntegrityError as exc:
                conflicts.append({"key": key, "reason": str(exc)})
        invalid = conflicting_keys(family, rows)
        for key in sorted(invalid):
            conflicts.append({"key": key, "reason": "CROSS_LANE_CONTRACT_CONFLICT"})
            rows.pop(key)
        lineages = {
            key: [
                {
                    "source_table": record["source_table"],
                    "source_version": record["source_version"],
                    "row_hash": review_hash(family, row),
                }
                for record in grouped[key]
                if record["row"] == row
                and record.get("change_type", "insert") in {"insert", "update_postimage"}
            ]
            for key, row in rows.items()
        }
        return ViewResult(list(rows.values()), conflicts, lineages)
