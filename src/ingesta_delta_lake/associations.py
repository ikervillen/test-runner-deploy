"""Asociaciones ZF revisadas, evidencias candidatas y decisiones por observación física."""

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import pyarrow as pa

from . import cont_zc, lista_ar, lista_defectos, lista_rech, mp9, physical_assignments, topology
from .common import IntegrityError, digest, parse_int64

CONTRACT = "asignacion_produccion_ramal_v2"
RULE = "temporal_assignment_v2"
FIELDS = (
    "id_asignacion",
    "planta",
    "origen_fenix",
    "linea",
    "ramal",
    "zona",
    "id_produccion",
    "vigente_desde",
    "vigente_hasta",
    "evidencia_origen",
    "version_regla",
    "revision_decision",
)
SCHEMA = pa.schema(
    [
        pa.field(
            f,
            pa.int64()
            if f in {"ramal", "revision_decision"}
            else pa.timestamp("us")
            if f in {"vigente_desde", "vigente_hasta"}
            else pa.string(),
            f == "vigente_hasta",
        )
        for f in FIELDS
    ]
)
SOURCE_KEYS = {
    **{f: lambda r, family=f: topology.key(family, r) for f in topology.FAMILIES},
    "LISTA_AR": lista_ar.key,
    "LISTA_DEFECTOS": lista_defectos.key,
    "LISTA_RECH": lista_rech.key,
    "CONT_ZC": cont_zc.key,
    "MP9_CAMBIO_MODELO": mp9.key,
    "CATALOGO_PRODUCCIONES": lambda r: r["id_produccion"],
    "SIP_MODELO": lambda r: r["id_modelo_fenix"],
    "SIP_DEFECTO": lambda r: r["id_defecto_fenix"],
    "SIP_DEFECTO_STATUS": lambda r: r["id_status_fenix"],
    "SIP_DEFECTO_AR": lambda r: r["id_tipo_ar_fenix"],
}


def row_hash(row):
    return digest([row[f] for f in FIELDS])


def content_hash(rows):
    return digest([(k, row_hash(v)) for k, v in sorted(rows.items())])


def stamp(text):
    if not isinstance(text, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", text
    ):
        raise ValueError("Se requiere fecha local ISO YYYY-MM-DDTHH:MM:SS")
    datetime.fromisoformat(text)
    return text


def overlaps(a, b, c, d):
    return (d is None or a < d) and (b is None or c < b)


def contains(a, b, c, d):
    return a <= c and (b is None or (d is not None and d <= b))


def source_id(family, row):
    return digest([family, SOURCE_KEYS[family](row)])


def review_index(bundle):
    """Hash each pending key once; the index lives only for this immutable bundle."""
    result = {}
    for family, cases in bundle["cases"].items():
        keys, origins = set(), set()
        for case in cases:
            payload = case["payload"]
            if "key" in payload:
                keys.add(digest(payload["key"]))
            else:
                refs = payload.get("evidence", [])
                refs = refs if isinstance(refs, list) else [refs]
                origins.update(ref.get("origin") for ref in refs)
        result[family] = keys, origins
    return result


def blocked(bundle, family, row, index=None):
    identity = SOURCE_KEYS[family](row)
    if index is not None:
        keys, origins = index.get(family, ((), ()))
        return row.get("origen_fenix") in origins or (bool(keys) and digest(identity) in keys)
    for case in bundle["cases"].get(family, []):
        p = case["payload"]
        if "key" in p:
            if digest(p["key"]) == digest(identity):
                return True
        else:
            refs = p.get("evidence", [])
            refs = refs if isinstance(refs, list) else [refs]
            if any(r.get("origin") == row.get("origen_fenix") for r in refs):
                return True
    return False


def ar_blocked(bundle, row, index=None):
    return blocked(bundle, "LISTA_AR", row, index) or any(
        bundle["cases"].get(f) for f in ("SIP_DEFECTO", "SIP_DEFECTO_STATUS", "SIP_DEFECTO_AR")
    )


def decision_identity(raw):
    identity = raw.get("id")
    return identity if isinstance(identity, str) else None


def production_context(bundle):
    models = {
        r["id_modelo_fenix"]: r["codigo_modelo_botella"] for r in bundle["sources"]["SIP_MODELO"]
    }
    prods = {r["id_produccion"]: r for r in bundle["sources"]["CATALOGO_PRODUCCIONES"]}
    return models, prods


def zone_for_ar(bundle, row):
    types = {r["codigo_tipo_ar"]: r["descripcion"] for r in bundle["sources"]["SIP_DEFECTO_AR"]}
    description = types.get(row["TIPO"], "")
    return "ZF" if description.endswith(" ZF") else "ZC" if description.endswith(" ZC") else None


