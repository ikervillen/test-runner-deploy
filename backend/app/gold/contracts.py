"""Executable Gold contracts: grain, dependency DAG and referential integrity."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace

from .common import IntegrityError, digest
from .schemas import DIMENSIONAL_FACTS, KEYS, SCHEMAS, check_row, make_table

CONTRACT_VERSION = "quality-gold-dimensional-v3"


@dataclass(frozen=True)
class Contract:
    kind: str
    grain: str
    parents: tuple[str, ...] = ()
    additive: tuple[str, ...] = ()


CONTRACTS = {
    "zc_production_links": Contract(
        "bridge",
        "One producer-accepted physical counter observation to production link",
        ("productions", "zc_observations"),
    ),
    "segment_catalog": Contract(
        "catalog", "Catalog version and exact workbook family classification"
    ),
    "dim_plant": Contract("dimension", "One qualified plant identity"),
    "dim_date": Contract("dimension", "One calendar date; no implicit timezone conversion"),
    "dim_machine": Contract(
        "dimension", "One physical machine attribute version and effective interval", ("dim_plant",)
    ),
    "dim_model": Contract(
        "dimension", "One qualified model attribute version", ("dim_plant", "model_segment_catalog")
    ),
    "dim_defect": Contract(
        "dimension", "One qualified defect attribute version", ("dim_plant", "defect_catalog")
    ),
    "productions": Contract("context", "One accredited physical fabrication"),
    "production_assignments": Contract(
        "bridge", "One accepted physical assignment episode", ("productions",)
    ),
    "production_capacity": Contract("context", "One assigned-capacity episode", ("productions",)),
    "defect_catalog": Contract("catalog", "Catalog version, code namespace and defect code"),
    "ar_status_catalog": Contract("catalog", "Catalog version and status code"),
    "ar_type_catalog": Contract("catalog", "Catalog version and event type"),
    "model_segment_catalog": Contract("catalog", "Catalog version and bottle model"),
    "dar_profile_members": Contract(
        "bridge", "Catalog, profile, group and defect membership", ("defect_catalog",)
    ),
    "mia_rejections": Contract(
        "fact",
        "One accepted defect observation",
        ("productions", "production_assignments", "defect_catalog"),
        ("rechazos",),
    ),
    "mia_inspections": Contract(
        "fact",
        "One accepted physical inspection observation",
        ("productions", "production_assignments"),
    ),
    "inspection_bases": Contract(
        "fact",
        "One deduplicated physical inspection basis and selection",
        ("mia_inspections",),
        ("inspeccionadas",),
    ),
    "mia_window_exposures": Contract(
        "fact",
        "Production, window and selection; one independent denominator",
        ("inspection_bases", "productions"),
        ("inspeccionadas",),
    ),
    "mia_window_bases": Contract(
        "fact",
        "Production, window, selection and defect",
        ("mia_rejections", "mia_window_exposures"),
        ("rechazos",),
    ),
    "ar_intervals": Contract(
        "fact",
        "One accepted attributable quality-event interval",
        (
            "productions",
            "production_assignments",
            "defect_catalog",
            "ar_type_catalog",
            "ar_status_catalog",
        ),
    ),
    "ar_window_exposures": Contract(
        "fact",
        "Production, window and selection; one capacity basis",
        ("production_capacity", "productions"),
    ),
    "ar_window_bases": Contract(
        "fact",
        "Production, window, selection and category or DAR group",
        ("ar_intervals", "ar_window_exposures", "dar_profile_members"),
    ),
    "zc_observations": Contract(
        "fact",
        "One physical counter sample independent of concurrent productions",
        additive=("rueda", "teorico_total"),
    ),
    "zc_window_bases": Contract(
        "fact",
        "Origin, physical counter, window, cutoff and selection",
        ("zc_observations", "zc_production_links"),
        ("rueda", "teoricos"),
    ),
    "mia_reference_sets": Contract(
        "reference",
        "One explicitly selected historical population",
        ("productions", "model_segment_catalog"),
    ),
    "mia_reference_members": Contract(
        "bridge",
        "Reference population and member production",
        ("mia_reference_sets", "productions"),
    ),
    "mia_reference_values": Contract(
        "reference",
        "Reference population and defect",
        ("mia_reference_sets", "mia_reference_members", "mia_window_bases"),
    ),
    "production_calculation_state": Contract(
        "control", "One calculation-input fingerprint per production", ("productions",)
    ),
    "quality_scores": Contract(
        "fact",
        "One result per production, window and selection in this publication",
        (
            "productions",
            "mia_window_exposures",
            "mia_window_bases",
            "ar_window_exposures",
            "ar_window_bases",
            "zc_window_bases",
            "mia_reference_values",
        ),
    ),
    "quality_score_components": Contract("fact", "Result and component", ("quality_scores",)),
    "mia_score_details": Contract(
        "fact", "Result and defect", ("quality_scores", "defect_catalog")
    ),
    "aql_score_details": Contract("fact", "Result and severity category", ("quality_scores",)),
    "dar_score_details": Contract(
        "fact", "Result and DAR group", ("quality_scores", "dar_profile_members")
    ),
    "zc_score_details": Contract(
        "fact", "One hot-end result detail", ("quality_scores", "zc_window_bases")
    ),
    "publication_scope_status": Contract(
        "availability", "One source, production, interval and reason"
    ),
}

# Direct star-schema joins are executable dependencies, not only documentation.
DIMENSION_KEYS = {
    "plant_key": ("dim_plant", "plant_key"),
    "date_key": ("dim_date", "date_key"),
    "machine_key": ("dim_machine", "dimension_key"),
    "model_key": ("dim_model", "dimension_key"),
    "defect_key": ("dim_defect", "dimension_key"),
}
for _name in DIMENSIONAL_FACTS:
    CONTRACTS[_name] = replace(
        CONTRACTS[_name],
        parents=tuple(
            dict.fromkeys((*CONTRACTS[_name].parents, *(v[0] for v in DIMENSION_KEYS.values())))
        ),
    )

SOURCE_DEPENDENCIES = {
    "zc_production_links": ("observation_assignments",),
    "productions": (
        "CATALOGO_PRODUCCIONES",
        "MP9_CAMBIO_MODELO",
        "SIP_MODELO",
        "SIP_MAQUINA_MOLDEO",
        "hot_rejection_evidence",
    ),
    "production_assignments": ("ASIGNACION_PRODUCCION_RAMAL", "zc_presences"),
    "production_capacity": ("production_parameters", "machine_gobs", "gobs_dictionary"),
    "defect_catalog": ("SIP_DEFECTO", "catalog_bundle"),
    "ar_status_catalog": ("SIP_DEFECTO_STATUS",),
    "ar_type_catalog": ("SIP_DEFECTO_AR", "catalog_bundle"),
    "model_segment_catalog": ("catalog_bundle",),
    "segment_catalog": ("catalog_bundle",),
    "dar_profile_members": ("catalog_bundle",),
    "mia_rejections": ("LISTA_DEFECTOS", "observation_assignments"),
    "mia_inspections": ("LISTA_RECH", "observation_assignments"),
    "ar_intervals": ("LISTA_AR", "observation_assignments"),
    "zc_observations": ("CONT_ZC",),
    "dim_machine": ("SIP_MAQUINA_MOLDEO",),
}

# child, local columns, parent, referenced columns. Nullable FKs preserve unknowns.
FOREIGN_KEYS = (
    ("zc_production_links", ("id_observacion",), "zc_observations", ("id_observacion",)),
    ("production_assignments", ("id_produccion",), "productions", ("id_produccion",)),
    ("production_capacity", ("id_produccion",), "productions", ("id_produccion",)),
    (
        "mia_rejections",
        ("id_asignacion", "id_produccion"),
        "production_assignments",
        ("id_asignacion", "id_produccion"),
    ),
    (
        "mia_inspections",
        ("id_asignacion", "id_produccion"),
        "production_assignments",
        ("id_asignacion", "id_produccion"),
    ),
    (
        "ar_intervals",
        ("id_asignacion", "id_produccion"),
        "production_assignments",
        ("id_asignacion", "id_produccion"),
    ),
    ("mia_window_bases", ("inspected_basis_id",), "mia_window_exposures", ("inspected_basis_id",)),
    ("ar_window_bases", ("capacity_basis_id",), "ar_window_exposures", ("capacity_basis_id",)),
    ("quality_scores", ("reference_id",), "mia_reference_sets", ("reference_id",)),
    ("zc_score_details", ("basis_reference",), "zc_window_bases", ("id_base_zc",)),
    *(
        (fact, (field,), parent, (key,))
        for fact in sorted(DIMENSIONAL_FACTS)
        for field, (parent, key) in DIMENSION_KEYS.items()
    ),
)


def ordered(names=None):
    selected = set(CONTRACTS if names is None else names)
    unknown = selected - CONTRACTS.keys()
    if unknown:
        raise IntegrityError(f"Unknown contract tables: {sorted(unknown)}")
    result, visiting = [], set()

    def visit(name):
        if name in visiting:
            raise IntegrityError(f"Cyclic Gold dependency: {name}")
        if name in result:
            return
        visiting.add(name)
        for parent in CONTRACTS[name].parents:
            if parent in selected:
                visit(parent)
        visiting.remove(name)
        result.append(name)

    for name in sorted(selected):
        visit(name)
    return result


def describe():
    return {
        "contract_version": CONTRACT_VERSION,
        "rules": {
            "time_basis": "Industrial local time; no implicit timezone conversion",
            "nullable_foreign_keys": "Unknown stays NULL; never infer physical equivalence",
            "score_completeness": [
                "MIA",
                "Critico",
                "Grave",
                "Mayor",
                "Menor",
                "Leve",
                "DAR",
                "ZC",
            ],
            "dar_provisional": "Must retain PROVISIONAL_DAR; segment-specific approval pending",
            "aggregation": (
                "Additive quantities require disjoint physical samples and compatible selections; "
                "overlapping windows, shared counters, rates and scores must not be summed"
            ),
            "scd": (
                "Attribute changes create recording-time versions; accepted physical validity "
                "is separate. Late closure revises the same interval; prior publications persist"
            ),
        },
        "tables": [
            {
                "name": name,
                "kind": CONTRACTS[name].kind,
                "grain": CONTRACTS[name].grain,
                "key": list(KEYS[name]),
                "dependencies": list(CONTRACTS[name].parents),
                "source_dependencies": list(SOURCE_DEPENDENCIES.get(name, ())),
                "foreign_keys": [
                    {"columns": list(columns), "table": parent, "references": list(target)}
                    for child, columns, parent, target in FOREIGN_KEYS
                    if child == name
                ],
                "additive_measures": list(CONTRACTS[name].additive),
                "fields": [
                    {"name": f.name, "type": str(f.type), "nullable": f.nullable}
                    for f in SCHEMAS[name]
                ],
            }
            for name in ordered()
        ],
    }


def contract_id():
    return digest(describe())


def _iter_table(tables, name):
    spilled = getattr(tables, "spilled", None)
    if spilled and name in spilled:
        return tables.store.iter_rows(name)
    return tables[name]


def _validate_spilled(tables, *, complete=False):
    """Same rules as `validate`, scanning one spilled family at a time."""
    if complete and set(tables) != set(CONTRACTS):
        raise IntegrityError(f"Incomplete Gold contract: {sorted(set(CONTRACTS) ^ set(tables))}")
    names = ordered(tables)
    for name in names:
        if name not in getattr(tables, "spilled", ()):
            make_table(name, tables[name])
    productions = {r["id_produccion"] for r in tables.get("productions", [])}
    references = (
        {r["reference_id"] for r in _iter_table(tables, "mia_reference_sets")}
        if ("mia_reference_sets" in tables)
        else set()
    )
    scores = {}
    score_grains = set()
    components = defaultdict(list)
    for name in names:
        for raw in _iter_table(tables, name):
            row = check_row(name, raw) if name in tables.spilled else raw
            if {"inicio", "corte", "fin"}.issubset(row) and not (
                row["inicio"] < row["corte"] <= row["fin"]
            ):
                raise IntegrityError(f"{name}: invalid window or cutoff")
            if name == "quality_scores":
                grain = tuple(
                    row[k]
                    for k in ("id_produccion", "tipo_ventana", "inicio", "fin", "seleccion_id")
                )
                if grain in score_grains:
                    raise IntegrityError("quality_scores: multiple results for the same window")
                score_grains.add(grain)
                scores[row["id_resultado"]] = (
                    row["total_penalty"],
                    row["evaluated_weight"],
                    row["normalized_score"],
                )
            if (
                "id_produccion" in row
                and row["id_produccion"] is not None
                and "productions" in tables
                and row["id_produccion"] not in productions
            ):
                raise IntegrityError(f"{name}: unknown production")
            if (
                name in {"mia_reference_members", "mia_reference_values"}
                and "mia_reference_sets" in tables
                and row["reference_id"] not in references
            ):
                raise IntegrityError(f"{name}: unknown reference population")
            if (name.endswith("_details") or name == "quality_score_components") and (
                "quality_scores" in tables and row["id_resultado"] not in scores
            ):
                raise IntegrityError(f"{name}: detail without official complete score")
            if name == "quality_score_components":
                components[row["id_resultado"]].append(
                    (row["componente"], row["contribucion"], row["peso"])
                )
    for identity, (total, weight, normalized) in scores.items():
        parts = components[identity]
        if {item[0] for item in parts} != {
            "MIA",
            "Critico",
            "Grave",
            "Mayor",
            "Menor",
            "Leve",
            "DAR",
            "ZC",
        }:
            raise IntegrityError("Official score requires every component")
        got_total = round(sum(item[1] for item in parts), 4)
        got_weight = round(sum(item[2] for item in parts), 4)
        if got_total != total or got_weight != weight:
            raise IntegrityError("Score components and total/weight do not reconcile")
        if weight <= 0 or round(got_total / weight, 4) != normalized:
            raise IntegrityError("Normalized score does not reconcile")
    for child, columns, parent, target in FOREIGN_KEYS:
        if child not in tables or parent not in tables:
            continue
        identities = {tuple(r[k] for k in target) for r in _iter_table(tables, parent)}
        for row in _iter_table(tables, child):
            key = tuple(row.get(k) for k in columns)
            if None not in key and key not in identities:
                raise IntegrityError(f"{child}: missing {parent} reference {columns}")
    from .dimensional import validate_links

    validate_links(tables)


def validate(tables, *, complete=False):
    """Reject invalid business references before writing any Delta table."""
    if getattr(tables, "spilled", None):
        _validate_spilled(tables, complete=complete)
        return
    if complete and set(tables) != set(CONTRACTS):
        raise IntegrityError(f"Incomplete Gold contract: {sorted(set(CONTRACTS) ^ set(tables))}")
    ordered(tables)
    for name, rows in tables.items():
        make_table(name, rows)
    for child, columns, parent, target in FOREIGN_KEYS:
        if child not in tables or parent not in tables:
            continue
        identities = {tuple(r[k] for k in target) for r in tables[parent]}
        for row in tables[child]:
            key = tuple(row.get(k) for k in columns)
            if None not in key and key not in identities:
                raise IntegrityError(f"{child}: missing {parent} reference {columns}")
    from .dimensional import validate_links

    validate_links(tables)
    productions = {r["id_produccion"] for r in tables.get("productions", [])}
    scores = {r["id_resultado"]: r for r in tables.get("quality_scores", [])}
    references = {r["reference_id"] for r in tables.get("mia_reference_sets", [])}
    score_grains = set()
    for name, rows in tables.items():
        for row in rows:
            if {"inicio", "corte", "fin"}.issubset(row) and not (
                row["inicio"] < row["corte"] <= row["fin"]
            ):
                raise IntegrityError(f"{name}: invalid window or cutoff")
            if name == "quality_scores":
                grain = tuple(
                    row[k]
                    for k in ("id_produccion", "tipo_ventana", "inicio", "fin", "seleccion_id")
                )
                if grain in score_grains:
                    raise IntegrityError("quality_scores: multiple results for the same window")
                score_grains.add(grain)
            if (
                "id_produccion" in row
                and row["id_produccion"] is not None
                and "productions" in tables
                and row["id_produccion"] not in productions
            ):
                raise IntegrityError(f"{name}: unknown production")
            if (
                name in {"mia_reference_members", "mia_reference_values"}
                and "mia_reference_sets" in tables
                and row["reference_id"] not in references
            ):
                raise IntegrityError(f"{name}: unknown reference population")
            if (
                (name.endswith("_details") or name == "quality_score_components")
                and "quality_scores" in tables
                and row["id_resultado"] not in scores
            ):
                raise IntegrityError(f"{name}: detail without official complete score")
    if "quality_score_components" in tables:
        for identity, score in scores.items():
            parts = [r for r in tables["quality_score_components"] if r["id_resultado"] == identity]
            if {r["componente"] for r in parts} != {
                "MIA",
                "Critico",
                "Grave",
                "Mayor",
                "Menor",
                "Leve",
                "DAR",
                "ZC",
            }:
                raise IntegrityError("Official score requires every component")
            total = round(sum(r["contribucion"] for r in parts), 4)
            weight = round(sum(r["peso"] for r in parts), 4)
            if total != score["total_penalty"] or weight != score["evaluated_weight"]:
                raise IntegrityError("Score components and total/weight do not reconcile")
            if weight <= 0 or round(total / weight, 4) != score["normalized_score"]:
                raise IntegrityError("Normalized score does not reconcile")
