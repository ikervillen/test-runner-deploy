"""Discriminate tandem AR with independently attributed positive inspection samples.

An AR grouping number is never unique on its own. Only contiguous segments with
the same physical/business context may share a witness; original rows stay intact.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from itertools import islice

from . import lista_ar

EVENT_FIELDS = tuple(f for f in lista_ar.KEY_FIELDS if f not in {"FECHA_ON", "STATUS"})


def resolve(bundle, ledger, candidates, *, frozen=()):
    from .associations import source_id

    ar_entries = {r["source_id"]: r for r in ledger if r["family"] == "LISTA_AR"}
    groups = defaultdict(list)
    contexts = set()
    for row in bundle["sources"]["LISTA_AR"]:
        identity = source_id("LISTA_AR", row)
        entry = ar_entries[identity]
        if (
            identity in frozen
            or entry["status"] not in {"ASSIGNED", "AMBIGUOUS_PRODUCTION"}
            or row["FECHA_OFF"] is None
        ):
            continue
        context = (row["origen_fenix"], row["MOLDE"], row["CAVIDAD"])
        if row["MOLDE"] <= 0 or row["CAVIDAD"] in (None, "99Z"):
            continue
        # A missing grouping ID cannot link two otherwise similar events.
        event = (row["origen_fenix"], *(row[f] for f in EVENT_FIELDS))
        if row["ID_Agrupación"] is None:
            event = (identity,)
        groups[event].append((row, entry))
        if entry["status"] == "AMBIGUOUS_PRODUCTION" or entry.get("temporal_exception"):
            contexts.add(context)
    witnesses = defaultdict(list)
    offset = 0
    for family in ("LISTA_DEFECTOS", "LISTA_RECH"):
        rows = bundle["sources"][family]
        for row, entry in zip(rows, islice(ledger, offset, offset + len(rows)), strict=True):
            context = (row["origen_fenix"], row["MOLDE"], row["CAVIDAD"])
            if context not in contexts or not (row["INSPECC"] > 0 or row["RECH"] > 0):
                continue
            end = row["FECHA"]
            start = (datetime.fromisoformat(end) - timedelta(minutes=10)).isoformat(
                timespec="seconds"
            )
            witnesses[context].append((start, end, entry))
        offset += len(rows)
    prods = {p["id_produccion"]: p for p in bundle["sources"].get("CATALOGO_PRODUCCIONES", [])}
    for items in groups.values():
        for row, entry in items:
            exception = entry.get("temporal_exception")
            if not exception or entry["status"] != "ASSIGNED":
                continue
            outgoing = entry["productions"][0]
            context = (row["origen_fenix"], row["MOLDE"], row["CAVIDAD"])
            conflicts = [
                evidence["source_id"]
                for start, end, evidence in witnesses[context]
                if start < row["FECHA_OFF"]
                and exception["production_end"] < end
                and evidence["status"] in {"ASSIGNED", "TRANSITION_TO_OUTGOING"}
                and any(
                    target != outgoing
                    and prods[target]["ID_MAQUINA"] == prods[outgoing]["ID_MAQUINA"]
                    for target in evidence["productions"]
                )
            ]
            if conflicts:
                entry.update(
                    status="EVIDENCE_CONFLICT",
                    productions=[],
                    reason="Positive MIA evidence contradicts outgoing AR continuity",
                    conflicting_evidence_source_ids=sorted(set(conflicts))[:8],
                )
    for items in groups.values():
        ordered = sorted(items, key=lambda pair: (pair[0]["FECHA_ON"], pair[0]["FECHA_OFF"]))
        clusters = []
        for item in ordered:
            if clusters and clusters[-1][-1][0]["FECHA_OFF"] == item[0]["FECHA_ON"]:
                clusters[-1].append(item)
            else:
                clusters.append([item])
        for cluster in clusters:
            ambiguous = [(r, e) for r, e in cluster if e["status"] == "AMBIGUOUS_PRODUCTION"]
            if not ambiguous:
                continue
            first, last = cluster[0][0], cluster[-1][0]
            context = (first["origen_fenix"], first["MOLDE"], first["CAVIDAD"])
            evidence = [
                e
                for start, end, e in witnesses[context]
                if (first["FECHA_ON"] <= start and end <= last["FECHA_OFF"])
                or (first["FECHA_ON"] == last["FECHA_OFF"] and start <= first["FECHA_ON"] < end)
            ]
            if any(
                e["status"] not in {"ASSIGNED", "TRANSITION_TO_OUTGOING"}
                or len(e["productions"]) != 1
                for e in evidence
            ):
                continue
            targets = {p for e in evidence for p in e["productions"]}
            # Never select the first candidate or ignore incompatible witnesses.
            if len(targets) != 1 or any(not targets <= set(candidates(row)) for row, _ in cluster):
                continue
            for _, entry in ambiguous:
                entry.update(
                    status="ASSIGNED",
                    productions=sorted(targets),
                    episode_ids=sorted({i for e in evidence for i in e["episode_ids"]}),
                    reason="Physical MIA mold/cavity evidence within contiguous AR segments",
                    evidence_source_ids=sorted({e["source_id"] for e in evidence})[:8],
                    evidence_ar_segment_ids=[e["source_id"] for _, e in cluster],
                    evidence_observations=len(evidence),
                )
