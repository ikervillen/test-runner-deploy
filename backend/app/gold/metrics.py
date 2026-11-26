"""Base amounts before rates/scores. Physical measurements retain one identity."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from .ar_units import affected_unit, continuity
from .common import IntegrityError, PendingBasis, digest


@dataclass(frozen=True)
class Window:
    production_id: str
    kind: str
    start: datetime
    end: datetime
    cutoff: datetime
    selection_id: str = "all"

    def __post_init__(self):
        if not self.start < self.cutoff <= self.end:
            raise IntegrityError("A window requires start < effective cutoff <= end")

    @property
    def key(self) -> dict:
        return dict(
            id_produccion=self.production_id,
            tipo_ventana=self.kind,
            inicio=self.start,
            fin=self.end,
            corte=self.cutoff,
            seleccion_id=self.selection_id,
        )

    @property
    def identity(self) -> str:
        if self.kind == "PRODUCTION":
            return digest([self.production_id, self.kind, self.selection_id])
        return digest([self.production_id, self.kind, self.start, self.end, self.selection_id])

    def includes_sample(self, row: dict) -> bool:
        return self.start <= row["periodo_inicio"] and row["periodo_fin"] <= self.cutoff


def live_windows(
    production_id: str, now: datetime, *, delay: timedelta = timedelta(hours=1)
) -> list[Window]:
    cutoff = now - delay
    hour = cutoff.replace(minute=0, second=0, microsecond=0)
    result = [
        Window(
            production_id,
            "HOUR",
            hour - timedelta(hours=n),
            hour - timedelta(hours=n - 1),
            hour - timedelta(hours=n - 1),
        )
        for n in range(7, 0, -1)
    ]
    if cutoff > hour:
        result.append(Window(production_id, "HOUR", hour, hour + timedelta(hours=1), cutoff))
    return result


def production_windows(p: dict, cutoff: datetime) -> list[Window]:
    start = p["fecha_inicio_operativo_zc"]
    if start is None:
        return []
    end = min(p["fecha_fin_zf"] or cutoff, cutoff)
    if end <= start:
        return []
    result = [Window(p["id_produccion"], "PRODUCTION", start, end, end)]
    current = start.replace(hour=6, minute=0, second=0, microsecond=0)
    if current > start:
        current -= timedelta(days=1)
    while current < end:
        following = current + timedelta(hours=8)
        left, right = max(start, current), min(end, following)
        if left < right:
            result.append(Window(p["id_produccion"], "SHIFT", left, following, right))
        current = following
    return result


def filtered(rows: list[dict], selection: dict | None) -> list[dict]:
    names = {
        "branches": "ramal",
        "machine_codes": "codigo_maquina_inspeccion",
        "cavities": "cavidad",
        "sections": "seccion",
        "colors": "color",
    }
    result = rows
    for name, values in (selection or {}).items():
        if not values:
            continue
        if name not in names:
            raise IntegrityError(f"Unsupported physical filter: {name}")
        column = names[name]
        if not result:
            return []
        if all(r.get(column) is None for r in result):
            raise IntegrityError(f"Filter {name} has no accredited source dimension")
        result = [r for r in result if r.get(column) in values]
    return result


def mia_bases(
    rejections: list[dict],
    inspections: list[dict],
    window: Window,
    *,
    selection: dict | None = None,
    defect_codes: set[str] | None = None,
) -> dict:
    by: dict[tuple, int] = {}
    for r in filtered(inspections, selection):
        if r["id_produccion"] == window.production_id and window.includes_sample(r):
            key = (
                r["id_produccion"],
                r["linea"],
                r["ramal"],
                r["periodo_inicio"],
                r["periodo_fin"],
            )
            by[key] = max(by.get(key, 0), r["inspeccionadas"])
    bases = [
        {
            "id_base": digest([key, window.selection_id]),
            "id_produccion": key[0],
            "linea": key[1],
            "ramal": key[2],
            "periodo_inicio": key[3],
            "periodo_fin": key[4],
            "seleccion_id": window.selection_id,
            "inspeccionadas": value,
        }
        for key, value in sorted(by.items())
    ]
    denominator = sum(by.values())
    rejected: defaultdict[tuple[str, str], int] = defaultdict(int)
    for r in filtered(rejections, selection):
        if (
            r["id_produccion"] == window.production_id
            and window.includes_sample(r)
            and (defect_codes is None or r["codigo_defecto"] in defect_codes)
        ):
            rejected[(r["espacio_defecto"], r["codigo_defecto"])] += r["rechazos"]
    basis = digest(["inspection-exposure", window.identity])
    return {
        "inspection_bases": bases,
        "exposure": {
            **window.key,
            "inspected_basis_id": basis,
            "inspeccionadas": denominator,
            "muestras": len(by),
        },
        "rates": [
            {
                "espacio_defecto": k[0],
                "codigo_defecto": k[1],
                "rechazos": v,
                "inspeccionadas": denominator,
                "percent": v / denominator * 100 if denominator else None,
            }
            for k, v in sorted(rejected.items())
            if v > 0
        ],
        "amounts": [
            {
                **window.key,
                "espacio_defecto": k[0],
                "codigo_defecto": k[1],
                "rechazos": v,
                "inspected_basis_id": basis,
            }
            for k, v in sorted(rejected.items())
        ],
    }


def microseconds(delta: timedelta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def merged_duration(intervals: list[tuple[datetime, datetime]]) -> tuple[int, int]:
    ordered = sorted((a, b) for a, b in intervals if a < b)
    if not ordered:
        return 0, 0
    start, end = ordered[0]
    duration, events = 0, 1
    for a, b in ordered[1:]:
        if a <= end:
            end = max(end, b)
        else:
            duration += microseconds(end - start)
            start, end = a, b
            events += 1
    return events, duration + microseconds(end - start)


def ar_bases(
    events: list[dict], capacities: list[dict], window: Window, *, production=None, conflicts=()
) -> dict:
    pieces = sorted(
        [
            c
            for c in capacities
            if c["id_produccion"] == window.production_id and c["estado_capacidad"] == "CONFIRMED"
        ],
        key=lambda r: r["vigente_desde"],
    )
    cursor, available = window.start, 0
    for row in pieces:
        left, right = (
            max(window.start, row["vigente_desde"]),
            min(window.cutoff, row["vigente_hasta"] or window.cutoff),
        )
        if left >= right:
            continue
        if left > cursor:
            raise PendingBasis(
                "CAPACITY_COVERAGE_GAP", "Capacity has a gap in the requested window"
            )
        if left < cursor:
            raise IntegrityError("Accepted capacity intervals overlap in the requested window")
        available += microseconds(right - left) * row["cavidades_asignadas"]
        cursor = right
    if cursor != window.cutoff or available <= 0:
        raise PendingBasis(
            "CAPACITY_COVERAGE_GAP", "Assigned capacity does not cover the entire requested window"
        )
    by = defaultdict(list)
    by_defect = defaultdict(list)
    production = production or {}
    own = [event for event in events if event["id_produccion"] == window.production_id]
    joined = continuity(own, production) if any(event["cavidad"] == "99Z" for event in own) else {}
    for event in own:
        left, right = (
            max(window.start, event["fecha_on"]),
            min(window.cutoff, event["fecha_off"] or window.cutoff),
        )
        if left >= right:
            continue
        unit = affected_unit(event, production, joined, conflicts)
        if event["categoria"] is None:
            raise PendingBasis(
                "AR_CATEGORY_UNRESOLVED", "An affecting AR event has no accepted severity category"
            )
        by[("CATEGORY", event["categoria"], unit)].append((left, right))
        if event.get("codigo_defecto") is not None:
            by_defect[(event["categoria"], event["codigo_defecto"], unit)].append((left, right))
        if event["grupo_dar"]:
            by[("DAR_GROUP", event["grupo_dar"], unit)].append((left, right))
            by[("DAR_TOTAL", "DAR", unit)].append((left, right))
    amounts: defaultdict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for (kind, code, _), intervals in by.items():
        count, duration = merged_duration(intervals)
        amounts[(kind, code)][0] += duration
        amounts[(kind, code)][1] += count
    basis = digest(["capacity-exposure", window.identity])
    defect_totals: defaultdict[tuple[str, str], int] = defaultdict(int)
    for (category, code, _), intervals in by_defect.items():
        defect_totals[(category, code)] += merged_duration(intervals)[1]
    top_defects = {}
    for category in {k[0] for k in defect_totals}:
        codes = sorted(code for c, code in defect_totals if c == category)
        top_defects[category] = max(codes, key=lambda code: defect_totals[(category, code)])
    return {
        "top_defects": top_defects,
        "exposure": {**window.key, "capacity_basis_id": basis, "tiempo_disponible_us": available},
        "amounts": [
            {
                **window.key,
                "ambito": kind,
                "codigo_ambito": code,
                "tiempo_afectado_us": val[0],
                "capacity_basis_id": basis,
                "eventos": val[1],
            }
            for (kind, code), val in sorted(amounts.items())
        ],
    }


def zc_bases(rows: list[dict], window: Window, allowed_ids: set[str]) -> list[dict]:
    by: defaultdict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0])
    seen: set[str] = set()
    group_ids: defaultdict[tuple, set[str]] = defaultdict(set)
    for r in rows:
        identity = r["id_observacion"]
        if identity in seen or identity not in allowed_ids or not window.includes_sample(r):
            continue
        seen.add(identity)
        key = (r["origen_fenix"], r["id_maquina_contador"])
        group_ids[key].add(identity)
        by[key][0] += r["rueda"]
        by[key][1] += r["teorico_total"]
        by[key][2] += 1
    return [
        {
            "id_base_zc": digest(
                [
                    key,
                    window.kind,
                    window.start,
                    window.end,
                    window.cutoff,
                    window.selection_id,
                    sorted(group_ids[key]),
                ]
            ),
            "origen_fenix": key[0],
            "id_maquina_contador": key[1],
            "tipo_ventana": window.kind,
            "inicio": window.start,
            "fin": window.end,
            "corte": window.cutoff,
            "seleccion_id": window.selection_id,
            "rueda": value[0],
            "teoricos": value[1],
            "muestras": value[2],
        }
        for key, value in sorted(by.items())
    ]
