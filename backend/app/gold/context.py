"""Project accepted context; never infer a fabrication from CSV folder proximity."""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise

from .common import IntegrityError, digest


def stamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def capacity_revisions(rows: list[dict], previous: list[dict]) -> list[dict]:
    """A late closure is the same episode with a higher material revision."""
    accepted = {r["id_capacidad"]: r for r in previous}
    for row in rows:
        old = accepted.get(row["id_capacidad"])
        if old is not None:
            changed = any(row[k] != v for k, v in old.items() if k != "revision")
            row["revision"] = old["revision"] + int(changed)
    return rows


def issue(source: str, reason: str, production: str | None = None, **extra) -> dict:
    return {
        "source": source,
        "reason_code": reason,
        "id_produccion": production,
        "estado": "WAITING_DEPENDENCY",
        **extra,
    }


def catalog_mp9(rows, bundle):
    """Apply closures verified by the published Silver catalogue to its source rows."""
    if not bundle.get("historical_mp9_closures"):
        return rows
    from ingesta_delta_lake import mp9
    from ingesta_delta_lake.pipeline_rows import portable

    effective = {mp9.key(row): portable(row) for row in rows}
    for composition in bundle["catalog_composition"].values():
        if composition["coverage"] != "VALID":
            continue
        for accepted in (composition["root"], *composition["zf"]):
            key = mp9.key(accepted)
            if key not in effective or accepted["FECHA_OFF"] is None:
                continue
            candidate, conflicts = mp9.consolidate([effective[key], accepted])
            if conflicts:
                raise IntegrityError("Published catalogue contradicts its MP9 source")
            effective[key] = candidate
    return list(effective.values())


def productions(
    catalog: list[dict],
    mp9: list[dict],
    models: list[dict],
    machines: list[dict],
    boundaries: list[dict],
) -> tuple[list[dict], list[dict]]:
    changes = {(r["origen_fenix"], r["ID"]): r for r in mp9}
    by_model = {r["id_modelo_fenix"]: r["codigo_modelo_botella"] for r in models}
    by_boundary = {r["id_produccion"]: r for r in boundaries}
    rows, pending = [], []
    for accepted in catalog:
        identity, origin = accepted["id_produccion"], accepted["origen_fenix"]
        root = changes.get((origin, accepted["id_zc_raiz"]))
        if root is None or root["STATUS"] != 1:
            pending.append(issue("productions", "MISSING_ZC_ROOT", identity))
            continue
        children = [
            r
            for r in mp9
            if r["origen_fenix"] == origin and r["STATUS"] == 2 and r["ID_ST1_REL"] == root["ID"]
        ]
        if any(r["ID_MODELO"] != root["ID_MODELO"] for r in children):
            raise IntegrityError("Accepted ZC/ZF composition has different bottle models")
        machine = [
            r
            for r in machines
            if r["id_maquina_fenix"] == root["ID_MAQUINA"]
            and stamp(r["vigente_desde"]) <= stamp(root["FECHA_ON"])
            and (r["vigente_hasta"] is None or stamp(root["FECHA_ON"]) < stamp(r["vigente_hasta"]))
        ]
        if len(machine) != 1:
            pending.append(issue("productions", "MACHINE_UNRESOLVED", identity))
        boundary = by_boundary.get(identity)
        if boundary is None or not boundary.get("double_checked"):
            pending.append(issue("productions", "OPERATIONAL_DOUBLE_CHECK_PENDING", identity))
            boundary = None
        end = stamp(accepted["fecha_fin_zf"])
        if end is not None and (not children or any(r["FECHA_OFF"] is None for r in children)):
            raise IntegrityError("Catalogue closes a fabrication with open/missing ZF records")
        if end is not None and end != max(stamp(r["FECHA_OFF"]) for r in children):
            raise IntegrityError("Catalogue ZF end differs from its own linked records")
        rows.append(
            {
                "id_produccion": identity,
                "planta": accepted["planta"],
                "origen_fenix": origin,
                "linea": accepted["linea"],
                "id_maquina_fenix": root["ID_MAQUINA"],
                "maquina_fisica": machine[0]["nombre_maquina"] if len(machine) == 1 else None,
                "id_zc_raiz": root["ID"],
                "id_modelo_fenix": root["ID_MODELO"],
                "codigo_modelo_botella": by_model.get(root["ID_MODELO"]),
                "fecha_inicio_zc": stamp(root["FECHA_ON"]),
                "fecha_fin_zc": stamp(root["FECHA_OFF"]),
                "fecha_inicio_zf": min((stamp(r["FECHA_ON"]) for r in children), default=None),
                "fecha_fin_zf": end,
                "fecha_inicio_operativo_zc": stamp(boundary["start"]) if boundary else None,
                "criterio_inicio_operativo": boundary["rule"] if boundary else None,
                "estado_produccion": "CLOSED" if end is not None else "OPEN",
                "estado_dato": "VALID"
                if len(machine) == 1 and root["ID_MODELO"] in by_model
                else "PENDING",
            }
        )
    return rows, pending