def ar_productions(bundle, row):
    models, prods = production_context(bundle)
    zone = zone_for_ar(bundle, row)
    result = []
    for identity, p in prods.items():
        if (
            p["origen_fenix"] != row["origen_fenix"]
            or models.get(p["id_modelo_fenix"]) != row["MODELO"]
        ):
            continue
        obs = bundle["catalog_composition"].get(identity, {})
        if obs.get("coverage") != "VALID":
            continue
        zones = (
            obs.get("zf", [])
            if zone == "ZF"
            else [obs["root"]]
            if zone == "ZC" and "root" in obs
            else []
        )
        if any(
            (
                z["FECHA_ON"] <= row["FECHA_ON"]
                and (z["FECHA_OFF"] is None or row["FECHA_ON"] < z["FECHA_OFF"])
            )
            if row["FECHA_OFF"] == row["FECHA_ON"]
            else contains(z["FECHA_ON"], z["FECHA_OFF"], row["FECHA_ON"], row["FECHA_OFF"])
            for z in zones
        ):
            result.append(identity)
    return sorted(result)


def normalize_decision(raw, bundle, plant, lines):
    allowed = {
        "id",
        "kind",
        "origin",
        "branch",
        "machine",
        "production",
        "start",
        "end",
        "revision",
        "previous_hash",
        "actor",
        "reason",
        "evidence",
    }
    if set(raw) - allowed:
        raise ValueError("Campos de decisión desconocidos")
    for f in ("id", "kind", "origin", "actor", "reason"):
        if not isinstance(raw.get(f), str) or not raw[f].strip():
            raise ValueError(f"Falta {f}")
    d = {**raw, "end": raw.get("end"), "previous_hash": raw.get("previous_hash")}
    if not re.fullmatch(r"[A-Za-z0-9_-]+", d["id"]):
        raise ValueError("ID de episodio inválido")
    if d["origin"] not in lines:
        raise ValueError("Origen sin correspondencia autorizada")
    if d["kind"] not in {"ZF_ASSOCIATION", "ZF_PREPARATION", "ZC_PRESENCE", "ZC_PREPARATION"}:
        raise ValueError("Tipo de decisión desconocido")
    d["start"] = stamp(d["start"])
    if d["end"] is not None and stamp(d["end"]) <= d["start"]:
        raise ValueError("Intervalo vacío o invertido")
    if (
        not isinstance(d.get("revision"), int)
        or isinstance(d["revision"], bool)
        or not 1 <= d["revision"] < 2**63
    ):
        raise ValueError("Revisión inválida")
    number = "branch" if d["kind"].startswith("ZF") else "machine"
    if not isinstance(d.get(number), int) or isinstance(d[number], bool):
        raise ValueError("Código físico debe ser entero")
    d[number] = parse_int64(str(d[number]))
    other = "machine" if number == "branch" else "branch"
    if other in d:
        raise ValueError("No mezclar ramal ZF y máquina ZC")
    if d["kind"].endswith("PREPARATION"):
        if d["end"] is None or "production" in d:
            raise ValueError("Preparación requiere final y no producción")
    else:
        _, prods = production_context(bundle)
        p = prods.get(d.get("production"))
        if p is None or (p["planta"], p["origen_fenix"], p["linea"]) != (
            plant,
            d["origin"],
            lines[d["origin"]],
        ):
            raise ValueError("Producción incompatible o no publicada")
        if bundle["catalog_composition"].get(p["id_produccion"], {}).get("coverage") != "VALID":
            raise ValueError("Producción en revisión")
    evidence = d.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("Decisión sin evidencia")
    for e in evidence:
        if set(e) == {"external", "note"} and all(
            isinstance(v, str) and v.strip() for v in e.values()
        ):
            continue
        if set(e) != {"family", "source_id"}:
            raise ValueError("Referencia de evidencia inválida")
        family = e["family"]
        if not isinstance(family, str) or family not in bundle["sources"]:
            raise ValueError("Evidencia no encontrada en snapshot")
        matched = next(
            (r for r in bundle["sources"][family] if source_id(family, r) == e["source_id"]), None
        )
        if matched is None:
            raise ValueError("Evidencia no encontrada en snapshot")
        reviewed = (
            topology.affected(family, bundle["cases"].get(family, []), matched)
            if family in topology.FAMILIES
            else bool(bundle["cases"].get(family))
        )
        if reviewed or (family == "LISTA_AR" and ar_blocked(bundle, matched)):
            raise ValueError("Evidencia de una fuente en revisión")
    return d


