"""Accredit live ZF handovers from immutable MP9 relations and Silver topology."""

import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta

from ingesta_delta_lake import mp9, physical_assignments
from ingesta_delta_lake.associations import (
    decision_scope,
    interval_decision,
    overlaps,
    review_scope,
    validate_registry,
)
from ingesta_delta_lake.common import IntegrityError, digest
from ingesta_delta_lake.pipeline_bronze import BronzeFeed
from ingesta_delta_lake.pipeline_rows import portable

RELATION = "MP9_MOD_LINEA_ZF"
ACTOR = "rule:bronze_mp9_zf_relation_v1"


def relation_rows(data, encoding):
    for row in csv.reader(io.StringIO(data.decode(encoding)), delimiter=";", strict=True):
        if not row:
            continue
        if len(row) == 10 and row[-1] == "":
            row.pop()
        if len(row) != 9:
            raise ValueError("Unexpected MP9_MOD_LINEA_ZF CSV shape")
        identity, model, start, end, branch, machine, status, ok, parent = row
        start = mp9.timestamp(start)
        end = mp9.timestamp(end) if end.strip() else None
        if status not in {"1", "2"} or ok not in {"0", "1"} or (end and end < start):
            raise ValueError("Invalid MP9_MOD_LINEA_ZF status or interval")
        yield (
            int(identity),
            int(model),
            start,
            end,
            int(branch),
            int(machine),
            int(status),
            int(parent) if parent.strip() else None,
        )


def recent_relations(storage, policy, encoding, cutoff, origins):
    try:
        feed = BronzeFeed(storage)
    except FileNotFoundError:
        return []
    after = max(0, feed.sequence - policy.read_batch_rows)
    lower = cutoff - timedelta(seconds=policy.live_retry_period_seconds + policy.window_seconds)
    by_origin = defaultdict(list)
    for capture in feed.captures(after=after, limit=policy.read_batch_rows):
        snap = capture.snapshot
        if capture.family == RELATION and snap.origin in origins and snap.producer_time:
            when = datetime.fromisoformat(snap.producer_time)
            if lower <= when <= cutoff + timedelta(seconds=policy.window_seconds):
                by_origin[snap.origin].append(capture)
    evidence = []
    for origin, captures in by_origin.items():
        ordered = sorted(captures, key=lambda c: (c.snapshot.producer_time, c.sequence))
        # Only the latest captured version of each path is eligible.
        versions = {c.snapshot.source_path: c for c in ordered}
        try:
            rows = [
                (origin, *row)
                for capture in list(versions.values())[-2:]
                for row in relation_rows(
                    feed.read(capture, max_file_bytes=policy.max_file_bytes), encoding
                )
            ]
        except (IntegrityError, ValueError, OSError, csv.Error):
            continue
        evidence.extend(rows)
    return evidence