def assignments(accepted: list[dict], prods: list[dict], presences: list[dict]) -> list[dict]:
    by_id = {p["id_produccion"]: p for p in prods}
    result = []
    for row in [*accepted, *presences]:
        p = by_id.get(row["id_produccion"])
        if p is None:
            raise IntegrityError("Assignment refers to a missing production")
        result.append(
            {
                "id_asignacion": row["id_asignacion"],
                "id_produccion": p["id_produccion"],
                "planta": p["planta"],
                "origen_fenix": p["origen_fenix"],
                "linea": p["linea"],
                "id_maquina_fenix": p["id_maquina_fenix"],
                "zona": row["zona"],
                "ramal": row.get("ramal"),
                "vigente_desde": stamp(row["vigente_desde"]),
                "vigente_hasta": stamp(row.get("vigente_hasta")),
                "estado_asignacion": "CONFIRMED",
                "version_regla": row["version_regla"],
                "revision": row["revision_decision"],
                "referencia_evidencia": row["evidencia_origen"],
            }
        )
    return result


def capacity(
    prods: list[dict], parameters: list[dict], machines: list[dict], drops: list[dict]
) -> tuple[list[dict], list[dict]]:
    drop_values = {r["id_gotas"]: r["valor"] for r in drops}
    result, pending = [], []
    for p in prods:
        identity = p["id_produccion"]
        params = sorted(
            [
                r
                for r in parameters
                if r["origen_fenix"] == p["origen_fenix"]
                and r["id_cambio_modelo"] == p["id_zc_raiz"]
            ],
            key=lambda r: stamp(r["fecha_on"]),
        )
        if not params or p["fecha_inicio_operativo_zc"] is None:
            pending.append(issue("production_capacity", "PARAMETERS_OR_BOUNDARY_MISSING", identity))
            continue
        for param in params:
            start = max(stamp(param["fecha_on"]), p["fecha_inicio_operativo_zc"])
            end_candidates = [stamp(param["fecha_off"]), p["fecha_fin_zf"]]
            physical = [
                r
                for r in machines
                if r["id_maquina_fenix"] == p["id_maquina_fenix"]
                and stamp(r["vigente_desde"]) <= start
                and (r["vigente_hasta"] is None or start < stamp(r["vigente_hasta"]))
            ]
            if len(physical) != 1 or physical[0]["id_gotas"] not in drop_values:
                pending.append(issue("production_capacity", "GOBS_CONFIGURATION_MISSING", identity))
                continue
            if physical[0]["vigente_hasta"] is not None:
                end_candidates.append(stamp(physical[0]["vigente_hasta"]))
            end = min((x for x in end_candidates if x is not None), default=None)
            if end is not None and end <= start:
                continue
            n, g = param["num_secciones"], drop_values[physical[0]["id_gotas"]]
            if type(n) is not int or type(g) is not int or n <= 0 or g <= 0:
                raise IntegrityError(
                    "Confirmed assigned capacity must have positive integer inputs"
                )
            result.append(
                {
                    "id_capacidad": digest([identity, param["id"]]),
                    "id_produccion": identity,
                    "id_maquina_fenix": p["id_maquina_fenix"],
                    "numero_secciones": n,
                    "numero_gotas": g,
                    "cavidades_asignadas": n * g,
                    "vigente_desde": start,
                    "vigente_hasta": end,
                    "estado_capacidad": "CONFIRMED",
                    "version_regla": "assigned-sections-gobs-v1",
                    "revision": 1,
                    "referencia_evidencia": f"production_parameters:{param['id']}",
                }
            )
        intervals = sorted(
            (r for r in result if r["id_produccion"] == identity), key=lambda r: r["vigente_desde"]
        )
        for left, right in pairwise(intervals):
            if left["vigente_hasta"] is None or left["vigente_hasta"] > right["vigente_desde"]:
                raise IntegrityError("Capacity episodes overlap without an accepted correction")
    return result, pending
