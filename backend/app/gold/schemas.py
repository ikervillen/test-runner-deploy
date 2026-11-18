"""Explicit Arrow contracts. Ingestion keys and counters never become floating IDs."""

from __future__ import annotations

import math

import pyarrow as pa

from .common import IntegrityError, digest

TYPES = {
    "S": pa.string(),
    "I": pa.int64(),
    "F": pa.float64(),
    "T": pa.timestamp("us"),
    "D": pa.date32(),
    "B": pa.bool_(),
}


def schema(spec: str) -> pa.Schema:
    fields = []
    for item in spec.split():
        name, kind = item.split(":")
        fields.append(pa.field(name, TYPES[kind.rstrip("?")], kind.endswith("?")))
    return pa.schema(fields)


CONTEXT = (
    "id_observacion:S origen_fenix:S linea:S id_produccion:S "
    "referencia_origen:S periodo_inicio:T periodo_fin:T regla_atribucion:S "
)
MIA_CONTEXT = CONTEXT + (
    "id_asignacion:S ramal:I codigo_maquina_inspeccion:I molde:I cavidad:S? seccion:S? color:S? "
)
WINDOW = "id_produccion:S tipo_ventana:S inicio:T fin:T corte:T seleccion_id:S "
CATALOG = "catalog_version:S referencia_evidencia:S "

