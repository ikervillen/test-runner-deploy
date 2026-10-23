"""Offline, lossless SQLite replacement. Never run concurrently with a worker.

Usage: python -m ingesta_delta_lake.maintenance --config source.json [--apply]
The default only inventories. --apply retains a hard-linked rollback copy until
--remove-backup is explicitly selected, after validating the replacement.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import uuid
from contextlib import ExitStack, closing, contextmanager
from dataclasses import replace
from pathlib import Path

import portalocker

from .common import IntegrityError
from .config import load_config
from .inspection_retention import InspectionArchive
from .retention import pack, unpack


def _sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _tune(db):
    # Bound each connection's cache instead of using SQLite's 2 MiB default for
    # multi-gigabyte controls. Mapped pages remain reclaimable by the kernel.
    db.execute("PRAGMA cache_size=-131072")
    db.execute("PRAGMA mmap_size=536870912")


def _check(db, *, full=True):
    pragma = "integrity_check" if full else "quick_check"
    if db.execute("PRAGMA " + pragma).fetchall() != [("ok",)]:
        raise IntegrityError("Control failed SQLite " + pragma)
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise IntegrityError("Control has invalid foreign keys")


def _hash_row(result, row):
    logical = [unpack(v) if isinstance(v, bytes) else v for v in row]
    result.update(json.dumps(logical, ensure_ascii=False, separators=(",", ":")).encode())
    result.update(b"\n")


def _row_hash(rows):
    result = hashlib.sha256()
    count = 0
    for row in rows:
        _hash_row(result, row)
        count += 1
    return count, result.hexdigest()


def _copy(source, target, config=None):
    """One row in memory; new pages contain no historical free space."""
    objects = source.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%' ORDER BY type='table' DESC,name"
    ).fetchall()
    archive = InspectionArchive(source, config)
    retained_groups, retained_hash = 0, hashlib.sha256()
    with target:
        for kind, name, sql in objects:
            if kind != "table":
                continue
            target.execute(sql)
            columns = [r[1] for r in source.execute(f'PRAGMA table_info("{name}")')]
            placeholders = ",".join("?" for _ in columns)
            for row in source.execute(f'SELECT * FROM "{name}" ORDER BY rowid'):
                if name == "inspection_groups":
                    if archive.omit(row):
                        continue
                    retained_groups += 1
                    _hash_row(retained_hash, row)
                values = list(row)
                confirmed = (
                    name == "batches"
                    and row[columns.index("confirmed_at")] is not None
                    and row[columns.index("state")] in {"COMPLETED", "PARTIAL"}
                ) or (
                    name.endswith("_intents")
                    and "state" in columns
                    and row[columns.index("state")] == "CONFIRMED"
                )
                if confirmed:
                    values = [
                        pack(v) if c.endswith("_json") else v
                        for c, v in zip(columns, row, strict=True)
                    ]
                target.execute(f'INSERT INTO "{name}" VALUES({placeholders})', values)
        for kind, _, sql in objects:
            if kind != "table":
                target.execute(sql)
    archival_evidence = archive.verify()
    _check(target)
    evidence = {}
    for kind, name, _ in objects:
        if kind == "table":
            before = (
                (retained_groups, retained_hash.hexdigest())
                if name == "inspection_groups"
                else _row_hash(source.execute(f'SELECT * FROM "{name}" ORDER BY rowid'))
            )
            after = _row_hash(target.execute(f'SELECT * FROM "{name}" ORDER BY rowid'))
            if before != after:
                raise IntegrityError(f"Logical contents changed during compaction: {name}")
            evidence[name] = {"rows": before[0], "sha256": before[1]}
    evidence["_inspection_archive"] = archival_evidence
    return evidence


@contextmanager
def control_locks(config):
    with ExitStack() as stack:
        for lock in (
            ".runtime.lock",
            ".writer.lock",
            f".writer-{config.plant}-{config.family}.lock",
        ):
            stack.enter_context(
                portalocker.Lock(str(config.silver_root / lock), mode="a", timeout=0)
            )
        yield


def replace_control(config, *, apply=False, remove_backup=False, reformulate=False):
    config.validate()
    path = config.control_path
    if not path.is_file():
        raise IntegrityError("Control does not exist; maintenance cannot create a generation")
    with control_locks(config):
        with closing(sqlite3.connect(path)) as db:
            _tune(db)
            report = {
                "status": "INVENTORY",
                "path": str(path),
                "bytes_before": path.stat().st_size,
                "pending": db.execute(
                    "SELECT count(*) FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
                ).fetchone()[0],
                "reformulate": reformulate,
                "control_id": db.execute(
                    "SELECT value FROM settings WHERE key='control_id'"
                ).fetchone()[0],
            }
            if not apply:
                return report
            if (
                reformulate
                and db.execute(
                    "SELECT 1 FROM settings WHERE key='control_format' AND value='3'"
                ).fetchone()
            ):
                backup = path.with_name(path.name + ".before-compaction")
                report.update(
                    status="ALREADY_FORMAT_3",
                    bytes_after=path.stat().st_size,
                    backup=str(backup),
                    backup_removed=False,
                )
                if remove_backup and backup.exists():
                    proof = json.loads(
                        path.with_name(path.name + ".compaction-report.json").read_text()
                    )
                    if (
                        proof.get("control_id") != report["control_id"]
                        or proof.get("tables", {}).get("format") != 3
                        or proof.get("bytes_before") != backup.stat().st_size
                    ):
                        raise IntegrityError("Rollback copy does not match the verified migration")
                    with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as old:
                        if (
                            dict(old.execute("SELECT key,value FROM settings")).get("control_id")
                            != report["control_id"]
                        ):
                            raise IntegrityError("Rollback copy belongs to another control")
                    _check(db, full=False)
                    backup.unlink()
                    _sync_directory(path.parent)
                    report["backup_removed"] = True
                return report
            # A clean checkpoint is necessary before retaining the old inode.
            if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                raise IntegrityError("Control still has active readers/writers")
            if not reformulate:
                _check(db, full=False)
            # Reformulation streams every retained source table, verifies the
            # transformed hashes and rebuilds all target indexes. Scanning the
            # discarded source indexes as well caused prohibitive duplicate I/O on
            # the server's legacy control. Invalid source pages still fail the
            # table reads; the replacement receives full integrity/FK checks.
        temporary = path.with_name(path.name + ".compact-" + uuid.uuid4().hex)
        backup = path.with_name(path.name + ".before-compaction")
        if backup.exists():
            raise IntegrityError("A rollback copy already exists; review the previous migration")
        try:
            original_permissions = path.stat()
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as source:
                _tune(source)
                source.execute("BEGIN")
                with closing(sqlite3.connect(temporary)) as target:
                    _tune(target)
                    target.execute("PRAGMA synchronous=FULL")
                    if reformulate:
                        from .control_migration import migrate

                        report["tables"] = migrate(source, target, config)
                    else:
                        report["tables"] = _copy(source, target, config)
                source.rollback()
            os.chmod(temporary, stat.S_IMODE(original_permissions.st_mode))
            if os.name != "nt":
                # Maintenance may be invoked by root; the replacement must still
                # belong to the unprivileged writer rather than the operator.
                os.chown(temporary, original_permissions.st_uid, original_permissions.st_gid)
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            # Fail safely if the filesystem cannot retain an atomic rollback link.
            os.link(path, backup)
            _sync_directory(path.parent)
            os.replace(temporary, path)
            _sync_directory(path.parent)
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as verified:
                _tune(verified)
                _check(verified)
            report.update(
                status="COMPACTED",
                bytes_after=path.stat().st_size,
                backup=str(backup),
                backup_removed=False,
                validation=(
                    "source-streamed-tables,target-integrity-check,foreign-keys,logical-hashes"
                    if reformulate
                    else "source-quick-check,target-integrity-check,foreign-keys,logical-hashes"
                ),
            )
            if remove_backup:
                backup.unlink()
                _sync_directory(path.parent)
                report["backup_removed"] = True
            path.with_name(path.name + ".compaction-report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            return report
        finally:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument(
        "--runtime", action="store_true", help="Use the existing Docker environment settings"
    )
    parser.add_argument("--family", help="Required with --runtime; never autodiscover destinations")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--remove-backup", action="store_true")
    parser.add_argument("--reformulate", action="store_true")
    args = parser.parse_args(argv)
    if args.runtime:
        from .runtime import marker, settings

        marker()
        if not args.family:
            parser.error("--runtime requires --family")
        config = replace(settings()[2], family=args.family)
    else:
        config = load_config(args.config)
    print(
        json.dumps(
            replace_control(
                config,
                apply=args.apply,
                remove_backup=args.remove_backup,
                reformulate=args.reformulate,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
