"""Bound a Gold run to an explicit local period without modifying confirmed Silver."""

from dataclasses import dataclass, replace
from datetime import datetime

import pyarrow as pa
import pyarrow.dataset as ds

from .attribution import KEY_FIELDS, source_id
from .common import IntegrityError
from .context import stamp


@dataclass(frozen=True)
class LiveScope:
    start: datetime
    end: datetime | None = None

    def __post_init__(self):
        if self.start.tzinfo is not None or (self.end is not None and self.end.tzinfo is not None):
            raise IntegrityError("Gold source scope must use the local industrial time")
        if self.end is not None and self.end <= self.start:
            raise IntegrityError("Gold cutoff must follow the scoped start")

    def coverage_view(self, snapshot):
        rows = []
        for row in snapshot.manifest["coverage"]:
            if row["source"] in KEY_FIELDS:
                row = dict(row)
                row["covered_from"] = max(self.start, stamp(row.get("covered_from")) or self.start)
                end = stamp(row.get("covered_until"))
                if self.end is not None and (end is None or end > self.end):
                    end = row["covered_until"] = self.end
                if end is not None and end <= row["covered_from"]:
                    row.update(estado="WAITING_INPUT", reason_code="OUTSIDE_GOLD_SOURCE_SCOPE")
            rows.append(row)
        return replace(snapshot, manifest={**snapshot.manifest, "coverage": rows})

    def batches(self, snapshot, family, max_rows, columns=None):
        timestamp = pa.scalar(self.start, type=pa.timestamp("us"))
        if family == "LISTA_AR":
            end = ds.field("FECHA_OFF")
            predicate = end.is_null() | (end > timestamp)
            if self.end is not None:
                predicate &= ds.field("FECHA_ON") <= pa.scalar(self.end, type=pa.timestamp("us"))
        else:
            predicate = ds.field("FECHA") > timestamp
            if self.end is not None:
                predicate &= ds.field("FECHA") <= pa.scalar(self.end, type=pa.timestamp("us"))
        count = 0
        dataset = snapshot.table(family).to_pyarrow_dataset()
        for batch in dataset.scanner(
            columns=columns, filter=predicate, batch_size=8192, use_threads=False
        ).to_batches():
            count += batch.num_rows
            if count > max_rows:
                raise IntegrityError(f"{family}: Gold source period exceeds row budget")
            if batch.num_rows:
                yield batch

    def assignments(self, snapshot, max_rows, max_total_rows):
        source_ids = set()
        selected_rows = 0
        for family, fields in KEY_FIELDS.items():
            for batch in self.batches(snapshot, family, max_rows, ["origen_fenix", *fields]):
                selected_rows += batch.num_rows
                if selected_rows > max_total_rows:
                    raise IntegrityError("Gold source period exceeds the total fact budget")
                source_ids.update(source_id(family, row) for row in batch.to_pylist())

        result = []
        dataset = snapshot.table("observation_assignments").to_pyarrow_dataset()
        for batch in dataset.scanner(batch_size=8192, use_threads=False).to_batches():
            ids = batch.column(batch.schema.get_field_index("source_id"))
            selected = [value in source_ids for value in ids.to_pylist()]
            if any(selected):
                result.extend(batch.filter(pa.array(selected, type=pa.bool_())).to_pylist())
                if len(result) > max_rows:
                    raise IntegrityError(
                        "Scoped observation assignments exceed the Gold row budget"
                    )
        return result