def decision_scope(d):
    return d["origin"], d["kind"][:2], d.get("branch", d.get("machine"))


def review_scope(issue):
    if issue["reason"] == "OVERLAP_REVIEW":
        return tuple(issue["scope"])
    raw = issue["candidate"]
    if (
        isinstance(raw.get("origin"), str)
        and raw.get("kind") in ("ZF_ASSOCIATION", "ZF_PREPARATION", "ZC_PRESENCE", "ZC_PREPARATION")
        and isinstance(raw.get("branch", raw.get("machine")), int)
    ):
        return decision_scope(raw)
    return None


def validate_registry(registry):
    grouped = defaultdict(list)
    for d in registry.values():
        grouped[decision_scope(d)].append(d)
    bad = {}
    for scope, items in grouped.items():
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                if not overlaps(a["start"], a["end"], b["start"], b["end"]):
                    continue
                if (
                    scope[1] == "ZC"
                    and a["kind"] == b["kind"] == "ZC_PRESENCE"
                    and a["production"] != b["production"]
                ):
                    continue
                bad[scope] = "Solapamiento de episodios o preparación incompatible"
    return bad


def propose_registry(raw_decisions, previous, bundle, plant, lines):
    registry = dict(previous)
    issues = []
    bad_scopes = set()
    repeated = Counter(decision_identity(raw) for raw in raw_decisions)
    for raw in raw_decisions:
        try:
            d = normalize_decision(raw, bundle, plant, lines)
            if repeated[d["id"]] > 1:
                raise ValueError("ID de decisión repetido")
            old = previous.get(d["id"])
            if old and d == old:
                continue
            if old and (
                d["revision"] != old["revision"] + 1
                or d["previous_hash"] != digest(old)
                or decision_scope(d) != decision_scope(old)
                or d["kind"] != old["kind"]
            ):
                raise ValueError("Corrección sin revisión/huella previa o identidad incompatible")
            if not old and (d["revision"] != 1 or d["previous_hash"] is not None):
                raise ValueError("Nuevo episodio requiere revisión 1")
            registry[d["id"]] = d
        except (ValueError, KeyError, TypeError) as exc:
            issues.append(
                {
                    "reason": "DECISION_REVIEW",
                    "candidate": raw,
                    "accepted": previous.get(decision_identity(raw)),
                    "detail": str(exc),
                }
            )
            if (
                isinstance(raw.get("origin"), str)
                and isinstance(raw.get("kind"), str)
                and raw.get("kind")
                in {
                    "ZF_ASSOCIATION",
                    "ZF_PREPARATION",
                    "ZC_PRESENCE",
                    "ZC_PREPARATION",
                }
            ):
                physical = raw.get("branch", raw.get("machine"))
                if isinstance(physical, int) and not isinstance(physical, bool):
                    bad_scopes.add(decision_scope(raw))
    conflicts = validate_registry(registry)
    for scope, reason in conflicts.items():
        bad_scopes.add(scope)
        issues.append(
            {
                "reason": "OVERLAP_REVIEW",
                "scope": scope,
                "detail": reason,
                "candidates": [d for d in registry.values() if decision_scope(d) == scope],
                "accepted": {i: d for i, d in previous.items() if decision_scope(d) == scope},
            }
        )
    for identity, d in list(registry.items()):
        if decision_scope(d) in bad_scopes:
            if identity in previous:
                registry[identity] = previous[identity]
            else:
                del registry[identity]
    if validate_registry(registry):
        raise IntegrityError("El registro aceptado ya contiene solapamientos")
    return registry, issues


def project(registry, plant, lines):
    return {
        f"fenix-association-v1:{plant}:{d['origin']}:{d['id']}": {
            "id_asignacion": f"fenix-association-v1:{plant}:{d['origin']}:{d['id']}",
            "planta": plant,
            "origen_fenix": d["origin"],
            "linea": lines[d["origin"]],
            "ramal": d["branch"],
            "zona": "ZF",
            "id_produccion": d["production"],
            "vigente_desde": d["start"],
            "vigente_hasta": d["end"],
            "evidencia_origen": "decision:" + digest(d),
            "version_regla": RULE,
            "revision_decision": d["revision"],
        }
        for d in registry.values()
        if d["kind"] == "ZF_ASSOCIATION"
    }


