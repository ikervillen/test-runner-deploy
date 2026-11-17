"""Shared business identities and conservative reconciliation of accepted versions."""

from datetime import date, datetime
from decimal import Decimal

from . import cont_zc, lista_ar, lista_defectos, lista_rech, mp9, topology
from .common import IntegrityError, digest
from .contracts import CONTRACTS, DictionaryContract


def portable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: portable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [portable(v) for v in value]
    return value


def business_key(family, row):
    contract = CONTRACTS[family]
    value = portable(row)
    if isinstance(contract, DictionaryContract):
        key = value[contract.key]
    elif family in {"CATALOGO_PRODUCCIONES", "ASIGNACION_PRODUCCION_RAMAL"}:
        key = value[contract.key]
    elif family in topology.FAMILIES:
        key = topology.key(family, value)
    else:
        model = {
            "MP9_CAMBIO_MODELO": mp9,
            "LISTA_AR": lista_ar,
            "LISTA_DEFECTOS": lista_defectos,
            "LISTA_RECH": lista_rech,
            "CONT_ZC": cont_zc,
        }.get(family)
        key = model.key(value) if model is not None else value[contract.key]
    return digest([family, key])


def payload_hash(row):
    return digest(portable(row))


def review_hash(family, row):
    value = portable(row)
    if family == "CONT_ZC":
        for field in cont_zc.SPEEDS:
            value[field] = cont_zc.speed(value[field])
    return digest(value)


def accepted_extension(family, old, new):
    """Only choose a published row that extends another; never synthesize a row."""
    if old == new:
        return True
    nullable_closures = {
        "MP9_CAMBIO_MODELO": {"FECHA_OFF"},
        "CATALOGO_PRODUCCIONES": {"fecha_fin_zf", "ID_ST1_REL"},
        "LISTA_AR": {"FECHA_OFF"},
        **{family: {"vigente_hasta"} for family in topology.FAMILIES},
    }.get(family, set())
    for name in old:
        if old[name] == new.get(name):
            continue
        if name in nullable_closures and old[name] is None and new.get(name) is not None:
            continue
        if family == "MP9_CAMBIO_MODELO" and name == "ZF_OK" and old[name] == 0 and new[name] == 1:
            continue
        return False
    return True


def reconcile(family, versions):
    """Versions carry row, source_table and source_version from confirmed manifests."""
    by_lineage = {}
    for item in versions:
        lineage = item["source_table"]
        previous = by_lineage.get(lineage)
        rank = (
            item["source_version"],
            item.get("change_type", "insert") in {"insert", "update_postimage"},
        )
        before = (
            (
                previous["source_version"],
                previous.get("change_type", "insert") in {"insert", "update_postimage"},
            )
            if previous
            else None
        )
        if previous is None or rank > before:
            by_lineage[lineage] = item
        elif rank == before and item["row"] != previous["row"]:
            raise IntegrityError("One confirmed source revision has contradictory rows")
    candidates = [
        r
        for r in by_lineage.values()
        if r.get("change_type", "insert") in {"insert", "update_postimage"}
    ]
    if not candidates:
        return None
    if len(candidates) != len(by_lineage):
        raise IntegrityError("CROSS_LANE_DELETION_CONFLICT: deletion requires explicit lineage")
    for candidate in candidates:
        if all(accepted_extension(family, other["row"], candidate["row"]) for other in candidates):
            return candidate["row"]
    raise IntegrityError("CROSS_LANE_CONFLICT: accepted versions require explicit review")
