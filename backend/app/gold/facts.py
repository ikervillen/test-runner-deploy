"""Accepted Silver projections. Missing attribution is visible, never guessed."""

from __future__ import annotations

from .attribution import Attribution, interval, source_id
from .common import IntegrityError
from .context import stamp


def mia(family: str, rows: list[dict], attribution: Attribution) -> tuple[list[dict], list[dict]]:
    result, pending = [], []
    for row in rows:
        targets, decision = attribution.targets(family, row)
        if not targets:
            start, end = interval(row)
            pending.append(
                {
                    **decision,
                    "origin": row["origen_fenix"],
                    "period_start": start,
                    "period_end": end,
                }
            )
            continue
        p = targets[0]
        start, end = interval(row)
        base = {
            "id_observacion": source_id(family, row),
            "origen_fenix": row["origen_fenix"],
            "linea": p["linea"],
            "id_produccion": p["id_produccion"],
            "referencia_origen": f"{family}:{source_id(family, row)}",
            "periodo_inicio": start,
            "periodo_fin": end,
            "regla_atribucion": decision["rule"],
            "id_asignacion": decision["assignment_id"],
            "ramal": row["LINEA"],
            "codigo_maquina_inspeccion": row["COD_MAQ_ZF"],
            "molde": row["MOLDE"],
            "cavidad": row["CAVIDAD"],
            "seccion": None,
            "color": None,
        }
        if family == "LISTA_DEFECTOS":
            base.update(
                {
                    "espacio_defecto": "fenix_mia",
                    "codigo_defecto": str(row["COD_DEF"]),
                    "inspeccionadas_fuente": row["INSPECC"],
                    "rechazos": row["RECH"],
                    **{f"rech{i}": row[f"RECH{i}"] for i in range(1, 11)},
                }
            )
        else:
            base.update(
                {
                    "inspeccionadas": row["INSPECC"],
                    "rechazos_fuente": row["RECH"],
                    "rechazos_auto_fuente": row["RECH_AUTO"],
                    "fecha_r": stamp(row["FECHA_R"]),
                }
            )
        result.append(base)
    return result, pending


def ar(
    rows: list[dict],
    attribution: Attribution,
    defects: list[dict],
    status_catalog: list[dict],
    types: list[dict],
    profile_members: list[dict],
    model_segments: list[dict],
) -> tuple[list[dict], list[dict]]:
    by_code = {(d["espacio_codigo"], d["codigo"]): d for d in defects}
    by_status = {r["codigo_status"]: r for r in status_catalog}
    by_type = {r["codigo_tipo_ar"]: r for r in types}
    segments = {
        r["codigo_modelo_botella"]: r
        for r in model_segments
        if r["estado_correspondencia"] == "CONFIRMED"
    }
    result, pending = [], []
    for row in rows:
        targets, decision = attribution.targets("LISTA_AR", row)
        if not targets:
            pending.append(
                {
                    **decision,
                    "origin": row["origen_fenix"],
                    "period_start": stamp(row["FECHA_ON"]),
                    "period_end": stamp(row["FECHA_OFF"]),
                }
            )
            continue
        p = targets[0]
        identity = source_id("LISTA_AR", row)
        kind, status = by_type.get(row["TIPO"]), by_status.get(row["STATUS"])
        defect = by_code.get(("fenix_ar", str(row["COD_AR"])))
        if not kind or not status or not defect:
            pending.append(
                {
                    "source": "LISTA_AR",
                    "source_id": identity,
                    "id_produccion": p["id_produccion"],
                    "estado": "WAITING_DEPENDENCY",
                    "reason_code": "AR_CATALOG_UNRESOLVED",
                }
            )
            continue
        zone = decision.get("zone") or kind.get("zona_interpretada")
        if zone not in {"ZC", "ZF"}:
            pending.append(
                {
                    "source": "LISTA_AR",
                    "source_id": identity,
                    "id_produccion": p["id_produccion"],
                    "estado": "WAITING_DEPENDENCY",
                    "reason_code": "AR_ZONE_UNRESOLVED",
                }
            )
            continue
        segment = segments.get(p["codigo_modelo_botella"])
        members = [
            m
            for m in profile_members
            if segment
            and m["profile_id"] == segment["profile_id"]
            and m["espacio_codigo"] == "fenix_ar"
            and m["codigo"] == str(row["COD_AR"])
            and (
                m["aplicabilidad_cierre"] is None
                or m["aplicabilidad_cierre"] == segment["tipo_cierre"]
            )
        ]
        if len(members) > 1:
            raise IntegrityError("An AR code belongs to multiple applicable DAR groups")
        result.append(
            {
                "id_tramo": identity,
                "id_observacion": identity,
                "origen_fenix": row["origen_fenix"],
                "linea": p["linea"],
                "referencia_origen": f"LISTA_AR:{identity}",
                "id_produccion": p["id_produccion"],
                "id_asignacion": decision.get("assignment_id"),
                "codigo_ar_original": row["COD_AR"],
                "espacio_defecto": "fenix_ar",
                "codigo_defecto": str(row["COD_AR"]),
                "fecha_on": stamp(row["FECHA_ON"]),
                "fecha_off": stamp(row["FECHA_OFF"]),
                "maquina_is": row["MAQ_IS"],
                "cavidad": row["CAVIDAD"],
                "status_original": row["STATUS"],
                "tipo_original": row["TIPO"],
                "modelo_original": row["MODELO"],
                "color": row["COLOR"],
                "proceso": row["PROCESO"],
                "molde": row["MOLDE"],
                "id_agrupacion": row["ID_Agrupación"],
                "zona": zone,
                "categoria": defect["categoria"],
                "grupo_dar": members[0]["grupo_id"] if members else None,
                "regla_atribucion": decision["rule"],
            }
        )
    return result, pending


def zc(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        start, end = interval(row)
        identity = source_id("CONT_ZC", row)
        result.append(
            {
                "id_observacion": identity,
                "origen_fenix": row["origen_fenix"],
                "linea": row["origen_fenix"],
                "referencia_origen": f"CONT_ZC:{identity}",
                "id_maquina_contador": row["MAQ_IS"],
                "periodo_inicio": start,
                "periodo_fin": end,
                "rueda": row["RUEDA"],
                "teorico_a": row["TEORICO_A"],
                "teorico_b": row["TEORICO_B"],
                "teorico_total": row["TEORICO_A"] + row["TEORICO_B"],
                "salida_maquina_is": row["SALIDA_MAQ_IS"],
            }
        )
    return result