SPECS = {
    "zc_production_links": "id_produccion:S id_observacion:S regla_atribucion:S",
    "production_calculation_state": "id_produccion:S raw_inputs_hash:S config_id:S",
    "productions": "id_produccion:S planta:S origen_fenix:S? linea:S id_maquina_fenix:I? "
    "maquina_fisica:S? id_zc_raiz:I? id_modelo_fenix:I? codigo_modelo_botella:S? "
    "fecha_inicio_zc:T? fecha_fin_zc:T? fecha_inicio_zf:T? fecha_fin_zf:T? "
    "fecha_inicio_operativo_zc:T? criterio_inicio_operativo:S? estado_produccion:S estado_dato:S",
    "production_assignments": "id_asignacion:S id_produccion:S planta:S origen_fenix:S? "
    "linea:S id_maquina_fenix:I? zona:S ramal:I? vigente_desde:T vigente_hasta:T? "
    "estado_asignacion:S version_regla:S revision:I referencia_evidencia:S",
    "production_capacity": "id_capacidad:S id_produccion:S id_maquina_fenix:I? "
    "numero_secciones:I? numero_gotas:I? cavidades_asignadas:I? vigente_desde:T "
    "vigente_hasta:T? estado_capacidad:S version_regla:S revision:I referencia_evidencia:S",
    "defect_catalog": CATALOG + "espacio_codigo:S codigo:S id_fenix:I? descripcion:S "
    "descripcion_corta:S? categoria:S?",
    "ar_status_catalog": CATALOG + "id_status_fenix:I codigo_status:I descripcion:S "
    "descripcion_corta:S? codigo_origen:I codigo_zona:I",
    "ar_type_catalog": CATALOG + "id_tipo_ar_fenix:I codigo_tipo_ar:I descripcion:S "
    "codigo_agrupacion:I zona_interpretada:S? regla_zona:S?",
    "model_segment_catalog": CATALOG + "codigo_modelo_botella:S segmento_marble:S? "
    "segmento_calidad:S? profile_id:S? tipo_cierre:S? estado_correspondencia:S",
    "dar_profile_members": CATALOG + "profile_id:S segmento_calidad:S grupo_id:S "
    "grupo_label:S espacio_codigo:S codigo:S aplicabilidad_cierre:S?",
    "mia_rejections": MIA_CONTEXT + "espacio_defecto:S codigo_defecto:S "
    "inspeccionadas_fuente:I rechazos:I " + " ".join(f"rech{i}:I" for i in range(1, 11)),
    "mia_inspections": MIA_CONTEXT + "inspeccionadas:I rechazos_fuente:I "
    "rechazos_auto_fuente:I fecha_r:T?",
    "inspection_bases": "id_base:S id_produccion:S linea:S ramal:I periodo_inicio:T "
    "periodo_fin:T seleccion_id:S inspeccionadas:I",
    "mia_window_exposures": WINDOW + "inspected_basis_id:S inspeccionadas:I muestras:I",
    "mia_window_bases": WINDOW
    + "espacio_defecto:S codigo_defecto:S rechazos:I inspected_basis_id:S",
    "ar_intervals": "id_tramo:S id_observacion:S origen_fenix:S linea:S referencia_origen:S "
    "id_produccion:S id_asignacion:S? codigo_ar_original:I espacio_defecto:S codigo_defecto:S "
    "fecha_on:T fecha_off:T? maquina_is:I cavidad:S? status_original:I tipo_original:I "
    "modelo_original:S color:S? proceso:S? molde:I id_agrupacion:I? zona:S categoria:S? "
    "grupo_dar:S? regla_atribucion:S",
    "ar_window_exposures": WINDOW + "capacity_basis_id:S tiempo_disponible_us:I",
    "ar_window_bases": WINDOW + "ambito:S codigo_ambito:S tiempo_afectado_us:I "
    "capacity_basis_id:S eventos:I",
    "zc_observations": "id_observacion:S origen_fenix:S linea:S referencia_origen:S "
    "id_maquina_contador:I periodo_inicio:T periodo_fin:T rueda:I teorico_a:I "
    "teorico_b:I teorico_total:I salida_maquina_is:I",
    "zc_window_bases": "id_base_zc:S origen_fenix:S id_maquina_contador:I tipo_ventana:S "
    "inicio:T fin:T corte:T seleccion_id:S rueda:I teoricos:I muestras:I",
    "mia_reference_sets": "reference_id:S nivel:S valor:S seleccion_id:S "
    "reference_rule_id:S catalog_version:S estado:S",
    "mia_reference_members": "reference_id:S id_produccion:S rates_version:S",
    "mia_reference_values": "reference_id:S espacio_defecto:S codigo_defecto:S muestras:I "
    "media_percent:F? sigma_percent:F? estado:S",
    "quality_scores": "id_resultado:S " + WINDOW + "config_id:S formula_version:S "
    "reference_id:S? total_penalty:F normalized_score:F evaluated_weight:F "
    "estado_interpretacion:S fecha_calculo:T",
    "quality_score_components": "id_resultado:S componente:S score:F peso:F "
    "contribucion:F basis_reference:S",
    "mia_score_details": "id_resultado:S espacio_defecto:S codigo_defecto:S rechazos:I "
    "inspeccionadas:I current_percent:F historical_mean_percent:F? historical_sigma_percent:F? "
    "z_score:F? score:F peso:F contribucion:F muestras:I",
    "aql_score_details": "id_resultado:S categoria:S affected_cavity_minutes:F "
    "total_cavity_minutes:F exposure_percent:F aql_percent:F ratio:F? score:F peso:F "
    "contribucion:F top_defect_code:S?",
    "dar_score_details": "id_resultado:S grupo_id:S eventos:I affected_minutes:F "
    "share_percent:F exposure_percent:F score:F threshold_rule_id:S",
    "zc_score_details": "id_resultado:S target_percent:F actual_percent:F loss_points:F "
    "score:F peso:F contribucion:F muestras:I basis_reference:S",
    "publication_scope_status": "scope_id:S id_produccion:S? source:S origin:S? "
    "period_start:T? period_end:T? estado:S reason_code:S? evidence_reference:S? "
    "last_successful_discovery:T? confirmed_source_version:I? pending_files:I? "
    "earliest_pending_period:T?",
}
SCD = (
    "dimension_key:S entity_key:S valid_from:T? valid_to:T? recorded_from:T "
    "recorded_to:T? is_current:B attributes_hash:S source_version:S source_reference:S "
)
SPECS.update(
    {
        "dim_plant": "plant_key:S planta:S",
        "dim_date": "date_key:I fecha:D year:I month:I day:I weekday:I",
        "dim_machine": SCD + "planta:S id_maquina_fenix:I nombre:S codigo_linea_zc:I?",
        "dim_model": SCD
        + (
            "planta:S codigo_modelo_botella:S segmento_marble:S? "
            "segmento_calidad:S? profile_id:S? tipo_cierre:S? estado:S "
            "estado_dar:S? estado_modelo:S?"
        ),
        "dim_defect": SCD + "planta:S espacio_codigo:S codigo:S descripcion:S categoria:S?",
    }
)
DIMENSION_COLUMNS = "plant_key:S? machine_key:S? model_key:S? defect_key:S? date_key:I?"
DIMENSIONAL_FACTS = {
    "productions",
    "mia_rejections",
    "mia_inspections",
    "ar_intervals",
    "zc_observations",
    "quality_scores",
    "mia_window_bases",
}
SPECS["model_segment_catalog"] += " estado_modelo:S? estado_dar:S? referencia_clasificacion:S?"
SPECS["quality_scores"] += " configuration_status:S? basis_publication_id:S?"
SPECS["segment_catalog"] = (
    "catalog_version:S segmento_calidad:S etiqueta:S source_sha256:S dar_status:S"
)
for _name in DIMENSIONAL_FACTS:
    SPECS[_name] += " " + DIMENSION_COLUMNS
