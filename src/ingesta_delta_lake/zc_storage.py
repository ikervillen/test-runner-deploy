"""Control por observación compuesta y append Delta con velocidades decimales exactas."""

import json
import os
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter

import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__
from . import cont_zc as zc
from .commit_protocol import application_transaction, delta_configuration
from .common import IntegrityError, canonical, now
from .control import Control
from .delta import DeltaStore
from .pipeline_rows import portable


class ZcControl(Control):
    def __init__(self, config):
        super().__init__(config)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS measurement_intents(
              operation_id TEXT NOT NULL, record_key TEXT NOT NULL, key_json TEXT NOT NULL,
              after_json TEXT NOT NULL, business_hash TEXT NOT NULL, state TEXT NOT NULL,
              delta_version INTEGER, PRIMARY KEY(operation_id,record_key));
        """)

    def cases(self):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT payload_json FROM issues WHERE status!='RESOLVED' ORDER BY issue_id"
            )
        ]

    def held(self):
        return {tuple(p["key"]) for p in self.cases() if "key" in p}

    def prepare(self, batch_id, operation_id, prepared):
        if self.prepare_compact(batch_id, operation_id, prepared):
            return
        with self.db:
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,'PREPARED',?,?,NULL,NULL)",
                (batch_id, operation_id, canonical(prepared), now()),
            )
            self.db.executemany(
                "INSERT INTO measurement_intents VALUES(?,?,?,?,?,'PREPARED',NULL)",
                [
                    (
                        operation_id,
                        r["record_key"],
                        canonical(zc.key(r)),
                        canonical(r),
                        zc.row_hash(r),
                    )
                    for r in prepared["inserts"]
                ],
            )

    coverage_seconds = 0.0

    def confirm(self, batch_id, version):
        with self.db:
            batch = self.batch(batch_id)
            prepared = self.prepared(batch)
            started = perf_counter()
            self._advance_mark(
                "observed_until", prepared.get("observed_marks", {}), transaction=False
            )
            self.coverage_seconds += perf_counter() - started
            self.db.execute(
                "UPDATE batches SET state=?,confirmed_at=?,delta_version=? WHERE batch_id=?",
                (prepared["outcome_state"], now(), version, batch_id),
            )
            self.db.execute(
                "UPDATE measurement_intents SET state='CONFIRMED',delta_version=? "
                "WHERE operation_id=?",
                (version, batch["operation_id"]),
            )
            self.db.execute(
                "INSERT INTO settings VALUES('last_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )


class ZcStore(DeltaStore):
    scope = None

    def __init__(self, config, control_id):
        super().__init__(config, control_id)
        self.timings = Counter()

    reuse_version = False

    @contextmanager
    def measure(self, name):
        started = perf_counter()
        try:
            yield
        finally:
            self.timings[name + "_seconds"] += perf_counter() - started

    def table(self):
        if not self.reuse_version:
            return super().table()
        if "_pinned_table" not in self.__dict__:
            self._pinned_table = super().table()
        else:
            self.timings["delta_versions_reused"] += 1
        return self._pinned_table

    def open_version(self):
        with self.measure("delta_open"):
            self.timings["delta_opens"] += 1
            return super().open_version()

    def transaction_history(self, table):
        cached = self.__dict__.get("_pinned_history")
        if self.reuse_version and cached and cached[0] is table:
            self.timings["transaction_histories_reused"] += 1
            return cached[1]
        with self.measure("transaction_log"):
            history = super().transaction_history(table)
        if self.reuse_version:
            self._pinned_history = (table, history)
        self.timings["commit_metadata_entries"] += len(history)
        return history

    def scan(self, table, *, filter, batch_size):
        from .pipeline_read import AcceptedRead

        with self.measure("file_enumeration"):
            dataset = table.to_pyarrow_dataset(
                parquet_read_options=ds.ParquetReadOptions(dictionary_columns=["origen_fenix"])
            )
        if (
            not isinstance(table, AcceptedRead)
            and os.environ.get("INGESTA_ZC_PROFILE_PRUNING") == "1"
        ):
            with self.measure("file_pruning"):
                self.timings["candidate_fragments"] += sum(
                    1 for _ in dataset.get_fragments(filter=filter)
                )
        decoded = pa.schema(
            [
                field.with_type(field.type.value_type)
                if pa.types.is_dictionary(field.type)
                else field
                for field in dataset.schema
            ]
        )
        if decoded != zc.SCHEMA:
            raise IntegrityError("Esquema CONT_ZC incompatible")
        before = self.disk_read_bytes()
        with self.measure("parquet_scan_and_decode"):
            arrow = dataset.scanner(
                filter=filter, batch_size=batch_size, use_threads=False
            ).to_table()
        after = self.disk_read_bytes()
        if before is not None and after is not None:
            self.timings["process_physical_read_bytes"] += max(0, after - before)
        with self.measure("arrow_cast"):
            return arrow.cast(zc.SCHEMA)

    @staticmethod
    def disk_read_bytes():
        try:
            lines = Path("/proc/self/io").read_text().splitlines()
            return next(int(row.split()[1]) for row in lines if row.startswith("read_bytes:"))
        except (OSError, StopIteration, ValueError):
            return None

    def set_scope(self, scope):
        self.scope = scope

    def _predicate(self, prefix=""):
        clauses = []
        for origin, stamps in self.scope:
            quoted = origin.replace("'", "''")
            dates = [datetime.fromisoformat(stamp).isoformat(sep=" ") for stamp in stamps]
            literals = ", ".join(f"TIMESTAMP '{date}'" for date in dates)
            clauses.append(
                f"({prefix}origen_fenix = '{quoted}' AND {prefix}\"FECHA\" IN ({literals}))"
            )
        return " OR ".join(clauses) or "false"

    @property
    def cdf_predicate(self):
        return self._predicate() if self.scope is not None else None

    def read(self, table=None):
        start = perf_counter()
        try:
            return self._read(table)
        finally:
            self.timings["read_seconds"] += perf_counter() - start

    def _read(self, table=None):
        table = self.read_view(table if table is not None else self.table())
        if table is None:
            return None, {}
        if self.scope is None:
            arrow = table.to_pyarrow_table()
        else:
            predicate = ds.scalar(False)
            for origin, stamps in self.scope:
                dates = pa.array(
                    [datetime.fromisoformat(stamp) for stamp in stamps], type=pa.timestamp("us")
                )
                predicate |= (ds.field("origen_fenix").cast(pa.string()) == origin) & ds.field(
                    "FECHA"
                ).isin(dates)
            arrow = self.scan(table, filter=predicate, batch_size=self.config.read_batch_rows)
        if arrow.schema != zc.SCHEMA:
            raise IntegrityError("Esquema CONT_ZC incompatible")
        self.timings["rows_read"] += arrow.num_rows
        rows = {}
        with self.measure("arrow_to_python"):
            decoded_rows = arrow.to_pylist()
        for row in decoded_rows:
            if any(row[f] is None for f in zc.SCHEMA.names):
                raise IntegrityError("CONT_ZC contiene un campo obligatorio nulo")
            if row["FECHA"].microsecond or row["FECHA"].tzinfo is not None:
                raise IntegrityError("Precisión/zona CONT_ZC inesperadas")
            row["FECHA"] = row["FECHA"].isoformat(timespec="seconds")
            for f in zc.SPEEDS:
                row[f] = zc.speed(format(row[f], "f"))
            if (
                not row["origen_fenix"]
                or row["origen_fenix"] != row["origen_fenix"].strip()
                or row["SALIDA_MAQ_IS"] not in (0, 1)
                or any(row[f] < 0 for f in zc.FIELDS if f not in {"MAQ_IS", "FECHA", *zc.SPEEDS})
            ):
                raise IntegrityError("Contenido CONT_ZC publicado inválido")
            identity = zc.key(row)
            if identity in rows:
                raise IntegrityError("Observación CONT_ZC duplicada")
            rows[identity] = row
        return table.version(), rows

    def verify_commit(self, table, version, prepared):
        with self.measure("commit_cdf"):
            self._verify_commit(table, version, prepared)

    def _verify_commit(self, table, version, prepared):
        expected = {zc.key(r): zc.row_hash(r) for r in prepared["inserts"]}
        changes, preimages = {}, set()
        for batch in pa.RecordBatchReader.from_stream(table.load_cdf(version, version)):
            for event in batch.to_pylist():
                row = portable({f: event[f] for f in zc.SCHEMA.names})
                key, kind = zc.key(row), event["_change_type"]
                if key not in expected:
                    raise IntegrityError(
                        "CONT_ZC commit changed an observation outside its intention"
                    )
                if kind == "update_preimage" and key not in preimages:
                    preimages.add(key)
                elif kind in {"insert", "update_postimage"} and key not in changes:
                    changes[key] = (kind, zc.row_hash(row))
                else:
                    raise IntegrityError("Unexpected or duplicated CONT_ZC change in Delta")
        if {k: val for k, (_, val) in changes.items()} != expected or preimages != {
            k for k, (kind, _) in changes.items() if kind == "update_postimage"
        }:
            raise IntegrityError("CONT_ZC commit changes differ from its intention")

    def insert(self, rows, operation, before_version):
        try:
            return self._insert(rows, operation, before_version)
        finally:
            self.__dict__.pop("_pinned_table", None)
            self.__dict__.pop("_pinned_history", None)

    def _insert(self, rows, operation, before_version):
        table = self.table()
        version = table.version() if table is not None else None
        if version != before_version or not rows:
            raise IntegrityError("Snapshot CONT_ZC cambiado o inserción vacía")
        keys = [zc.key(r) for r in rows]
        if len(set(keys)) != len(keys):
            raise IntegrityError("La inserción duplicaría CONT_ZC")
        # Update only the accepted minute. Rewriting the complete logical view
        # would rematerialize retired rows and reset unrelated archive revisions.
        values = []
        for row in rows:
            item = {f: row[f] for f in zc.SCHEMA.names}
            item["FECHA"] = datetime.fromisoformat(item["FECHA"])
            for f in zc.SPEEDS:
                item[f] = Decimal(zc.speed(item[f]))
            values.append(item)
        arrow = pa.Table.from_pylist(values, schema=zc.SCHEMA)
        props = CommitProperties(
            custom_metadata={
                "ingesta_operation_id": operation,
                "ingesta_control_id": self.control_id,
                "ingesta_plant": self.plant,
                "ingesta_contract": zc.CONTRACT,
                "ingesta_release": __version__,
                "ingesta_timestamp_policy": "local_naive_test_v1",
            },
            app_transactions=application_transaction(self.control_id, before_version),
            max_commit_retries=0,
        )
        start = perf_counter()
        if table is None:
            write_deltalake(
                str(self.path),
                arrow,
                configuration=delta_configuration(self.config),
                commit_properties=props,
            )
        else:
            predicate = (
                "t.origen_fenix = s.origen_fenix AND t.MAQ_IS = s.MAQ_IS AND t.FECHA = s.FECHA"
            )
            if self.scope is not None:
                predicate += f" AND ({self._predicate('t.')})"
            metrics = (
                table.merge(
                    arrow,
                    predicate,
                    source_alias="s",
                    target_alias="t",
                    commit_properties=props,
                )
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute()
            )
            if (
                metrics["num_target_rows_inserted"] + metrics["num_target_rows_updated"]
                != len(rows)
                or metrics["num_target_rows_deleted"]
            ):
                raise IntegrityError("CONT_ZC merge differs from its accepted minute intentions")
        self.timings["commit_delta_seconds"] += perf_counter() - start
