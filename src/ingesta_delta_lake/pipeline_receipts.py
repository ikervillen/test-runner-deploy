"""Verified immutable archive receipts; never inspect the historical control database."""

import json

import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import DeltaTable

from .common import IntegrityError, digest
from .contracts import CONTRACTS
from .pipeline_archive import META
from .pipeline_publications import _inside, schema_hash
from .pipeline_rows import business_key, payload_hash


class ArchiveReceipts:
    def __init__(self, storage):
        self.storage = storage
        self.root = storage.exchange / "archive"

    def read(self, head, *, max_rows):
        operation = head.get("operation_id")
        if (
            not isinstance(operation, str)
            or len(operation) != 64
            or any(c not in "0123456789abcdef" for c in operation)
        ):
            raise IntegrityError("Invalid archive receipt identity")
        with (self.root / "receipts" / (operation + ".json")).open(encoding="utf-8") as stream:
            receipt = json.load(stream)
        if (
            receipt.get("schema_id") != "fenix-archive-receipt-v1"
            or receipt.get("operation_id") != operation
            or receipt.get("receipt_hash") != head.get("receipt_hash")
            or digest({k: v for k, v in receipt.items() if k != "receipt_hash"})
            != head.get("receipt_hash")
        ):
            raise IntegrityError("Archive receipt differs from its immutable proof")
        if len(receipt["rows"]) > max_rows:
            raise IntegrityError("Archive receipt exceeds its explicit row budget")
        return receipt

    def rows(self, receipt):
        table = DeltaTable(
            str(_inside(self.storage.silver, receipt["archive_uri"])),
            version=receipt["archive_version"],
        )
        if (
            table.metadata().id != receipt["archive_table_id"]
            or schema_hash(pa.schema(table.schema().to_arrow())) != receipt["schema_id_hash"]
        ):
            raise IntegrityError("Archive receipt refers to a different table")
        ids = [row["__archive_id"] for row in receipt["rows"]]
        values = (
            table.to_pyarrow_dataset()
            .to_table(filter=ds.field("__archive_id").isin(ids))
            .to_pylist()
        )
        expected = {row["__archive_id"]: row for row in receipt["rows"]}
        if len(values) != len(expected):
            raise IntegrityError("Archive receipt rows are missing or duplicated")
        for value in values:
            row = {field: value[field] for field in CONTRACTS[receipt["family"]].schema.names}
            if (
                {k: value[k] for k in META.names} != expected.get(value["__archive_id"])
                or payload_hash(row) != value["__archive_hash"]
                or business_key(receipt["family"], row) != value["__archive_key"]
            ):
                raise IntegrityError("Archive receipt payload differs from its copy proof")
        return values
