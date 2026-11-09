"""Mandatory temporal/physical contrast, including an explicit inconclusive result."""

from __future__ import annotations

from datetime import timedelta

from .common import digest
from .context import issue, stamp


def derive(
    catalog: list[dict], hot: list[dict], links: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Links must be independently accepted production/machine/mould/cavity evidence.

    No mapping is inferred from the very timestamp whose correctness is tested.
    No model in a hot rejection means inconclusive contrast, not proof of shutdown.
    """
    result, pending = [], []
    for p in catalog:
        declared = stamp(p["fecha_inicio_zc"])
        proposed = declared.replace(second=0, microsecond=0)
        if proposed.minute % 10 or proposed != declared:
            proposed += timedelta(minutes=10 - proposed.minute % 10)
        relevant = [
            r
            for r in hot
            if r["origen_fenix"] == p["origen_fenix"]
            and r["id_maquina_fenix"] == p["ID_MAQUINA"]
            and declared - timedelta(minutes=10) <= stamp(r["fecha"])
            and (p["fecha_fin_zf"] is None or stamp(r["fecha"]) <= stamp(p["fecha_fin_zf"]))
        ]
        incoming, outgoing, checked, contradiction = [], [], [], False
        previous = [
            q
            for q in catalog
            if q["origen_fenix"] == p["origen_fenix"]
            and q["ID_MAQUINA"] == p["ID_MAQUINA"]
            and stamp(q["fecha_inicio_zc"]) < declared
        ]
        previous_id = (
            max(previous, key=lambda q: stamp(q["fecha_inicio_zc"]))["id_produccion"]
            if previous
            else None
        )
        for r in relevant:
            end, start = stamp(r["fecha"]), stamp(r["fecha"]) - timedelta(minutes=10)
            cavity = f"{r['seccion']}{r['cavidad']}"
            matches = [
                link
                for link in links
                if link["origen_fenix"] == r["origen_fenix"]
                and link["id_maquina_fenix"] == r["id_maquina_fenix"]
                and link["molde"] == r["molde"]
                and link["cavidad"] == cavity
                and stamp(link["vigente_desde"]) <= start
                and (link["vigente_hasta"] is None or end <= stamp(link["vigente_hasta"]))
            ]
            identities = {m["id_produccion"] for m in matches}
            checked.append([r["id"], sorted(identities)])
            if len(identities) > 1:
                contradiction = True
            elif identities == {p["id_produccion"]}:
                incoming.append(start)
            elif previous_id is not None and identities == {previous_id} and end > proposed:
                outgoing.append(end)
        if incoming and outgoing and max(outgoing) > min(incoming):
            contradiction = True
        if contradiction:
            pending.append(
                issue(
                    "operational_boundaries", "INCOMPATIBLE_PHYSICAL_EVIDENCE", p["id_produccion"]
                )
            )
            continue
        revised = max([proposed, *outgoing])
        if incoming and min(incoming) < proposed and not outgoing:
            revised = min(incoming)
        physical_result = "CONFIRMED" if incoming or outgoing else "INCONCLUSIVE"
        result.append(
            {
                "id_produccion": p["id_produccion"],
                "start": revised,
                "rule": "zc-physical-override-v1"
                if revised != proposed
                else "zc-temporal-approximation-v1",
                "double_checked": True,
                "physical_result": physical_result,
                "checked_rows": len(checked),
                "evidence_hash": digest(checked),
            }
        )
    return result, pending
