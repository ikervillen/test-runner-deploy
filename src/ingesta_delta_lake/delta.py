"""Acceso exclusivamente insertivo al catálogo; un único escritor cooperante."""

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__
from .commit_protocol import application_transaction
from .common import IntegrityError
from .config import Config
from .contracts import MODEL_SCHEMA

SCHEMA = MODEL_SCHEMA  # Compatibilidad pública de la primera entrega.


class DeltaStore:
    def __init__(self, config: Config, control_id: str):
        self.path = config.table_path
        self.plant = config.plant
        self.control_id = control_id
        self.contract = config.contract

    def table(self) -> DeltaTable | None:
        if not DeltaTable.is_deltatable(str(self.path)):
            if (self.path / "_delta_log").exists():
                raise IntegrityError("Existe un log Delta que no se puede abrir; requiere revisión")
            if self.path.exists() and (not self.path.is_dir() or any(self.path.iterdir())):
                raise IntegrityError("El destino contiene archivos sin una tabla Delta reconocible")
            return None
        table = DeltaTable(str(self.path))
        history = table.history()
        if not history or any(
            h.get("ingesta_control_id") != self.control_id
            or h.get("ingesta_plant") != self.plant
            or h.get("ingesta_contract") != self.contract.name
            for h in history
        ):
            raise IntegrityError(
                "Tabla ajena a este control/contrato o modificada por otro escritor"
            )
        return table

    def read(self, table: DeltaTable | None = None) -> tuple[int | None, dict]:
        table = table if table is not None else self.table()
        if table is None:
            return None, {}
        arrow = table.to_pyarrow_table()
        if arrow.schema != self.contract.schema:
            raise IntegrityError(f"Esquema Delta inesperado: {arrow.schema}")
        rows = {}
        codes = set()
        for r in arrow.to_pylist():
            identifier = r[self.contract.key]
            value = self.contract.value(r)
            code = self.contract.code(value)
            if any(v is None or (isinstance(v, str) and not v.strip()) for v in r.values()):
                raise IntegrityError("El catálogo aceptado tiene valores inválidos")
            if identifier in rows or code in codes:
                raise IntegrityError("El catálogo aceptado no es una correspondencia uno a uno")
            rows[identifier] = value
            codes.add(code)
        return table.version(), rows

    def insert(self, rows: list[dict], operation_id: str, before_version: int | None) -> None:
        table = self.table()
        if (table.version() if table else None) != before_version:
            raise IntegrityError("Cambió el snapshot antes de escribir")
        if not rows:
            raise IntegrityError("No se debe invocar el escritor sin inserciones")
        schema = self.contract.schema
        arrow = pa.Table.from_pylist([{k: r[k] for k in schema.names} for r in rows], schema=schema)
        write_deltalake(
            table if table else str(self.path),
            arrow,
            mode="append" if table else "error",
            commit_properties=CommitProperties(
                custom_metadata={
                    "ingesta_operation_id": operation_id,
                    "ingesta_control_id": self.control_id,
                    "ingesta_plant": self.plant,
                    "ingesta_contract": self.contract.name,
                    "ingesta_release": __version__,
                },
                app_transactions=application_transaction(self.control_id, before_version),
                max_commit_retries=0,
            ),
        )
