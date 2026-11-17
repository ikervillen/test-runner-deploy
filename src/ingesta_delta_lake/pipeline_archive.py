"""History-owned, resumable archival of confirmed live row versions."""

import json
import os
import sqlite3
from pathlib import Path

import portalocker
import pyarrow as pa
import pyarrow.parquet as pq
from deltalake import CommitProperties, DeltaTable, Transaction, write_deltalake

from .common import IntegrityError, digest
from .contracts import CONTRACTS
from .pipeline_publications import confirmation_age, immutable_json, schema_hash
from .pipeline_rows import business_key, payload_hash
from .runtime import atomic_json

META = pa.schema(
    [
        pa.field("__archive_id", pa.string(), nullable=False),
        pa.field("__archive_key", pa.string(), nullable=False),
        pa.field("__archive_hash", pa.string(), nullable=False),
        pa.field("__archive_source_table", pa.string(), nullable=False),
        pa.field("__archive_source_version", pa.int64(), nullable=False),
        pa.field("__archive_confirmed_at", pa.string(), nullable=False),
        pa.field("__archive_change_type", pa.string(), nullable=False),
    ]
)


class Archive:
    def __init__(self, storage, *, identity):
        self.storage, self.identity = storage, identity
        storage.checkpoint.mkdir(parents=True, exist_ok=True)
        self.root = storage.exchange / "archive"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(storage.checkpoint / "archive.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS identity(value TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS seen(row_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS cursors(
                source TEXT PRIMARY KEY, row_offset INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS completed(
                source_table TEXT PRIMARY KEY, version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
              job_id TEXT PRIMARY KEY, family TEXT NOT NULL, before_version INTEGER,
              artifact TEXT NOT NULL, source TEXT NOT NULL, next_offset INTEGER NOT NULL,
              state TEXT NOT NULL, archive_version INTEGER, source_publication TEXT NOT NULL);
        """)
        previous = self.db.execute("SELECT value FROM identity").fetchone()
        if previous is not None and previous[0] != identity:
            self.db.close()
            raise IntegrityError("Archive checkpoint belongs to another writer")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO identity VALUES(?)", (identity,))

    def close(self):
        self.db.close()

    def target(self, family):
        return self.storage.silver / "archive" / family.lower()

    def recover(self):
        for job in self.db.execute(
            "SELECT * FROM jobs WHERE state!='PUBLISHED' ORDER BY rowid"
        ).fetchall():
            self._commit(dict(job))

    def copy(self, snapshot, family, *, policy, technical_now):
        if snapshot.manifest["lane"] != "live":
            raise IntegrityError("The archive accepts confirmed live publications only")
        with portalocker.Lock(str(self.storage.checkpoint / "archive.lock"), mode="a", timeout=0):
            self.recover()
            ref = snapshot.manifest["tables"][family]
            if confirmation_age(ref, technical_now) < policy.retention_seconds:
                return {"status": "NOT_DUE", "rows": 0}
            completed = self.db.execute(
                "SELECT version FROM completed WHERE source_table=?", (ref["table_id"],)
            ).fetchone()
            start = completed[0] + 1 if completed else 0
            if start > ref["version"]:
                return {"status": "UNCHANGED", "rows": 0, "complete": True}
            source = digest([ref["table_id"], start, ref["version"]])
            cursor = self.db.execute(
                "SELECT row_offset FROM cursors WHERE source=?", (source,)
            ).fetchone()
            offset = cursor[0] if cursor else 0
            rows, scanned = [], 0
            finished = True
            table = snapshot.table(family)
            if pa.schema(table.schema().to_arrow()) != CONTRACTS[family].schema:
                raise IntegrityError("Archive source differs from its business contract")
            if table.metadata().configuration.get("delta.enableChangeDataFeed") != "true":
                raise IntegrityError("Archival requires CDF from the first source version")
            maintenance = {
                h["version"]
                for h in table.history()
                if h.get("ingesta_maintenance") == "archive-retirement"
            }
            reader = pa.RecordBatchReader.from_stream(table.load_cdf(start, ref["version"]))
            chunks = (
                batch.slice(index, policy.archive_batch_rows)
                for batch in reader
                for index in range(0, batch.num_rows, policy.archive_batch_rows)
            )
            for batch in chunks:
                values = batch.to_pylist()
                if scanned + len(values) <= offset:
                    scanned += len(values)
                    continue
                batch_start = scanned
                skip = max(0, offset - batch_start)
                for index, event in enumerate(values[skip:], start=skip):
                    scanned = batch_start + index + 1
                    change, version = event["_change_type"], event["_commit_version"]
                    row = {k: v for k, v in event.items() if k in CONTRACTS[family].schema.names}
                    key, payload = business_key(family, row), payload_hash(row)
                    row_id = digest([ref["table_id"], key, payload, version, change])
                    if (
                        version not in maintenance
                        and not self.db.execute(
                            "SELECT 1 FROM seen WHERE row_id=?", (row_id,)
                        ).fetchone()
                    ):
                        rows.append(
                            {
                                **row,
                                "__archive_id": row_id,
                                "__archive_key": key,
                                "__archive_hash": payload,
                                "__archive_source_table": ref["table_id"],
                                "__archive_source_version": version,
                                "__archive_confirmed_at": ref["confirmed_at"],
                                "__archive_change_type": change,
                            }
                        )
                    if scanned - offset >= policy.archive_batch_rows:
                        finished = False
                        break
                if not finished:
                    break
            if not rows:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (source, scanned))
                    if finished:
                        self.db.execute(
                            "INSERT OR REPLACE INTO completed VALUES(?,?)",
                            (ref["table_id"], ref["version"]),
                        )
                if finished:
                    self._publish()
                return {"status": "UNCHANGED", "rows": 0, "complete": finished}
            target = self.target(family)
            before = (
                DeltaTable(str(target)).version() if DeltaTable.is_deltatable(str(target)) else None
            )
            job_id = digest([source, offset, [r["__archive_id"] for r in rows]])
            artifact = self.root / "work" / (job_id + ".parquet")
            artifact.parent.mkdir(exist_ok=True)
            schema = pa.schema([*pa.schema(snapshot.table(family).schema().to_arrow()), *META])
            if artifact.exists():
                existing = pq.read_table(artifact)
                if existing.to_pylist() != rows:
                    raise IntegrityError("Archive work identity was reused")
            else:
                pq.write_table(pa.Table.from_pylist(rows, schema=schema), artifact)
                with artifact.open("r+b") as stream:
                    os.fsync(stream.fileno())
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?,'PREPARED',NULL,?)",
                    (
                        job_id,
                        family,
                        before,
                        str(artifact),
                        source,
                        scanned,
                        snapshot.manifest["publication_id"],
                    ),
                )
            job = dict(self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())
            receipt = self._commit(job)
            if finished:
                with self.db:
                    self.db.execute(
                        "INSERT OR REPLACE INTO completed VALUES(?,?)",
                        (ref["table_id"], ref["version"]),
                    )
                self._publish()
            return {
                "status": "ARCHIVED",
                "rows": len(rows),
                "complete": finished,
                "receipt": receipt,
            }

    def _commit(self, job):
        target = self.target(job["family"])
        expected_artifact = (self.root / "work" / (job["job_id"] + ".parquet")).resolve()
        if Path(job["artifact"]).resolve() != expected_artifact:
            raise IntegrityError("Archive work artifact escapes its store")
        data = pq.read_table(job["artifact"])
        for value in data.to_pylist():
            row = {k: v for k, v in value.items() if k not in META.names}
            key, payload = business_key(job["family"], row), payload_hash(row)
            if (
                key != value["__archive_key"]
                or payload != value["__archive_hash"]
                or (
                    digest(
                        [
                            value["__archive_source_table"],
                            key,
                            payload,
                            value["__archive_source_version"],
                            value["__archive_change_type"],
                        ]
                    )
                    != value["__archive_id"]
                )
            ):
                raise IntegrityError("Prepared archive artifact differs from its evidence")
        table = DeltaTable(str(target)) if DeltaTable.is_deltatable(str(target)) else None
        if table and any(h.get("archive_owner") != self.identity for h in table.history()):
            raise IntegrityError("Archive table belongs to another writer")
        commits = (
            [h for h in table.history() if h.get("archive_operation_id") == job["job_id"]]
            if table
            else []
        )
        expected = 0 if job["before_version"] is None else job["before_version"] + 1
        if not commits:
            if (table.version() if table else None) != job["before_version"]:
                raise IntegrityError("Archive writer changed before its pending operation")
            write_deltalake(
                table if table else str(target),
                data,
                mode="append" if table else "error",
                configuration={"delta.enableExpiredLogCleanup": "false"},
                commit_properties=CommitProperties(
                    custom_metadata={
                        "archive_operation_id": job["job_id"],
                        "archive_owner": self.identity,
                    },
                    app_transactions=[Transaction("archive:" + self.identity, expected)],
                    max_commit_retries=0,
                ),
            )
            table = DeltaTable(str(target))
        elif len(commits) != 1 or commits[0]["version"] != expected:
            raise IntegrityError("Archive commit cannot be reconciled")
        import pyarrow.dataset as ds

        ids = data["__archive_id"].to_pylist()
        accepted = table.to_pyarrow_dataset().to_table(filter=ds.field("__archive_id").isin(ids))
        expected_rows = {r["__archive_id"]: r for r in data.to_pylist()}
        actual = {r["__archive_id"]: r for r in accepted.to_pylist()}
        if actual != expected_rows or len(actual) != accepted.num_rows:
            raise IntegrityError("Archive result differs from its prepared rows")
        receipt = {
            "schema_id": "fenix-archive-receipt-v1",
            "operation_id": job["job_id"],
            "owner": self.identity,
            "family": job["family"],
            "source_publication": job["source_publication"],
            "archive_uri": target.relative_to(self.storage.silver).as_posix(),
            "archive_table_id": table.metadata().id,
            "archive_version": expected,
            "schema_id_hash": schema_hash(pa.schema(table.schema().to_arrow())),
            "rows": [{k: r[k] for k in META.names} for r in data.to_pylist()],
        }
        receipts = self.root / "receipts"
        receipts.mkdir(exist_ok=True)
        immutable_json(receipts / (job["job_id"] + ".json"), receipt)
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO seen VALUES(?)", [(identity,) for identity in ids]
            )
            self.db.execute(
                "UPDATE jobs SET state='CONFIRMED',archive_version=? WHERE job_id=?",
                (expected, job["job_id"]),
            )
            self.db.execute(
                "INSERT OR REPLACE INTO cursors VALUES(?,?)", (job["source"], job["next_offset"])
            )
        self._publish(job["family"], receipt)
        with self.db:
            self.db.execute("UPDATE jobs SET state='PUBLISHED' WHERE job_id=?", (job["job_id"],))
        artifact = Path(job["artifact"]).resolve()
        if not artifact.is_relative_to((self.root / "work").resolve()):
            raise IntegrityError("Archive work artifact escapes its store")
        artifact.unlink(missing_ok=True)
        return receipt

    def _publish(self, family=None, receipt=None):
        path = self.root / "current.json"
        previous = json.loads(path.read_text()) if path.exists() else {"tables": {}}
        if path.exists() and (
            previous.get("owner") != self.identity
            or digest({k: v for k, v in previous.items() if k != "publication_id"})
            != previous.get("publication_id")
        ):
            raise IntegrityError("Archive publication belongs to another writer or was altered")
        tables = dict(previous["tables"])
        if receipt is not None:
            tables[family] = {
                k: receipt[k]
                for k in ("archive_uri", "archive_table_id", "archive_version", "schema_id_hash")
            }
        value = {
            "schema_id": "fenix-archive-snapshot-v1",
            "owner": self.identity,
            "tables": tables,
            "completed": dict(self.db.execute("SELECT source_table,version FROM completed")),
        }
        publication = {**value, "publication_id": digest(value)}
        immutable_json(self.root / (publication["publication_id"] + ".json"), publication)
        atomic_json(path, publication)
