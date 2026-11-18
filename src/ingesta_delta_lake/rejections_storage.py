"""Append y control propios de LISTA_RECH; sin completar fechas ni contadores."""

from datetime import datetime

import pyarrow as pa
from deltalake import write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__
from . import lista_rech as rech
from .commit_protocol import application_transaction, delta_configuration
from .common import IntegrityError
from .defects_control import DefectsControl
from .delta import DeltaStore
from .mia_scope import ScopedRead


class RejectionsControl(DefectsControl):
    model = rech

    def held(self):
        keys, signatures, groups = super().held()
        # Sin COD_DEF, cada grupo es una única observación pendiente de resolución.
        groups.update(tuple(i["group"]) for i in self.cases() if "group" in i)
        return keys, signatures, groups


class RejectionsStore(ScopedRead, DeltaStore):
    model = rech

    def read(self, table=None):
        table = self.read_view(table if table is not None else self.table())
        if table is None:
            return None, {}
        rows, signatures = {}, set()
        for batch in self.batches(table, rech.SCHEMA):
            for row in batch.to_pylist():
                if any(
                    row[f] is None for f in rech.SCHEMA.names if f not in {"CAVIDAD", "FECHA_R"}
                ):
                    raise IntegrityError("Campo obligatorio LISTA_RECH nulo")
                for column in ("FECHA", "FECHA_R"):
                    stamp = row[column]
                    if stamp is not None:
                        if stamp.microsecond or stamp.tzinfo is not None:
                            raise IntegrityError("Precisión o zona horaria LISTA_RECH inesperadas")
                        row[column] = stamp.isoformat(timespec="seconds")
                if (
                    not row["origen_fenix"]
                    or row["MOLDE"] < 0
                    or any(row[f] < 0 for f in rech.FIELDS[4:7])
                    or (
                        row["CAVIDAD"] is not None
                        and (not row["CAVIDAD"] or row["CAVIDAD"] != row["CAVIDAD"].strip())
                    )
                ):
                    raise IntegrityError("Contenido LISTA_RECH publicado inválido")
                if self.scope is not None and not self.includes(row):
                    continue
                k, s = rech.key(row), rech.signature(row)
                if k in rows or s in signatures:
                    raise IntegrityError("Clave/firma LISTA_RECH duplicada, incluida cavidad nula")
                rows[k] = row
                signatures.add(s)
        return table.version(), rows

    def insert(self, rows, operation, before_version):
        self.validate_scope(rows)
        table = self.table()
        version, existing = self.read(table)
        if version != before_version or not rows:
            raise IntegrityError("Snapshot LISTA_RECH cambiado o inserción vacía")
        keys = [rech.key(r) for r in rows]
        if len(set(keys)) != len(keys) or set(keys) & set(existing):
            raise IntegrityError("La inserción duplicaría claves LISTA_RECH")
        signatures = [rech.signature(r) for r in [*existing.values(), *rows]]
        if len(signatures) != len(set(signatures)):
            raise IntegrityError("Se intentó publicar otra revisión activa de LISTA_RECH")
        values = []
        for row in rows:
            value = {f: row[f] for f in rech.SCHEMA.names}
            for column in ("FECHA", "FECHA_R"):
                value[column] = (
                    datetime.fromisoformat(value[column]) if value[column] is not None else None
                )
            values.append(value)
        write_deltalake(
            table if table else str(self.path),
            pa.Table.from_pylist(values, schema=rech.SCHEMA),
            mode="append" if table else "error",
            configuration=delta_configuration(self.config),
            commit_properties=CommitProperties(
                custom_metadata={
                    "ingesta_operation_id": operation,
                    "ingesta_control_id": self.control_id,
                    "ingesta_plant": self.plant,
                    "ingesta_contract": rech.CONTRACT,
                    "ingesta_release": __version__,
                    "ingesta_timestamp_policy": "local_naive_test_v1",
                },
                app_transactions=application_transaction(self.control_id, before_version),
                max_commit_retries=0,
            ),
        )
