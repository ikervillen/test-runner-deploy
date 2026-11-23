"""Vigencias declaradas y relevos operativos sobre topología versionada."""

from collections import defaultdict

from . import topology as t
from .common import digest

ACTOR = "rule:declared_zf_topology_v2"
ZC_ACTOR = "rule:declared_zc_topology_v1"
HANDOVER = "Relevo operativo MP9 acreditado en la misma posición física"


def zc_presence(bundle, previous, prod, composition, machine):
    """Use the ZC root, never the later global ZF closure, for shared-wheel presence."""
    from .associations import source_id

    root = composition["root"]
    start = max(root["FECHA_ON"], machine["vigente_desde"])
    ends = [e for e in (root["FECHA_OFF"], machine["vigente_hasta"]) if e is not None]
    end = min(ends) if ends else None
    if end is not None and end <= start:
        return None
    identity = "auto_zc_" + digest([prod["id_produccion"], t.key(t.MACHINES, machine)])
    old = previous.get(identity)
    if old and old["actor"] != ZC_ACTOR:
        return None
    decision = {
        "id": identity,
        "kind": "ZC_PRESENCE",
        "origin": prod["origen_fenix"],
        # CONT_ZC identifies the shared line/wheel, not the tandem half's ID.
        "machine": machine["codigo_linea_zc"],
        "production": prod["id_produccion"],
        "start": start,
        "end": end,
        "revision": 1,
        "previous_hash": None,
        "actor": ZC_ACTOR,
        "reason": "Presencia ZC declarada; rendimiento compartido de rueda sin repartir botellas",
        "evidence": [
            {"family": family, "source_id": source_id(family, row)}
            for family, row in [("CATALOGO_PRODUCCIONES", prod), (t.MACHINES, machine)]
        ],
    }
    if old:
        if all(old[k] == v for k, v in decision.items() if k not in {"revision", "previous_hash"}):
            return old
        decision.update(revision=old["revision"] + 1, previous_hash=digest(old))
    return decision


def live_handover(candidates, previous):
    """Bound automatic open episodes only with a confirmed successor in the same slot."""
    groups = defaultdict(list)
    for decision, slot, source, reference in candidates:
        groups[slot].append((decision, source, reference))

    for items in groups.values():
        items.sort(key=lambda item: item[0]["start"])
        for index, (outgoing, source, _) in enumerate(items[:-1]):
            if outgoing["end"] is not None or source["FECHA_OFF"] is not None:
                continue
            incoming, successor, reference = items[index + 1]
            cut = incoming["start"]
            if (
                not outgoing["start"] < cut
                or successor["FECHA_ON"] != cut
                or incoming["production"] == outgoing["production"]
                or (index > 0 and items[index - 1][0]["start"] == outgoing["start"])
                or (index + 2 < len(items) and items[index + 2][0]["start"] == cut)
                or (outgoing["kind"] == "ZF_ASSOCIATION" and successor["ZF_OK"] != 1)
            ):
                continue
            outgoing["end"] = cut
            outgoing["reason"] = HANDOVER
            outgoing["evidence"] = [*outgoing["evidence"], reference]

    for decision, _, _, _ in candidates:
        old = previous.get(decision["id"])
        if old and old["end"] is not None and old["reason"] == HANDOVER:
            if decision["end"] is None or old["end"] <= decision["end"]:
                # Replay or a disappearing successor cannot reopen an accredited episode.
                decision.update(end=old["end"], reason=old["reason"], evidence=old["evidence"])
        if old:
            payload = {k: v for k, v in decision.items() if k not in {"revision", "previous_hash"}}
            if all(old[k] == v for k, v in payload.items()):
                decision.update(revision=old["revision"], previous_hash=old["previous_hash"])
            else:
                decision.update(revision=old["revision"] + 1, previous_hash=digest(old))


def decisions(bundle, previous, codes, *, live=False):
    from .associations import decision_coverage, source_id

    result, candidates = [], []
    machines = bundle["sources"][t.MACHINES]
    branches = bundle["sources"][t.BRANCHES]
    for prod in bundle["sources"]["CATALOGO_PRODUCCIONES"]:
        composition = bundle["catalog_composition"].get(prod["id_produccion"], {})
        if not decision_coverage(bundle, prod["id_produccion"], "ZC_PRESENCE"):
            continue
        if not live and composition.get("coverage") != "VALID":
            continue
        for machine in machines:
            if (
                machine["id_maquina_fenix"] != prod["ID_MAQUINA"]
                or machine["codigo_linea_zc"] != codes[prod["origen_fenix"]]
            ):
                continue
            presence = zc_presence(bundle, previous, prod, composition, machine)
            machine_key = t.key(t.MACHINES, machine)
            if live and t.affected(t.MACHINES, bundle["cases"][t.MACHINES], machine):
                continue
            if presence is not None:
                presence = dict(presence)
                result.append(presence)
                candidates.append(
                    (
                        presence,
                        (prod["origen_fenix"], machine_key),
                        composition["root"],
                        {
                            "family": "CATALOGO_PRODUCCIONES",
                            "source_id": source_id("CATALOGO_PRODUCCIONES", prod),
                        },
                    )
                )
            if composition.get("coverage") != "VALID":
                continue
            for branch in branches:
                if (
                    branch["origen_fenix"] != prod["origen_fenix"]
                    or branch["id_maquina_fenix"] != prod["ID_MAQUINA"]
                ):
                    continue
                if live and t.affected(t.BRANCHES, bundle["cases"][t.BRANCHES], branch):
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
                    d = dict(d)
                    result.append(d)
                    candidates.append(
                        (
                            d,
                            (prod["origen_fenix"], machine_key, t.key(t.BRANCHES, branch)),
                            cold,
                            {
                                "family": "CATALOGO_PRODUCCIONES",
                                "source_id": source_id("CATALOGO_PRODUCCIONES", prod),
                            },
                        )
                    )
    if live:
        live_handover(candidates, previous)
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
