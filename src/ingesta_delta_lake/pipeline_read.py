"""Pinned accepted archive context for validation after physical live retirement."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.dataset as ds

from .common import IntegrityError
from .pipeline_view import ArchiveSnapshot, SilverView


class AcceptedArchive(ArchiveSnapshot):
    def __init__(self, view):
        self.view = view
        super().__init__(
            SimpleNamespace(silver=view.context["root"]), manifest_path=view.context["manifest"]
        )
        # A native owner's validator must not silently adopt another owner's decisions.
        self.manifest = {**self.manifest, "rejections": {}}

    def records(self, *args, **kwargs):
        local = self.view.local
        if self.view.context["lane"] == "live":
            if local is None:
                return
            own = (ds.field("__archive_source_table") == local.metadata().id) & (
                ds.field("__archive_source_version") <= local.version()
            )
            selected = kwargs.get("expression")
            kwargs["expression"] = own if selected is None else own & selected
        for record in super().records(*args, **kwargs):
            local = self.view.local
            if self.view.context["lane"] == "live" and (
                local is None
                or record["source_table"] != local.metadata().id
                or record["source_version"] > local.version()
            ):
                continue
            yield record


class AcceptedRead:
    def __init__(self, store, table):
        self.store, self.local = store, table
        self.context = store.read_context
        self.schema = store.contract.schema

    def version(self):
        return self.local.version() if self.local is not None else None

    def to_pyarrow_dataset(self, **kwargs):
        return AcceptedDataset(self, kwargs)

    def to_pyarrow_table(self):
        return self.to_pyarrow_dataset().scanner().to_table()


class AcceptedDataset:
    def __init__(self, view, options):
        self.view, self.options, self.schema = view, options, view.schema

    def scanner(self, *, filter=None, batch_size=None, **kwargs):
        return AcceptedScanner(self, filter, batch_size or self.view.store.config.read_batch_rows)

    def to_table(self, *, filter=None, **kwargs):
        return self.scanner(filter=filter).to_table()

    def count_rows(self):
        return sum(batch.num_rows for batch in self.scanner().to_batches())


class AcceptedScanner:
    def __init__(self, dataset, expression, batch_size):
        self.dataset, self.expression, self.batch_size = dataset, expression, batch_size

    def to_batches(self):
        view = self.dataset.view
        family, schema = view.store.config.family, view.schema
        measure = getattr(view.store, "measure", lambda _: nullcontext())
        with measure("archive_metadata"):
            archive = AcceptedArchive(view)
        snapshot = None
        if view.local is not None:

            def batches(_family, *, batch_size, expression=None):
                with measure("local_file_enumeration"):
                    dataset = view.local.to_pyarrow_dataset(**self.dataset.options)
                with measure("local_parquet_scan_and_decode"):
                    scanner = dataset.scanner(
                        filter=expression, batch_size=batch_size, use_threads=False
                    )
                    yield from scanner.to_batches()

            snapshot = SimpleNamespace(
                manifest={
                    "tables": {
                        family: {
                            "table_id": view.local.metadata().id,
                            "version": view.local.version(),
                            "rejected_hashes": sorted(
                                {
                                    value
                                    for commit in view.store.transaction_history(view.local)
                                    if commit["version"] <= view.local.version()
                                    for value in json.loads(
                                        commit.get("ingesta_review_rejected_hashes", "[]")
                                    )
                                }
                            ),
                        }
                    }
                },
                table=lambda _: view.local,
                batches=batches,
                transaction_history=lambda: view.store.transaction_history(view.local),
            )
        combined = SilverView(archive=archive, **{view.context["lane"]: snapshot})
        result = combined.rows(
            family,
            max_rows=view.store.config.read_max_rows,
            batch_size=self.batch_size,
            expression=self.expression,
            cdf_predicate=getattr(view.store, "cdf_predicate", None),
            measure=measure,
        )
        if result.conflicts:
            raise IntegrityError("Accepted archive context has unresolved cross-lane conflicts")
        yield from pa.Table.from_pylist(result.rows, schema=schema).to_batches(
            max_chunksize=self.batch_size
        )

    def to_table(self):
        return pa.Table.from_batches(list(self.to_batches()), schema=self.dataset.schema)
