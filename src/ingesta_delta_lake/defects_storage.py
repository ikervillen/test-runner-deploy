"""Append controlado de 18 campos MIA originales y ámbito técnico, sin actualizaciones."""

from datetime import datetime

import pyarrow as pa
from deltalake import write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__
from . import lista_defectos as defects
from .common import IntegrityError
from .delta import DeltaStore


class DefectsStore(DeltaStore):
    def read(self, table=None):
        table = table if table is not None else self.table()
        if table is None:
            return None, {}
        arrow = table.to_pyarrow_table()
        if arrow.schema != defects.SCHEMA:
            raise IntegrityError("Esquema LISTA_DEFECTOS incompatible")
        rows = {}
        for row in arrow.to_pylist():
            if any(row[f] is None for f in defects.SCHEMA.names if f != "CAVIDAD"):
                raise IntegrityError("Campo obligatorio LISTA_DEFECTOS nulo")
            if row["FECHA"].microsecond or row["FECHA"].tzinfo is not None:
                raise IntegrityError("Precisión o zona horaria LISTA_DEFECTOS inesperadas")
            row["FECHA"] = row["FECHA"].isoformat(timespec="seconds")
            if (
                not row["origen_fenix"]
                or row["MOLDE"] < 0
                or any(row[f] < 0 for f in defects.FIELDS[5:17])
                or (
                    row["CAVIDAD"] is not None
                    and (not row["CAVIDAD"] or row["CAVIDAD"] != row["CAVIDAD"].strip())
                )
            ):
                raise IntegrityError("Contenido LISTA_DEFECTOS publicado inválido")
            k = defects.key(row)
            if k in rows:
                raise IntegrityError("Clave LISTA_DEFECTOS duplicada, incluida cavidad nula")
            rows[k] = row
        return table.version(), rows

    def insert(self, rows, operation, before_version):
        table = self.table()
        version, existing = self.read(table)
        if version != before_version or not rows:
            raise IntegrityError("Snapshot LISTA_DEFECTOS cambiado o inserción vacía")
        keys = [defects.key(r) for r in rows]
        if len(set(keys)) != len(keys) or set(keys) & set(existing):
            raise IntegrityError("La inserción duplicaría claves LISTA_DEFECTOS")
        _, signatures, groups = defects.indexes([*existing.values(), *rows])
        if any(
            len({r["INSPECC"] for r in values}) > 1
            for index in (signatures, groups)
            for values in index.values()
        ):
            raise IntegrityError("Se intentó publicar una revisión/base de inspección no resuelta")
        values = []
        for row in rows:
            value = {f: row[f] for f in defects.SCHEMA.names}
            value["FECHA"] = datetime.fromisoformat(value["FECHA"])
            values.append(value)
        write_deltalake(
            table if table else str(self.path),
            pa.Table.from_pylist(values, schema=defects.SCHEMA),
            mode="append" if table else "error",
            commit_properties=CommitProperties(
                custom_metadata={
                    "ingesta_operation_id": operation,
                    "ingesta_control_id": self.control_id,
                    "ingesta_plant": self.plant,
                    "ingesta_contract": defects.CONTRACT,
                    "ingesta_release": __version__,
                    "ingesta_timestamp_policy": "local_naive_test_v1",
                },
                max_commit_retries=0,
            ),
        )
