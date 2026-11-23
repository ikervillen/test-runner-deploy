"""MP9 identity MERGE: validated closure and monotonic ZF confirmation only."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime

import pyarrow as pa
from deltalake import write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__, mp9
from .commit_protocol import application_transaction, delta_configuration, merge_counts_match
from .common import DependencyPending, IntegrityError, canonical
from .delta import DeltaStore
from .modelos import content_hash as model_hash
from .pipeline_control import check_source_version


def load_models(config, *, derived=False):
    config = replace(config, family="SIP_MODELO")
    if not config.control_path.exists():
        if config.table_path.exists():
            raise IntegrityError("Falta el control del diccionario de modelos existente")
        return {}, {"contract": config.contract.name, "version": None, "hash": model_hash({})}
    with closing(sqlite3.connect(f"{config.control_path.as_uri()}?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        expected = canonical(
            {
                "plant": config.plant,
                "silver_root": str(config.silver_root),
                "bronze_root": str(config.bronze_root),
                "contract": config.contract.name,
            }
        )
        if settings.get("binding") != expected:
            raise IntegrityError("El diccionario de modelos pertenece a otro ámbito")
        if db.execute(
            "SELECT COUNT(*) FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
        ).fetchone()[0]:
            error = DependencyPending if derived else IntegrityError
            raise error("Recuperar primero las operaciones pendientes de SIP_MODELO")
        version, models = DeltaStore(config, settings["control_id"]).read()
        check_source_version(
            config,
            settings,
            version,
            "El snapshot de modelos no está confirmado por su control",
            dependency=derived,
        )
    return models, {
        "contract": config.contract.name,
        "version": version,
        "hash": model_hash(models),
        "control_id": settings["control_id"],
        "table_path": str(config.table_path),
    }


class Mp9Store(DeltaStore):
    def read(self, table=None):
        table = self.read_view(table if table is not None else self.table())
        if table is None:
            return None, {}
        arrow = table.to_pyarrow_table()
        if arrow.schema != mp9.SCHEMA:
            raise IntegrityError("Esquema MP9 inesperado")
        rows = {}
        for row in arrow.to_pylist():
            for name in ("FECHA_ON", "FECHA_OFF"):
                if row[name] is not None:
                    row[name] = row[name].isoformat(timespec="seconds")
            identity = mp9.key(row)
            if identity in rows or any(
                row[f] is None
                for f in (
                    "ID",
                    "ID_MODELO",
                    "ID_MAQUINA",
                    "FECHA_ON",
                    "STATUS",
                    "ZF_OK",
                    "origen_fenix",
                )
            ):
                raise IntegrityError("Identidad MP9 duplicada o campo obligatorio nulo")
            if (
                row["STATUS"] not in (1, 2)
                or row["ZF_OK"] not in (0, 1)
                or not row["origen_fenix"]
                or (row["FECHA_OFF"] is not None and row["FECHA_OFF"] < row["FECHA_ON"])
            ):
                raise IntegrityError("Contenido MP9 publicado incompatible con su contrato")
            rows[identity] = row
        return table.version(), rows

    def apply_changes(self, inserts, updates, operation, before_version):
        table = self.table()
        version, existing = self.read(table)
        if version != before_version or not (inserts or updates):
            raise IntegrityError("Snapshot MP9 cambiado o escritura vacía")
        seen = set()
        for row in inserts:
            identity = mp9.key(row)
            if identity in existing or identity in seen:
                raise IntegrityError("La inserción MP9 ya existe o está duplicada")
            seen.add(identity)
        for row in updates:
            identity = mp9.key(row)
            old = existing.get(identity)
            if (
                identity in seen
                or old != row["before"]
                or old is None
                or old == {f: row[f] for f in mp9.SCHEMA.names}
                or not mp9.advances(old, row)
            ):
                raise IntegrityError("Actualización MP9 incompatible con un cierre validado")
            seen.add(identity)
        projected = []
        for row in [*inserts, *updates]:
            values = {f: row[f] for f in mp9.SCHEMA.names}
            for field in ("FECHA_ON", "FECHA_OFF"):
                if values[field] is not None:
                    values[field] = datetime.fromisoformat(values[field])
            projected.append(values)
        arrow = pa.Table.from_pylist(projected, schema=mp9.SCHEMA)
        props = CommitProperties(
            custom_metadata={
                "ingesta_operation_id": operation,
                "ingesta_control_id": self.control_id,
                "ingesta_plant": self.plant,
                "ingesta_contract": mp9.CONTRACT,
                "ingesta_release": __version__,
                "ingesta_timestamp_policy": "local_naive_test_v1",
                "ingesta_lifecycle_rule": mp9.LIFECYCLE_RULE,
            },
            app_transactions=application_transaction(self.control_id, before_version),
            max_commit_retries=0,
        )
        if table is None:
            write_deltalake(
                str(self.path),
                arrow,
                mode="error",
                configuration=delta_configuration(self.config),
                commit_properties=props,
            )
        else:
            metrics = (
                table.merge(
                    arrow,
                    "t.origen_fenix = s.origen_fenix AND t.ID = s.ID",
                    source_alias="s",
                    target_alias="t",
                    commit_properties=props,
                )
                .when_matched_update(
                    {"FECHA_OFF": "s.FECHA_OFF", "ZF_OK": "s.ZF_OK"},
                    predicate=(
                        "(t.FECHA_OFF IS NULL AND s.FECHA_OFF IS NOT NULL) "
                        "OR (t.ZF_OK = 0 AND s.ZF_OK = 1)"
                    ),
                )
                .when_not_matched_insert_all()
                .execute()
            )
            if not merge_counts_match(metrics, inserts, updates, self.config):
                raise IntegrityError("El MERGE MP9 no coincide con las intenciones preparadas")
