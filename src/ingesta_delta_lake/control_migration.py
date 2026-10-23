"""Streaming format-3 shadow copy. Source remains untouched until verified swap."""

import hashlib
import json
import os
import shutil

from .common import IntegrityError, canonical, now
from .compact_control import WORK_KEY, initialize, publish, summary
from .control_archive import Archive, logical
from .inspection_retention import InspectionArchive
from .retention import payload


def migrate(source, target, config):
    from .maintenance import _check, _hash_row, _row_hash

    settings = dict(source.execute("SELECT key,value FROM settings"))
    if settings.get("control_format") == "3":
        raise IntegrityError("Control already uses format 3")
    identity = settings["control_id"]
    root = config.control_root / "audit" / config.family / identity
    work = config.control_root / "work" / config.family / identity
    objects = source.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%' ORDER BY type='table' DESC,name"
    ).fetchall()
    groups = InspectionArchive(source, config)
    table_names = {name for kind, name, _ in objects if kind == "table"}
    queue = (
        "input_versions"
        if "input_versions" in table_names and settings.get("input_queue_migrated")
        else "capture_cache"
        if "capture_cache" in table_names and settings.get("capture_cache_seeded")
        else None
    )
    expectations, evidence, archives, references = {}, {}, {}, []
    reserve = int(os.environ.get("INGESTA_CONTROL_FREE_RESERVE_BYTES", str(1024**3)))
    if reserve < 64 * 1024**2:
        raise IntegrityError("Migration disk reserve is too small")
    visited = 0
    try:
        with target:
            for kind, name, sql in objects:
                if kind != "table":
                    continue
                target.execute(sql)
                columns = [r[1] for r in source.execute(f'PRAGMA table_info("{name}")')]
                expected, count = hashlib.sha256(), 0
                for row in source.execute(f'SELECT * FROM "{name}" ORDER BY rowid'):
                    if visited % 50000 == 0:
                        if shutil.disk_usage(config.control_root).free < reserve:
                            raise OSError(
                                "Insufficient free space for recoverable control migration"
                            )
                        print(
                            canonical(
                                {
                                    "event": "CONTROL_MIGRATION_PROGRESS",
                                    "table": name,
                                    "visited_rows": visited,
                                }
                            ),
                            file=__import__("sys").stderr,
                            flush=True,
                        )
                    visited += 1
                    values = list(row)
                    fields = dict(zip(columns, row, strict=True))
                    if name == "receipts" and queue:
                        descriptor = json.loads(fields["descriptor_json"])
                        if source.execute(
                            f'SELECT 1 FROM "{queue}" WHERE origin=? AND sha256=?',
                            (descriptor["origin"], descriptor["sha256"]),
                        ).fetchone():
                            if "capture_receipts" not in archives:
                                archives["capture_receipts"] = Archive(root, "capture-receipts")
                            archives["capture_receipts"].append(logical(row))
                            continue
                    if name == "inspection_groups" and groups.omit(row):
                        # Exact hashes are checked against archived preparations.
                        continue
                    if name.endswith("_intents") and fields.get("state") == "CONFIRMED":
                        archive = archives.setdefault(name, None)
                        if archive is None:
                            archive = archives[name] = Archive(root, name)
                        archive.append(logical(row))
                        continue
                    if name == "batches":
                        document = payload(fields["prepared_json"])
                        if fields["confirmed_at"] and fields["state"] in {"COMPLETED", "PARTIAL"}:
                            archive = archives.get("legacy_preparations")
                            if archive is None:
                                archive = archives["legacy_preparations"] = Archive(root, "legacy")
                            archive.append({"batch_id": fields["batch_id"], "prepared": document})
                            document = summary(document)
                            document["legacy_audit"] = True
                        elif fields["state"] in {"PREPARED", "WRITING", "UNKNOWN"}:
                            document = publish(work, document)
                            references.append((fields["batch_id"], document[WORK_KEY]))
                        values[columns.index("prepared_json")] = canonical(document)
                    _hash_row(expected, values)
                    count += 1
                    target.execute(
                        f'INSERT INTO "{name}" VALUES({",".join("?" for _ in columns)})', values
                    )
                expectations[name] = count, expected.hexdigest()
            for kind, _, sql in objects:
                if kind != "table":
                    target.execute(sql)
        evidence["inspection_groups"] = groups.verify()
        for name, archive in archives.items():
            evidence[name] = archive.finish()
        for name, expected in expectations.items():
            observed = _row_hash(target.execute(f'SELECT * FROM "{name}" ORDER BY rowid'))
            if observed != expected:
                raise IntegrityError("Migrated contents differ: " + name)
        initialize(target)
        with target:
            target.executemany("INSERT INTO prepared_artifacts VALUES(?,?)", references)
            target.execute(
                "INSERT INTO compact_receipts SELECT batch_id,confirmed_at,prepared_json "
                "FROM batches WHERE confirmed_at IS NOT NULL AND state IN ('COMPLETED','PARTIAL')"
            )
            for key, value in (
                ("control_format", "3"),
                ("control_migrated_at", now()),
                ("legacy_audit", canonical(evidence)),
            ):
                target.execute(
                    "INSERT INTO settings VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
        _check(target)
        return {
            "format": 3,
            "tables": {k: {"rows": v[0], "sha256": v[1]} for k, v in expectations.items()},
            "audit": evidence,
            "pending_artifacts": len(references),
        }
    except BaseException:
        for archive in archives.values():
            archive.abort()
        raise
