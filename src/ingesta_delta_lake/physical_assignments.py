"""Vigencias declaradas ZF + topología vigente; sin reglas por nombres A/B."""

from . import topology as t
from .common import digest

ACTOR = "rule:declared_zf_topology_v2"


def decisions(bundle, previous, codes):
    from .associations import source_id

    result = []
    machines = bundle["sources"][t.MACHINES]
    branches = bundle["sources"][t.BRANCHES]
    for prod in bundle["sources"]["CATALOGO_PRODUCCIONES"]:
        composition = bundle["catalog_composition"].get(prod["id_produccion"], {})
        if composition.get("coverage") != "VALID":
            continue
        for machine in machines:
            if (
                machine["id_maquina_fenix"] != prod["ID_MAQUINA"]
                or machine["codigo_linea_zc"] != codes[prod["origen_fenix"]]
            ):
                continue
            for branch in branches:
                if (
                    branch["origen_fenix"] != prod["origen_fenix"]
                    or branch["id_maquina_fenix"] != prod["ID_MAQUINA"]
                ):
                    continue
                for cold in composition["zf"]:
                    start = max(cold["FECHA_ON"], machine["vigente_desde"], branch["vigente_desde"])
                    ends = [
                        e
                        for e in (
                            cold["FECHA_OFF"],
                            machine["vigente_hasta"],
                            branch["vigente_hasta"],
                        )
                        if e is not None
                    ]
                    end = min(ends) if ends else None
                    if end is not None and end <= start:
                        continue
                    identity = "auto_" + digest(
                        [
                            prod["id_produccion"],
                            cold["ID"],
                            t.key(t.MACHINES, machine),
                            t.key(t.BRANCHES, branch),
                        ]
                    )
                    old = previous.get(identity)
                    # Una corrección humana explícita del episodio no se deshace al derivar.
                    if old and old["actor"] != ACTOR:
                        continue
                    d = {
                        "id": identity,
                        "kind": "ZF_ASSOCIATION",
                        "origin": prod["origen_fenix"],
                        "branch": branch["codigo_ramal"],
                        "production": prod["id_produccion"],
                        "start": start,
                        "end": end,
                        "revision": 1,
                        "previous_hash": None,
                        "actor": ACTOR,
                        "reason": "Vigencia declarada ZF y catálogos físicos versionados",
                        "evidence": [
                            {"family": f, "source_id": source_id(f, r)}
                            for f, r in [
                                ("CATALOGO_PRODUCCIONES", prod),
                                (t.MACHINES, machine),
                                (t.BRANCHES, branch),
                            ]
                        ],
                    }
                    if old:
                        if all(
                            old[k] == v
                            for k, v in d.items()
                            if k not in {"revision", "previous_hash"}
                        ):
                            d = old
                        else:
                            d.update(revision=old["revision"] + 1, previous_hash=digest(old))
                    result.append(d)
    return result


def branch_for_period(bundle, origin, branch, start, end):
    machines = {t.key(t.MACHINES, r): r for r in bundle["sources"][t.MACHINES]}
    choices = []
    for r in bundle["sources"][t.BRANCHES]:
        if r["origen_fenix"] != origin or r["codigo_ramal"] != branch:
            continue
        if t.affected(t.BRANCHES, bundle["cases"][t.BRANCHES], r):
            return None
        if r["vigente_desde"] <= start and (
            r["vigente_hasta"] is None or end <= r["vigente_hasta"]
        ):
            m = t.machine_for_period(
                machines, bundle["cases"][t.MACHINES], r["id_maquina_fenix"], start, end
            )
            if m and m["codigo_linea_zc"] == bundle["line_codes"][origin]:
                choices.append(r)
    return choices[0] if len(choices) == 1 else None


def outgoing(items, start, end, prods):
    """Convención aprobada: la muestra que cruza el cambio pertenece a la saliente."""
    pieces = sorted(
        (
            d
            for d in items
            if "production" in d and d["start"] < end and (d["end"] is None or start < d["end"])
        ),
        key=lambda d: d["start"],
    )
    if len(pieces) not in (1, 2):
        return None
    old = pieces[0]
    if old["end"] is None or not old["start"] <= start < old["end"] < end:
        return None
    if len(pieces) == 2:
        new = pieces[1]
        if (
            new["start"] < old["end"]
            or (new["end"] is not None and new["end"] < end)
            or old["production"] == new["production"]
            or prods[old["production"]]["ID_MAQUINA"] != prods[new["production"]]["ID_MAQUINA"]
        ):
            return None
    return "TRANSITION_TO_OUTGOING", [old["production"]], [p["id"] for p in pieces]