def series_evidence(composition, topology, evidence):
    episodes = defaultdict(set)
    for identity, item in composition.items():
        if item.get("coverage") != "VALID":
            continue
        for zf in item["zf"]:
            key = zf["origen_fenix"], zf["ID_MAQUINA"], zf["ID_MODELO"], zf["FECHA_ON"]
            episodes[key].add((identity, zf["FECHA_OFF"]))
    branches = defaultdict(list)
    for row in topology:
        branches[row["origen_fenix"], row["id_linea_zf_fenix"]].append(row)
    candidates = defaultdict(set)
    for origin, identity, model, start, end, branch, machine, status, parent in evidence:
        candidates[origin, identity].add((model, start, end, branch, machine, status, parent))
    accepted, blocked = defaultdict(dict), defaultdict(list)
    for (origin, identity), items in candidates.items():
        zf = [v for v in items if v[5] == 2]
        if not zf:
            continue
        fixed = {
            (m, a, b, machine, status, parent) for m, a, _, b, machine, status, parent in items
        }
        ends = {end for _, _, end, *_ in items if end is not None}
        if len(fixed) == 1 and len(ends) <= 1:
            model, start, branch, machine, _, parent = next(iter(fixed))
            end = next(iter(ends), None)
            roots = candidates.get((origin, parent), ())
            linked = episodes[origin, machine, model, start]
            if len(linked) == 1 and any(
                v[0] == model and v[3:6] == (branch, machine, 1) for v in roots
            ):
                production, mp9_end = next(iter(linked))
                if not (end and mp9_end and end != mp9_end):
                    end = end or mp9_end
                    mapped = []
                    for row in branches[origin, branch]:
                        if row["id_maquina_fenix"] != machine or not overlaps(
                            start, end, row["vigente_desde"], row["vigente_hasta"]
                        ):
                            continue
                        left = max(start, row["vigente_desde"])
                        right = min((v for v in (end, row["vigente_hasta"]) if v), default=None)
                        mapped.append((row["codigo_ramal"], left, right))
                    if mapped:
                        for ramal, left, right in mapped:
                            key = "zf_relation_" + digest([origin, identity, ramal, left])
                            accepted[origin, "ZF", ramal][key] = {
                                "id": key,
                                "origin": origin,
                                "kind": "ZF_ASSOCIATION",
                                "production": production,
                                "branch": ramal,
                                "start": left,
                                "end": right,
                                "actor": ACTOR,
                                "revision": 1,
                                "evidence": [{"family": RELATION, "source_id": str(identity)}],
                            }
                        continue
        for _, start, end, branch, *_ in zf:
            ramales = {r["codigo_ramal"] for r in branches[origin, branch]} or {None}
            for ramal in ramales:
                blocked[origin, "ZF", ramal].append((start, end))
    return accepted, blocked


def apply_series_handover(document, tables, evidence, window, slot_seconds):
    """Replace automatic decisions only where the entire affected sample is accredited."""
    cases = document["association_cases"]
    decisions = document["registry"]
    composition = document["bundle"]["catalog_composition"]
    accepted, blocked = series_evidence(
        composition, portable(tables["SIP_LINEA_ZF"].to_pylist()), evidence
    )
    prods = {
        identity: value["root"] for identity, value in composition.items() if value.get("root")
    }
    samples = defaultdict(set)
    left, right = window
    for family in ("LISTA_DEFECTOS", "LISTA_RECH"):
        for batch in tables[family].select(["origen_fenix", "LINEA", "FECHA"]).to_batches():
            for row in batch.to_pylist():
                end = row["FECHA"]
                if left < end <= right:
                    start = end - timedelta(seconds=slot_seconds)
                    scope = row["origen_fenix"], "ZF", row["LINEA"]
                    samples[scope].add((start.isoformat(), end.isoformat()))
    by_scope = defaultdict(list)
    for case in cases:
        by_scope[review_scope(case["payload"])].append(case["payload"])
    current = defaultdict(dict)
    for key, row in decisions.items():
        current[decision_scope(row)][key] = row
    automated = {physical_assignments.ACTOR, ACTOR}
    resolved = set()
    for scope, candidates in accepted.items():
        if not samples[scope] or validate_registry(candidates):
            continue
        if any(d.get("actor") not in automated for d in current[scope].values()):
            continue
        if any(
            case["reason"] != "OVERLAP_REVIEW"
            or not case.get("candidates")
            or any(d.get("actor") not in automated for d in case["candidates"])
            for case in by_scope[scope]
        ):
            continue
        rejected = blocked[scope] + blocked[(scope[0], "ZF", None)]
        items = list(candidates.values())
        for start, end in samples[scope]:
            if any(overlaps(a, b, start, end) for a, b in rejected):
                break
            status, _, _ = interval_decision(items, start, end)
            if status != "ASSIGNED" and (
                status != "MIXED_MODELS"
                or not physical_assignments.outgoing(items, start, end, prods)
            ):
                break
        else:
            for key, old in current[scope].items():
                if any(overlaps(old["start"], old["end"], d["start"], d["end"]) for d in items):
                    del decisions[key]
            decisions.update(candidates)
            resolved.add(scope)
    document["association_cases"] = [
        case for case in cases if review_scope(case["payload"]) not in resolved
    ]
    document["series_handover_scopes"] = sorted(resolved)
