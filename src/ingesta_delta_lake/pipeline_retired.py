"""Owner-local fingerprints for accepted rows residing in the verified archive."""

import json

import pyarrow as pa

from .common import IntegrityError
from .contracts import CONTRACTS
from .pipeline_rows import business_key, review_hash


def initialize(control):
    control.db.execute(
        "CREATE TABLE IF NOT EXISTS retired_current("
        "key_hash TEXT PRIMARY KEY,row_hash TEXT NOT NULL)"
    )


def synchronize(control, table):
    if not control.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='retired_current'"
    ).fetchone():
        return
    start = int(control.setting("retirement_index_version") or table.version()) + 1
    if start > table.version():
        return
    scanned = 0
    maintenance = {
        commit["version"]
        for commit in table.history()
        if commit.get("ingesta_maintenance") == "archive-retirement"
    }
    control.db.execute("SAVEPOINT reconcile_retired")
    try:
        for batch in pa.RecordBatchReader.from_stream(table.load_cdf(start, table.version())):
            scanned += batch.num_rows
            if scanned > control.config.read_max_rows:
                raise IntegrityError("Retired index reconciliation exceeds its explicit row budget")
            for event in batch.to_pylist():
                if event["_commit_version"] in maintenance or event["_change_type"] not in {
                    "insert",
                    "update_postimage",
                }:
                    continue
                row = {
                    field: event[field] for field in CONTRACTS[control.config.family].schema.names
                }
                control.db.execute(
                    "DELETE FROM retired_current WHERE key_hash=?",
                    (business_key(control.config.family, row),),
                )
        for commit in table.history():
            if start <= commit["version"] <= table.version():
                control.db.executemany(
                    "DELETE FROM retired_current WHERE row_hash=?",
                    [
                        (value,)
                        for value in json.loads(commit.get("ingesta_review_rejected_hashes", "[]"))
                    ],
                )
        control.db.execute(
            "INSERT INTO settings VALUES('retirement_index_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(table.version()),),
        )

    except BaseException:
        control.db.execute("ROLLBACK TO reconcile_retired")
        control.db.execute("RELEASE reconcile_retired")
        raise
    else:
        control.db.execute("RELEASE reconcile_retired")


def logical_rows(control, table):
    physical = (
        sum(pa.table(table.get_add_actions())["num_records"].to_pylist())
        if table is not None
        else 0
    )
    if (
        table is None
        or not control.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='retired_current'"
        ).fetchone()
    ):
        return physical
    synchronize(control, table)
    return physical + control.db.execute("SELECT COUNT(*) FROM retired_current").fetchone()[0]


def register(control, rows, version):
    initialize(control)
    control.db.executemany(
        "INSERT OR REPLACE INTO retired_current VALUES(?,?)",
        [
            (business_key(control.config.family, row), review_hash(control.config.family, row))
            for row in rows
        ],
    )
    control.db.execute(
        "INSERT INTO settings VALUES('retirement_index_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(version),),
    )
