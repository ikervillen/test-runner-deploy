"""Conformed dimensions and selective type-2 history without invented effective dates.

Physical machine validity comes from accepted Silver. Catalogs without an approved
effective date retain NULL business validity and track knowledge/recording time.
Historical score FKs remain pinned to their original attribute versions.
"""

from __future__ import annotations

from datetime import date, datetime
from itertools import pairwise

from .common import IntegrityError, digest
from .context import stamp
from .schemas import DIMENSIONAL_FACTS

ATTRIBUTES = {
    "dim_machine": ("planta", "id_maquina_fenix", "nombre", "codigo_linea_zc"),
    "dim_model": (
        "planta",
        "codigo_modelo_botella",
        "segmento_marble",
        "segmento_calidad",
        "profile_id",
        "tipo_cierre",
        "estado",
        "estado_dar",
        "estado_modelo",
    ),
    "dim_defect": ("planta", "espacio_codigo", "codigo", "descripcion", "categoria"),
}


def reconcile(name, candidates, previous, recorded_at):
    """Type-2 attributes; closures revise an interval without losing old publications."""
    result = [dict(r) for r in previous]
    current = {}
    for row in result:
        if row["is_current"]:
            key = (row["entity_key"], row["valid_from"])
            if key in current:
                raise IntegrityError(f"{name}: multiple current beliefs about one interval")
            current[key] = row
    seen = {}
    for candidate in candidates:
        row = dict(candidate)
        if row["valid_to"] is not None and (
            row["valid_from"] is None or row["valid_to"] <= row["valid_from"]
        ):
            raise IntegrityError(f"{name}: invalid effective interval")
        attributes = {k: row[k] for k in ATTRIBUTES[name]}
        fingerprint = digest(attributes)
        slot = (row["entity_key"], row["valid_from"])
        if slot in seen and seen[slot] != (fingerprint, row["valid_to"]):
            raise IntegrityError(f"{name}: conflicting source versions")
        if slot in seen:
            continue
        seen[slot] = (fingerprint, row["valid_to"])
        old = current.get(slot)
        if old and old["attributes_hash"] == fingerprint:
            if recorded_at < old["recorded_from"]:
                raise IntegrityError("SCD recording time cannot move backwards")
            old.update(
                valid_to=row["valid_to"],
                source_version=row["source_version"],
                source_reference=row["source_reference"],
            )
            continue
        if old:
            if recorded_at <= old["recorded_from"]:
                raise IntegrityError("Changed attributes require a later recording time")
            old.update(is_current=False, recorded_to=recorded_at)
        row.update(
            dimension_key=digest(
                [name, row["entity_key"], row["valid_from"], fingerprint, recorded_at]
            ),
            recorded_from=recorded_at,
            recorded_to=None,
            is_current=True,
            attributes_hash=fingerprint,
        )
        result.append(row)
        current[slot] = row
    intervals = {}
    for row in result:
        if row["is_current"] and row["valid_from"] is not None:
            intervals.setdefault(row["entity_key"], []).append(row)
    for group in intervals.values():
        ordered = sorted(group, key=lambda r: r["valid_from"])
        for left, right in pairwise(ordered):
            if left["valid_to"] is None or left["valid_to"] > right["valid_from"]:
                raise IntegrityError(f"{name}: overlapping accepted effective intervals")
    return sorted(result, key=lambda r: r["dimension_key"])