def interval_decision(items, start, end):
    """Una muestra completa; permitir un único modelo más preparación confirmada."""
    pieces = []
    for d in items:
        if overlaps(d["start"], d["end"], start, end):
            pieces.append((max(start, d["start"]), min(end, d["end"] or end), d))
    pieces.sort(key=lambda p: p[0])
    cursor = start
    for a, b, _ in pieces:
        if a != cursor:
            return "PENDING_COVERAGE", [], []
        cursor = b
    if cursor != end:
        return "PENDING_COVERAGE", [], []
    prods = sorted({d["production"] for _, _, d in pieces if "production" in d})
    ids = sorted({d["id"] for _, _, d in pieces})
    if len(prods) > 1:
        return "MIXED_MODELS", [], ids
    if not prods:
        return "PREPARATION", [], ids
    if all("production" in d for _, _, d in pieces):
        return "ASSIGNED", prods, ids
    production_positions = [i for i, (_, _, d) in enumerate(pieces) if "production" in d]
    if production_positions == list(range(len(production_positions))):
        return "OLD_PLUS_PREPARATION", prods, ids
    if production_positions == list(range(len(pieces) - len(production_positions), len(pieces))):
        return "NEW_PLUS_PREPARATION", prods, ids
    return "PENDING_TRANSITION", [], ids


class ObservationContext(defaultdict):
    def __init__(self, bundle):
        super().__init__(list)
        self.reviews = review_index(bundle)


def prepare_context(bundle):
    ar_index = ObservationContext(bundle)
    for row in bundle["sources"]["LISTA_AR"]:
        if (
            not ar_blocked(bundle, row, ar_index.reviews)
            and zone_for_ar(bundle, row) == "ZF"
            and row["MOLDE"] > 0
            and row["CAVIDAD"] not in (None, "99Z")
        ):
            ar_index[(row["origen_fenix"], row["MOLDE"], row["CAVIDAD"])].append(
                (row, ar_productions(bundle, row))
            )
    return ar_index


def candidates(bundle, ar_index):
    groups = {}
    reviews = getattr(ar_index, "reviews", None)
    for family in ("LISTA_RECH", "LISTA_DEFECTOS"):
        for row in bundle["sources"][family]:
            if (
                row["MOLDE"] <= 0
                or row["CAVIDAD"] in (None, "99Z")
                or blocked(bundle, family, row, reviews)
            ):
                continue
            end = row["FECHA"]
            start = (datetime.fromisoformat(end) - timedelta(minutes=10)).isoformat(
                timespec="seconds"
            )
            for a, prods in ar_index.get((row["origen_fenix"], row["MOLDE"], row["CAVIDAD"]), []):
                if not overlaps(start, end, a["FECHA_ON"], a["FECHA_OFF"]):
                    continue
                k = (row["origen_fenix"], row["LINEA"], a["MODELO"], tuple(prods))
                if k not in groups:
                    groups[k] = {
                        "origen_fenix": k[0],
                        "ramal": k[1],
                        "model_code": k[2],
                        "production_candidates": prods,
                        "status": "NEEDS_REVIEW"
                        if len(prods) == 1
                        else "AMBIGUOUS_PRODUCTION"
                        if prods
                        else "PRODUCTION_UNAVAILABLE",
                        "first_observed_sample_end": end,
                        "last_observed_sample_end": end,
                        "matched_evidence_pairs": 0,
                        "evidence": [],
                    }
                g = groups[k]
                g["matched_evidence_pairs"] += 1
                g["first_observed_sample_end"] = min(g["first_observed_sample_end"], end)
                g["last_observed_sample_end"] = max(g["last_observed_sample_end"], end)
                e = {
                    "ar": source_id("LISTA_AR", a),
                    "family": family,
                    "observation": source_id(family, row),
                }
                if len(g["evidence"]) < 8 and e not in g["evidence"]:
                    g["evidence"].append(e)
    return sorted(groups.values(), key=lambda g: (g["origen_fenix"], g["ramal"], g["model_code"]))


