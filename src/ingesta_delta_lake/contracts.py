"""Contratos de las fuentes implementadas y del catálogo derivado."""

from collections.abc import Callable
from dataclasses import dataclass

import pyarrow as pa

from . import (
    associations,
    catalog,
    cont_zc,
    defectos,
    lista_ar,
    lista_defectos,
    lista_rech,
    modelos,
    mp9,
    status,
    tipos_ar,
    topology,
)
from .common import ConfigurationError

MODEL_SCHEMA = pa.schema(
    [
        pa.field("id_modelo_fenix", pa.int64(), nullable=False),
        pa.field("codigo_modelo_botella", pa.string(), nullable=False),
    ]
)
DEFECT_SCHEMA = pa.schema(
    [
        pa.field("id_defecto_fenix", pa.int64(), nullable=False),
        pa.field("codigo_aviso_rechazo", pa.int64(), nullable=False),
        pa.field("descripcion", pa.string(), nullable=False),
        pa.field("descripcion_corta", pa.string(), nullable=False),
    ]
)
STATUS_SCHEMA = pa.schema(
    [
        pa.field("id_status_fenix", pa.int64(), nullable=False),
        pa.field("codigo_status", pa.int64(), nullable=False),
        pa.field("descripcion", pa.string(), nullable=False),
        pa.field("descripcion_corta", pa.string(), nullable=False),
        pa.field("codigo_origen", pa.int64(), nullable=False),
        pa.field("codigo_zona", pa.int64(), nullable=False),
    ]
)
AR_SCHEMA = pa.schema(
    [
        pa.field("id_tipo_ar_fenix", pa.int64(), nullable=False),
        pa.field("codigo_tipo_ar", pa.int64(), nullable=False),
        pa.field("descripcion", pa.string(), nullable=False),
        pa.field("codigo_agrupacion", pa.int64(), nullable=False),
    ]
)


@dataclass(frozen=True)
class DictionaryContract:
    name: str
    schema: pa.Schema
    parse: Callable
    decide: Callable
    content_hash: Callable
    statistics: Callable

    @property
    def key(self) -> str:
        return self.schema.names[0]

    def value(self, row: dict):
        fields = self.schema.names[1:]
        return row[fields[0]] if len(fields) == 1 else tuple(row[k] for k in fields)

    def code(self, value):
        return value[0] if isinstance(value, tuple) else value


@dataclass(frozen=True)
class TemporalContract:
    name: str
    schema: pa.Schema
    content_hash: Callable
    key: str = "ID"


CONTRACTS = {
    **{
        f: TemporalContract(
            topology.CONTRACTS[f],
            topology.SCHEMAS[f],
            lambda rows, family=f: topology.content_hash(family, rows),
            key="record_key",
        )
        for f in topology.FAMILIES
    },
    "SIP_MODELO": DictionaryContract(
        modelos.CONTRACT,
        MODEL_SCHEMA,
        modelos.parse,
        modelos.decide,
        modelos.content_hash,
        lambda parsed: {"rows_read": parsed.rows_read, "unique_pairs": len(parsed.pairs)},
    ),
    "SIP_DEFECTO": DictionaryContract(
        defectos.CONTRACT,
        DEFECT_SCHEMA,
        defectos.parse,
        defectos.decide,
        defectos.content_hash,
        defectos.statistics,
    ),
    "SIP_DEFECTO_STATUS": DictionaryContract(
        status.CONTRACT,
        STATUS_SCHEMA,
        status.parse,
        status.decide,
        status.content_hash,
        status.statistics,
    ),
    "SIP_DEFECTO_AR": DictionaryContract(
        tipos_ar.CONTRACT,
        AR_SCHEMA,
        tipos_ar.parse,
        tipos_ar.decide,
        tipos_ar.content_hash,
        tipos_ar.statistics,
    ),
    "MP9_CAMBIO_MODELO": TemporalContract(mp9.CONTRACT, mp9.SCHEMA, mp9.content_hash),
    "CATALOGO_PRODUCCIONES": TemporalContract(
        catalog.CONTRACT, catalog.SCHEMA, catalog.content_hash, key="id_produccion"
    ),
    "LISTA_DEFECTOS": TemporalContract(
        lista_defectos.CONTRACT,
        lista_defectos.SCHEMA,
        lista_defectos.content_hash,
        key="record_key",
    ),
    "LISTA_RECH": TemporalContract(
        lista_rech.CONTRACT, lista_rech.SCHEMA, lista_rech.content_hash, key="record_key"
    ),
    "LISTA_AR": TemporalContract(
        lista_ar.CONTRACT, lista_ar.SCHEMA, lista_ar.content_hash, key="record_key"
    ),
    "CONT_ZC": TemporalContract(
        cont_zc.CONTRACT, cont_zc.SCHEMA, cont_zc.content_hash, key="record_key"
    ),
    "ASIGNACION_PRODUCCION_RAMAL": TemporalContract(
        associations.CONTRACT, associations.SCHEMA, associations.content_hash, key="id_asignacion"
    ),
}


def get_contract(family: str) -> DictionaryContract | TemporalContract:
    try:
        return CONTRACTS[family]
    except (KeyError, TypeError) as exc:
        raise ConfigurationError(f"Familia no implementada: {family}") from exc
