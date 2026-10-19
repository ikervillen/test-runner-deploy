"""Snapshots de diccionarios confirmados y MERGE AR con comparación explícita de nulos."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime

import pyarrow as pa
from deltalake import write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__
from . import lista_ar as ar
from .common import IntegrityError, canonical, digest
from .delta import DeltaStore

CODE_FIELDS = {
    "SIP_DEFECTO": "codigo_aviso_rechazo",
    "SIP_DEFECTO_STATUS": "codigo_status",
    "SIP_DEFECTO_AR": "codigo_tipo_ar",
}


def load_references(config):
    references, manifest = {}, {}
    for family, code_field in CODE_FIELDS.items():
        source = replace(config, family=family)
        rows, cases, version, control_id = {}, [], None, None
        if not source.control_path.exists():
            if source.table_path.exists():
                raise IntegrityError(f"Falta el control de {family}")
        else:
            with closing(
                sqlite3.connect(source.control_path.as_uri() + "?mode=ro", uri=True)
            ) as db:
                db.execute("BEGIN")
                settings = dict(db.execute("SELECT key,value FROM settings"))
                expected = canonical(
                    {
                        "plant": source.plant,
                        "silver_root": str(source.silver_root),
                        "bronze_root": str(source.bronze_root),
                        "contract": source.contract.name,
                    }
                )
                if settings.get("binding") != expected:
                    raise IntegrityError(f"Control de {family} de otro ámbito")
                if db.execute(
                    "SELECT COUNT(*) FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
                ).fetchone()[0]:
                    raise IntegrityError(f"Recuperar primero {family}")
                control_id = settings["control_id"]
                version, rows = DeltaStore(source, control_id).read()
                if settings.get("last_version", "None") != str(version):
                    raise IntegrityError(f"Snapshot {family} sin confirmar")
                cases = [
                    {"issue_id": r[0], "reason": r[1], "status": r[2], "payload": json.loads(r[3])}
                    for r in db.execute(
                        "SELECT issue_id,reason,status,payload_json FROM issues "
                        "WHERE status!='RESOLVED' ORDER BY issue_id"
                    )
                ]
        decoded = [
            dict(zip(source.contract.schema.names, (k, *v), strict=True)) for k, v in rows.items()
        ]
        references[family] = {"codes": {r[code_field]: r for r in decoded}, "reviews": cases}
        manifest[family] = {
            "contract": source.contract.name,
            "version": version,
            "hash": source.contract.content_hash(rows),
            "control_id": control_id,
            "table_path": str(source.table_path),
            "quality_hash": digest(cases),
        }
    return references, manifest


class ArStore(DeltaStore):
    def read(self, table=None):
        table = table if table is not None else self.table()
        if table is None:
            return None, {}
        arrow = table.to_pyarrow_table()
        if arrow.schema != ar.SCHEMA:
            raise IntegrityError("Esquema LISTA_AR incompatible")
        rows = {}
        for row in arrow.to_pylist():
            if any(row[f] is None for f in ar.SCHEMA.names if f not in ar.NULLABLE):
                raise IntegrityError("Campo obligatorio LISTA_AR nulo")
            for f in ("FECHA_ON", "FECHA_OFF"):
                if row[f] is not None:
                    if row[f].microsecond or row[f].tzinfo is not None:
                        raise IntegrityError("Precisión/zona LISTA_AR inesperadas")
                    row[f] = row[f].isoformat(timespec="seconds")
            if any(
                row[f] is not None and (not row[f] or row[f] != row[f].strip())
                for f in ("CAVIDAD", "MODELO", "COLOR", "PROCESO", "origen_fenix")
            ):
                raise IntegrityError("Texto LISTA_AR inválido")
            if row["FECHA_OFF"] is not None and row["FECHA_OFF"] < row["FECHA_ON"]:
                raise IntegrityError("Cierre LISTA_AR anterior al inicio")
            identity = ar.key(row)
            if identity in rows:
                raise IntegrityError("Tramo LISTA_AR duplicado, incluidos nulos")
            rows[identity] = row
        return table.version(), rows

    def apply_changes(self, inserts, updates, operation, before_version):
        table = self.table()
        version, existing = self.read(table)
        if version != before_version or not (inserts or updates):
            raise IntegrityError("Snapshot AR cambiado o escritura vacía")
        seen = set()
        for row in inserts:
            identity = ar.key(row)
            if identity in existing or identity in seen:
                raise IntegrityError("Inserción AR duplicada")
            seen.add(identity)
        for row in updates:
            identity = ar.key(row)
            old = existing.get(identity)
            if (
                identity in seen
                or old is None
                or old != row["before"]
                or old["FECHA_OFF"] is not None
                or row["FECHA_OFF"] is None
                or row["FECHA_OFF"] < row["FECHA_ON"]
            ):
                raise IntegrityError("Cierre AR incompatible con la intención")
            seen.add(identity)
        projected = []
        for row in [*inserts, *updates]:
            item = {f: row[f] for f in ar.SCHEMA.names}
            for f in ("FECHA_ON", "FECHA_OFF"):
                item[f] = datetime.fromisoformat(item[f]) if item[f] is not None else None
            projected.append(item)
        arrow = pa.Table.from_pylist(projected, schema=ar.SCHEMA)
        props = CommitProperties(
            custom_metadata={
                "ingesta_operation_id": operation,
                "ingesta_control_id": self.control_id,
                "ingesta_plant": self.plant,
                "ingesta_contract": ar.CONTRACT,
                "ingesta_release": __version__,
                "ingesta_timestamp_policy": "local_naive_test_v1",
            },
            max_commit_retries=0,
        )
        if table is None:
            write_deltalake(str(self.path), arrow, mode="error", commit_properties=props)
        else:
            parts = []
            for f in ("origen_fenix", *ar.KEY_FIELDS):
                a, b = f"t.`{f}`", f"s.`{f}`"
                parts.append(
                    f"({a} = {b} OR ({a} IS NULL AND {b} IS NULL))"
                    if f in ar.NULLABLE
                    else f"{a} = {b}"
                )
            metrics = (
                table.merge(
                    arrow,
                    " AND ".join(parts),
                    source_alias="s",
                    target_alias="t",
                    commit_properties=props,
                )
                .when_matched_update(
                    {"FECHA_OFF": "s.FECHA_OFF"},
                    predicate="t.FECHA_OFF IS NULL AND s.FECHA_OFF IS NOT NULL",
                )
                .when_not_matched_insert_all()
                .execute()
            )
            if (
                metrics["num_target_rows_inserted"] != len(inserts)
                or metrics["num_target_rows_updated"] != len(updates)
                or metrics["num_target_rows_deleted"] != 0
            ):
                raise IntegrityError("MERGE AR distinto de las intenciones")