SCHEMAS = {name: schema(spec) for name, spec in SPECS.items()}
KEYS = {
    "production_calculation_state": ("id_produccion",),
    "productions": ("id_produccion",),
    "production_assignments": ("id_asignacion",),
    "production_capacity": ("id_capacidad",),
    "defect_catalog": ("catalog_version", "espacio_codigo", "codigo"),
    "ar_status_catalog": ("catalog_version", "codigo_status"),
    "ar_type_catalog": ("catalog_version", "codigo_tipo_ar"),
    "model_segment_catalog": ("catalog_version", "codigo_modelo_botella"),
    "dar_profile_members": ("catalog_version", "profile_id", "espacio_codigo", "codigo"),
    "mia_rejections": ("id_observacion",),
    "mia_inspections": ("id_observacion",),
    "inspection_bases": ("id_base",),
    "ar_intervals": ("id_tramo",),
    "zc_observations": ("id_observacion",),
    "zc_window_bases": ("id_base_zc",),
    "mia_reference_sets": ("reference_id",),
    "mia_reference_members": ("reference_id", "id_produccion"),
    "mia_reference_values": ("reference_id", "espacio_defecto", "codigo_defecto"),
    "quality_scores": ("id_resultado",),
    "quality_score_components": ("id_resultado", "componente"),
    "mia_score_details": ("id_resultado", "espacio_defecto", "codigo_defecto"),
    "aql_score_details": ("id_resultado", "categoria"),
    "dar_score_details": ("id_resultado", "grupo_id"),
    "zc_score_details": ("id_resultado",),
    "publication_scope_status": ("scope_id",),
}
WK = ("id_produccion", "tipo_ventana", "inicio", "fin", "seleccion_id")
KEYS.update(
    {
        "zc_production_links": ("id_produccion", "id_observacion"),
        "segment_catalog": ("catalog_version", "segmento_calidad"),
        "dim_plant": ("plant_key",),
        "dim_date": ("date_key",),
        "dim_machine": ("dimension_key",),
        "dim_model": ("dimension_key",),
        "dim_defect": ("dimension_key",),
    }
)
KEYS.update(
    {
        "mia_window_exposures": WK,
        "ar_window_exposures": WK,
        "mia_window_bases": (*WK, "espacio_defecto", "codigo_defecto"),
        "ar_window_bases": (*WK, "ambito", "codigo_ambito"),
    }
)
# Physical layout of the tables that grow with history. Readers select by production, so
# Delta prunes whole partitions before any batch is decoded. Applied when a table is first
# created; existing stores keep their layout and remain readable.
PARTITION_COLUMN = "id_produccion"
PARTITIONED_TABLES = frozenset(
    {
        "mia_rejections",
        "mia_inspections",
        "ar_intervals",
        "inspection_bases",
        "mia_window_exposures",
        "mia_window_bases",
        # A click asks for one production. These are the tables that click opens.
        "quality_scores",
        "production_calculation_state",
        "production_assignments",
        "ar_window_exposures",
        "ar_window_bases",
    }
)


def partition_columns(name: str) -> list[str] | None:
    return [PARTITION_COLUMN] if name in PARTITIONED_TABLES else None


def schema_id(value: pa.Schema) -> str:
    return digest([(f.name, str(f.type), f.nullable) for f in value])