def assign(bundle, registry, ar_index, review_scopes=()):
    reviews = getattr(ar_index, "reviews", None)
    zf = defaultdict(list)
    zc = defaultdict(list)
    for d in registry.values():
        (zf if d["kind"].startswith("ZF") else zc)[decision_scope(d)[::2]].append(d)
    models, prods = production_context(bundle)
    ledger = []
    for family in ("LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC"):
        for row in bundle["sources"][family]:
            targets = []
            episode_ids = []
            reason = ""
            status = "PENDING_ASSOCIATION"
            if blocked(bundle, family, row, reviews) or (
                family == "LISTA_AR" and ar_blocked(bundle, row, reviews)
            ):
                status = "SOURCE_REVIEW"
            elif family == "LISTA_AR":
                targets = ar_productions(bundle, row)
                status = (
                    "PENDING_ZONE"
                    if zone_for_ar(bundle, row) is None
                    else "PENDING_OPEN_AR"
                    if row["FECHA_OFF"] is None
                    else "ASSIGNED"
                    if len(targets) == 1
                    else "AMBIGUOUS_PRODUCTION"
                    if len(targets) > 1
                    else "PENDING_PRODUCTION"
                )
                if status != "ASSIGNED":
                    targets = []
            else:
                end = row["FECHA"]
                start = (datetime.fromisoformat(end) - timedelta(minutes=10)).isoformat(
                    timespec="seconds"
                )
                items = (zc if family == "CONT_ZC" else zf).get(
                    (row["origen_fenix"], row["MAQ_IS"] if family == "CONT_ZC" else row["LINEA"]),
                    [],
                )
                usable = [
                    d
                    for d in items
                    if "production" not in d
                    or bundle["catalog_composition"].get(d["production"], {}).get("coverage")
                    == "VALID"
                ]
                if family == "CONT_ZC":
                    candidates_zc = sorted(
                        {
                            d["production"]
                            for d in usable
                            if "production" in d and overlaps(d["start"], d["end"], start, end)
                        }
                    )
                    if candidates_zc:
                        decisions = [
                            interval_decision(
                                [
                                    d
                                    for d in usable
                                    if "production" not in d or d["production"] == p
                                ],
                                start,
                                end,
                            )
                            for p in candidates_zc
                        ]
                        if all(
                            s in {"ASSIGNED", "OLD_PLUS_PREPARATION", "NEW_PLUS_PREPARATION"}
                            for s, _, _ in decisions
                        ):
                            status = "SHARED_MACHINE"
                            targets = candidates_zc
                            episode_ids = sorted({i for _, _, ids in decisions for i in ids})
                        else:
                            status = "PENDING_ZC_TRANSITION"
                    else:
                        status, targets, episode_ids = interval_decision(usable, start, end)
                else:
                    status, targets, episode_ids = interval_decision(usable, start, end)
                    if bundle.get("topology_enabled"):
                        branch = physical_assignments.branch_for_period(
                            bundle, row["origen_fenix"], row["LINEA"], start, end
                        )
                        if branch is None:
                            status, targets = "PENDING_TOPOLOGY", []
                        else:
                            if status in {"MIXED_MODELS", "PENDING_COVERAGE"}:
                                transition = physical_assignments.outgoing(
                                    usable, start, end, prods
                                )
                                if transition is not None:
                                    status, targets, episode_ids = transition
                            if (
                                targets
                                and prods[targets[0]]["ID_MAQUINA"] != branch["id_maquina_fenix"]
                            ):
                                status, targets = "EVIDENCE_CONFLICT", []
                    hints = {
                        a["MODELO"]
                        for a, _ in ar_index.get(
                            (row["origen_fenix"], row["MOLDE"], row["CAVIDAD"]), []
                        )
                        if overlaps(start, end, a["FECHA_ON"], a["FECHA_OFF"])
                    }
                    if targets and any(
                        m != models[prods[targets[0]]["id_modelo_fenix"]] for m in hints
                    ):
                        status = "EVIDENCE_CONFLICT"
                        targets = []
                if status == "PREPARATION" and (
                    row["RUEDA"] > 0
                    if family == "CONT_ZC"
                    else row["INSPECC"] > 0 or row["RECH"] > 0
                ):
                    status = "FLOW_DURING_PREPARATION"
                scope = (
                    row["origen_fenix"],
                    "ZC" if family == "CONT_ZC" else "ZF",
                    row["MAQ_IS"] if family == "CONT_ZC" else row["LINEA"],
                )
                if scope in review_scopes:
                    status, targets = "DECISION_REVIEW", []
                reason = f"sample:[{start},{end})"
            ledger.append(
                {
                    "family": family,
                    "source_id": source_id(family, row),
                    "key": SOURCE_KEYS[family](row),
                    "origin": row["origen_fenix"],
                    "status": status,
                    "productions": targets,
                    "episode_ids": episode_ids,
                    "reason": reason,
                    "rule": RULE,
                }
            )
    return ledger


def diagnostics(ledger):
    result = {}
    for family in ("LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC"):
        items = [r for r in ledger if r["family"] == family]
        counts = Counter(r["status"] for r in items)
        assigned = sum(bool(r["productions"]) for r in items)
        result[family] = {
            "source_rows": len(items),
            "assigned_rows": assigned,
            "unassigned_rows": len(items) - assigned,
            "by_status": dict(counts),
        }
    return result