def materialize(tables, *, plant, machines, recorded_at, previous=None):
    previous = previous or {}
    plant_key = digest(["plant", plant])
    tables["dim_plant"] = [{"plant_key": plant_key, "planta": plant}]

    def candidate(entity, version, reference, attributes, start=None, end=None):
        return dict(
            entity_key=digest(entity),
            valid_from=stamp(start),
            valid_to=stamp(end),
            source_version=str(version),
            source_reference=reference,
            **attributes,
        )

    machine_rows = [
        candidate(
            [plant, r["id_maquina_fenix"]],
            digest(r),
            "SIP_MAQUINA_MOLDEO:" + str(r["id_maquina_fenix"]),
            dict(
                planta=plant,
                id_maquina_fenix=r["id_maquina_fenix"],
                nombre=r["nombre_maquina"],
                codigo_linea_zc=r.get("codigo_linea_zc"),
            ),
            r["vigente_desde"],
            r.get("vigente_hasta"),
        )
        for r in machines
    ]
    segments = {r["codigo_modelo_botella"]: r for r in tables["model_segment_catalog"]}
    models = {
        p["codigo_modelo_botella"]
        for p in tables["productions"]
        if p["codigo_modelo_botella"] is not None
    }
    model_rows = []
    for model in sorted(models):
        mapping = segments.get(model, {})
        attrs = {
            k: mapping.get(k)
            for k in (
                "segmento_marble",
                "segmento_calidad",
                "profile_id",
                "tipo_cierre",
                "estado_dar",
                "estado_modelo",
            )
        }
        attrs.update(
            planta=plant,
            codigo_modelo_botella=model,
            estado=mapping.get("estado_correspondencia", "PENDING"),
        )
        model_rows.append(
            candidate(
                [plant, model],
                mapping.get("catalog_version", "UNRESOLVED"),
                mapping.get("referencia_evidencia", "P01_PENDING"),
                attrs,
            )
        )
    defect_rows = [
        candidate(
            [plant, r["espacio_codigo"], r["codigo"]],
            r["catalog_version"],
            r["referencia_evidencia"],
            dict(
                planta=plant,
                espacio_codigo=r["espacio_codigo"],
                codigo=r["codigo"],
                descripcion=r["descripcion"],
                categoria=r["categoria"],
            ),
        )
        for r in tables["defect_catalog"]
    ]
    for name, candidates in (
        ("dim_machine", machine_rows),
        ("dim_model", model_rows),
        ("dim_defect", defect_rows),
    ):
        tables[name] = reconcile(name, candidates, previous.get(name, []), recorded_at)
    by_model = {
        r["codigo_modelo_botella"]: r["dimension_key"]
        for r in tables["dim_model"]
        if r["is_current"]
    }
    by_defect = {
        (r["espacio_codigo"], r["codigo"]): r["dimension_key"]
        for r in tables["dim_defect"]
        if r["is_current"]
    }
    physical = [r for r in tables["dim_machine"] if r["is_current"]]
    productions = {p["id_produccion"]: p for p in tables["productions"]}
    dates = {r["date_key"]: r for r in previous.get("dim_date", [])}
    context = DimensionLookup(plant_key, productions, physical, by_model, by_defect, dates)
    spilled = getattr(tables, "spilled", None)
    for name in DIMENSIONAL_FACTS:
        if spilled and name in spilled:
            continue
        for row in tables.get(name, []):
            attach_dimensions(name, row, context)
    tables["dim_date"] = sorted(dates.values(), key=lambda r: r["date_key"])
    # Spilled facts are stamped by the caller, then checked with the full link pass.
    validate_links(tables, include_spilled=False)
    return context


class DimensionLookup:
    def __init__(self, plant_key, productions, physical, by_model, by_defect, dates):
        self.plant_key = plant_key
        self.productions = productions
        self.physical = physical
        self.by_model = by_model
        self.by_defect = by_defect
        self.dates = dates


def attach_dimensions(name: str, row: dict, lookup: DimensionLookup) -> dict:
    """Stamp one fact. A closed score keeps the dimension keys it was published with."""
    p = lookup.productions.get(row.get("id_produccion"), {})
    instant = next(
        (
            row[k]
            for k in ("periodo_inicio", "fecha_on", "inicio", "fecha_inicio_zc")
            if row.get(k) is not None
        ),
        None,
    )
    day = (
        instant.date()
        if isinstance(instant, datetime)
        else instant
        if isinstance(instant, date)
        else None
    )
    date_key = int(day.strftime("%Y%m%d")) if day else None
    if day:
        lookup.dates[date_key] = dict(
            date_key=date_key,
            fecha=day,
            year=day.year,
            month=day.month,
            day=day.day,
            weekday=day.isoweekday(),
        )
    matched = [
        m
        for m in lookup.physical
        if m["id_maquina_fenix"] == p.get("id_maquina_fenix")
        and instant is not None
        and m["valid_from"] <= instant
        and (m["valid_to"] is None or instant < m["valid_to"])
    ]
    if len(matched) > 1:
        raise IntegrityError("Ambiguous temporal machine dimension")
    if name == "quality_scores" and row.get("plant_key"):
        return row
    row.update(
        plant_key=lookup.plant_key,
        date_key=date_key,
        machine_key=matched[0]["dimension_key"] if matched else None,
        model_key=lookup.by_model.get(p.get("codigo_modelo_botella")),
        defect_key=lookup.by_defect.get((row.get("espacio_defecto"), row.get("codigo_defecto"))),
    )
    return row


def _iter_rows(tables, name):
    spilled = getattr(tables, "spilled", None)
    if spilled and name in spilled:
        return tables.store.iter_rows(name)
    return tables.get(name, [])


def validate_links(tables, *, include_spilled=True):
    dimensions = {
        "plant_key": ("dim_plant", "plant_key"),
        "date_key": ("dim_date", "date_key"),
        "machine_key": ("dim_machine", "dimension_key"),
        "model_key": ("dim_model", "dimension_key"),
        "defect_key": ("dim_defect", "dimension_key"),
    }
    for field, (name, key) in dimensions.items():
        identities = {r[key] for r in tables.get(name, [])}
        for fact in DIMENSIONAL_FACTS:
            if not include_spilled and getattr(tables, "spilled", None) and fact in tables.spilled:
                continue
            for row in _iter_rows(tables, fact):
                if row.get(field) is not None and row[field] not in identities:
                    raise IntegrityError(f"{fact}.{field}: missing dimension version")