_ROW_ADDITIONS = {
    "model_segment_catalog": ("estado_modelo", "estado_dar", "referencia_clasificacion"),
    "quality_scores": ("configuration_status", "basis_publication_id"),
}
_DIMENSION_COLUMNS = ("plant_key", "machine_key", "model_key", "defect_key", "date_key")


def check_row(name: str, row: dict) -> dict:
    """One output row against its contract. The caller keeps the returned copy."""
    expected = SCHEMAS[name]
    if name in _ROW_ADDITIONS:
        row = dict.fromkeys(_ROW_ADDITIONS[name]) | row
    row = dict.fromkeys(_DIMENSION_COLUMNS) | row if name in DIMENSIONAL_FACTS else dict(row)
    enums = {
        "estado_produccion": {"OPEN", "CLOSED", "UNKNOWN"},
        "estado_dato": {"VALID", "PENDING", "REVIEW"},
        "estado_asignacion": {"CONFIRMED", "PENDING", "REVIEW"},
        "estado_capacidad": {"CONFIRMED", "PENDING", "REVIEW"},
        "estado_correspondencia": {"CONFIRMED", "PENDING", "REVIEW"},
        "estado_dar": {"CONFIRMED", "PENDING", "PROVISIONAL"},
        "dar_status": {"CONFIRMED", "PENDING", "PROVISIONAL"},
        "configuration_status": {"CONFIRMED", "PROVISIONAL_DAR"},
        "zona": {"ZC", "ZF"},
        "zona_interpretada": {"ZC", "ZF"},
        "tipo_ventana": {"HOUR", "SHIFT", "PRODUCTION"},
        "categoria": {"Critico", "Grave", "Mayor", "Menor", "Leve"},
        "ambito": {"CATEGORY", "DAR_GROUP", "DAR_TOTAL"},
        "nivel": {"MODEL", "SEGMENT"},
        "estado": {"READY", "WAITING_INPUT", "WAITING_DEPENDENCY", "REVIEW", "UNAVAILABLE"},
    }
    amounts = {
        "rechazos",
        "inspeccionadas",
        "inspeccionadas_fuente",
        "rechazos_fuente",
        "rechazos_auto_fuente",
        "muestras",
        "rueda",
        "teoricos",
        "teorico_total",
        "teorico_a",
        "teorico_b",
        "tiempo_afectado_us",
        "tiempo_disponible_us",
        "eventos",
        *(f"rech{i}" for i in range(1, 11)),
    }
    if set(row) != set(expected.names):
        raise IntegrityError(f"{name}: columns differ: {set(row) ^ set(expected.names)}")
    for field in expected:
        value = row[field.name]
        allowed = (
            {"CONFIRMED", "PENDING", "REVIEW"}
            if name == "dim_model" and field.name == "estado"
            else enums.get(field.name)
        )
        if value is not None and allowed is not None and value not in allowed:
            raise IntegrityError(f"{name}.{field.name}: unknown controlled value")
        if value is not None and field.name in amounts and value < 0:
            raise IntegrityError(f"{name}.{field.name}: negative quantity")
        if value is None and not field.nullable:
            raise IntegrityError(f"{name}.{field.name} cannot be null")
        if isinstance(value, float) and not math.isfinite(value):
            raise IntegrityError(f"{name}.{field.name} is not finite")
        if pa.types.is_integer(field.type) and value is not None and type(value) is not int:
            raise IntegrityError(f"{name}.{field.name} must be an exact integer")
    if (
        name == "productions"
        and row["estado_produccion"] == "CLOSED"
        and row["fecha_fin_zf"] is None
    ):
        raise IntegrityError("A closed production requires an accredited ZF end")
    if name == "production_capacity" and row["estado_capacidad"] == "CONFIRMED":
        n, g = row["numero_secciones"], row["numero_gotas"]
        if n is None or g is None or n <= 0 or g <= 0 or n * g != row["cavidades_asignadas"]:
            raise IntegrityError("Confirmed capacity requires positive consistent sections/gobs")
    return row


def make_table(name: str, rows: list[dict]) -> pa.Table:
    prepared = [check_row(name, row) for row in rows]
    table = pa.Table.from_pylist(prepared, schema=SCHEMAS[name])
    keys = [tuple(row[k] for k in KEYS[name]) for row in prepared]
    if len(set(keys)) != len(keys):
        raise IntegrityError(f"{name}: duplicate logical key")
    return table
