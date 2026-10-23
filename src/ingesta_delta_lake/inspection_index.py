"""Rebuild the current MIA index from a pinned Silver version with bounded RAM."""

from .common import IntegrityError, digest
from .mia_scope import group_holds


def rebuild(control, store):
    if control.pending():
        raise IntegrityError("Recover pending operations before rebuilding the inspection index")
    table = store.table()
    version = table.version() if table else None
    if control.setting("last_version") != str(version):
        raise IntegrityError("Inspection rebuild requires the confirmed Delta version")
    db = control.db
    db.execute("PRAGMA temp_store=FILE")
    db.execute("DROP TABLE IF EXISTS temp.inspection_rebuild")
    db.execute(
        "CREATE TEMP TABLE inspection_rebuild("
        "group_id TEXT,origin TEXT,inspected INTEGER,n INTEGER,"
        "PRIMARY KEY(group_id,inspected))"
    )
    rows = 0
    try:
        if table:
            for batch in table.to_pyarrow_dataset().scanner(batch_size=4096).to_batches():
                values = []
                for row in batch.to_pylist():
                    stamp = row["FECHA"]
                    if stamp.tzinfo or stamp.microsecond or row["INSPECC"] < 0:
                        raise IntegrityError("Unexpected inspection data during index rebuild")
                    row["FECHA"] = stamp.isoformat(timespec="seconds")
                    values.append(
                        (digest(control.model.group(row)), row["origen_fenix"], row["INSPECC"])
                    )
                db.executemany(
                    "INSERT INTO inspection_rebuild VALUES(?,?,?,1) "
                    "ON CONFLICT(group_id,inspected) DO UPDATE SET n=n+1",
                    values,
                )
                rows += len(values)
        latest = store.table()
        if (latest.version() if latest else None) != version:
            raise IntegrityError("Delta changed during inspection index rebuild")
        # Move only after the complete source scan. Failure rolls the old index back.
        with db:
            db.execute("DELETE FROM inspection_current")
            clause = "count(*)=1" + (
                " AND sum(n)=1" if control.config.family == "LISTA_RECH" else ""
            )
            db.execute(
                "INSERT INTO inspection_current SELECT group_id,origin,"
                "json_object('accepted_inspection_values',json_group_array(inspected)),"
                f"CASE WHEN {clause} THEN 'RESOLVED' ELSE 'REVIEW' END,"
                f"CASE WHEN {clause} THEN min(inspected) ELSE NULL END,sum(n) "
                "FROM (SELECT * FROM inspection_rebuild ORDER BY group_id,inspected) "
                "GROUP BY group_id"
            )
            control.update_inspection_holds(group_holds(control.cases()))
            db.execute(
                "INSERT INTO settings VALUES('inspection_index_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )
            if (
                db.execute("SELECT coalesce(sum(row_count),0) FROM inspection_current").fetchone()[
                    0
                ]
                != rows
            ):
                raise IntegrityError("Inspection rebuild row coverage differs")
        return {"status": "INDEX_REBUILT", "delta_version": version, "rows": rows}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute("DROP TABLE IF EXISTS temp.inspection_rebuild")


def execute(config):
    from contextlib import closing

    from .defects_control import DefectsControl
    from .defects_storage import DefectsStore
    from .maintenance import control_locks
    from .rejections_storage import RejectionsControl, RejectionsStore

    config.validate()
    if config.family not in {"LISTA_RECH", "LISTA_DEFECTOS"} or not config.control_path.is_file():
        raise IntegrityError("Index rebuild requires an existing MIA control")
    klass, store = (
        (RejectionsControl, RejectionsStore)
        if config.family == "LISTA_RECH"
        else (DefectsControl, DefectsStore)
    )
    with control_locks(config), closing(klass(config)) as control:
        if not control.compact:
            raise IntegrityError("Reformulate control before rebuilding the minimal index")
        return rebuild(control, store(config, control.control_id))
