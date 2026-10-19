"""Catálogo derivado v2: raíz ZC y máquina física, sin unir por modelo o fecha."""

from collections import defaultdict

import pyarrow as pa

from .common import digest

CONTRACT = "catalogo_producciones_v2"
FIELDS = (
    "id_produccion",
    "planta",
    "origen_fenix",
    "linea",
    "id_zc_raiz",
    "id_modelo_fenix",
    "fecha_inicio_zc",
    "fecha_fin_zf",
    "ID_MAQUINA",
    "ID_ST1_REL",
)
SCHEMA = pa.schema(
    [
        pa.field("id_produccion", pa.string(), False),
        pa.field("planta", pa.string(), False),
        pa.field("origen_fenix", pa.string(), False),
        pa.field("linea", pa.string(), False),
        pa.field("id_zc_raiz", pa.int64(), False),
        pa.field("id_modelo_fenix", pa.int64(), False),
        pa.field("fecha_inicio_zc", pa.timestamp("us"), False),
        pa.field("fecha_fin_zf", pa.timestamp("us"), True),
        pa.field("ID_MAQUINA", pa.int64(), False),
        pa.field("ID_ST1_REL", pa.int64(), True),
    ]
)


def production_id(plant: str, origin: str, root: int) -> str:
    # Planta/origen se validan sin ':'; el entero usa su representación decimal canónica.
    return f"fenix-production-v1:{plant}:{origin}:{root}"


def row_hash(row: dict) -> str:
    return digest([row[f] for f in FIELDS])


def content_hash(rows: dict[str, dict]) -> str:
    return digest([(k, row_hash(v)) for k, v in sorted(rows.items())])


def project(plant, lines, records, cases, previous, previous_observations):
    roots = {k: r for k, r in records.items() if r["STATUS"] == 1}
    cold = defaultdict(list)
    for row in records.values():
        if row["STATUS"] == 2:
            target = (row["origen_fenix"], row["ID_ST1_REL"])
            root = roots.get(target)
            if (
                root is None
                or row["ID_MODELO"] != root["ID_MODELO"]
                or row["ID_MAQUINA"] != root["ID_MAQUINA"]
            ):
                raise ValueError("Una relación MP9 publicada no es válida para el catálogo")
            cold[target].append(row)
    affected = defaultdict(list)
    pending_roots = set()
    for case in cases:
        payload = case["payload"]
        origin, identifier = payload.get("origen_fenix"), payload.get("ID")
        variants = payload.get("candidates", [])
        if payload.get("candidate"):
            variants = [*variants, payload["candidate"]]
        accepted = records.get((origin, identifier))
        if accepted:
            variants = [*variants, accepted]
        targets = set()
        for row in variants:
            key = (row["origen_fenix"], row["ID"])
            if row["STATUS"] == 1:
                targets.add(key)
                if key not in roots:
                    pending_roots.add(key)
            elif row["ID_ST1_REL"] is not None:
                targets.add((row["origen_fenix"], row["ID_ST1_REL"]))
        if (origin, identifier) in roots:
            targets.add((origin, identifier))
        if not targets:
            # Una incidencia sin contexto suficiente no autoriza confirmar cierres del origen.
            source_origins = (
                {origin}
                if origin
                else {r.get("origin") for r in (payload.get("evidence") or [])}
                if isinstance(payload.get("evidence"), list)
                else {(payload.get("evidence") or {}).get("origin")}
            )
            targets = {k for k in roots if k[0] in source_origins}
        for target in targets:
            affected[target].append(case["issue_id"])

    rows, observations, issues = {}, {}, []
    for key, root in sorted(roots.items()):
        origin, identifier = key
        identity = production_id(plant, origin, identifier)
        children = sorted(cold[key], key=lambda r: r["ID"])
        component_ids = [identifier, *(r["ID"] for r in children)]
        reasons = []
        if affected[key]:
            reasons.append("SOURCE_REVIEW")
        if not children:
            reasons.append("MISSING_ZF")
        final = None
        if children and not reasons and all(r["FECHA_OFF"] is not None for r in children):
            final = max(r["FECHA_OFF"] for r in children)
            if final < root["FECHA_ON"]:
                final = None
                reasons.append("INVALID_LIMITS")
        candidate = dict(
            zip(
                FIELDS,
                (
                    identity,
                    plant,
                    origin,
                    lines[origin],
                    identifier,
                    root["ID_MODELO"],
                    root["FECHA_ON"],
                    final,
                    root["ID_MAQUINA"],
                    identifier if children else None,
                ),
                strict=True,
            )
        )
        old = previous.get(identity)
        prior = previous_observations.get(identity)
        if old:
            changed = [
                f
                for f in FIELDS
                if f not in ("fecha_fin_zf", "ID_ST1_REL") and candidate[f] != old[f]
            ]
            if (
                old["fecha_fin_zf"] is not None
                and prior
                and prior["component_ids"] != component_ids
            ):
                changed.append("composition")
            if (
                old["fecha_fin_zf"] is not None
                and final is not None
                and final != old["fecha_fin_zf"]
            ):
                changed.append("fecha_fin_zf")
            if changed:
                reasons.append("CATALOG_REVIEW")
                issues.append(
                    {
                        "reason": "CATALOG_CONFLICT",
                        "id_produccion": identity,
                        "changed_fields": changed,
                        "accepted": old,
                        "candidate": candidate,
                        "evidence": {"root": root, "zf": children},
                    }
                )
                candidate = old
            elif old["fecha_fin_zf"] is not None:
                candidate["fecha_fin_zf"] = old["fecha_fin_zf"]
                candidate["ID_ST1_REL"] = old["ID_ST1_REL"]
        candidate_components = list(component_ids)
        if "CATALOG_REVIEW" in reasons and prior:
            component_ids = prior["component_ids"]
        observations[identity] = {
            "component_ids": component_ids,
            "candidate_component_ids": candidate_components,
            "source_issues": affected[key],
            "coverage": "REVIEW"
            if any(r != "MISSING_ZF" for r in reasons)
            else "PENDING_ZF"
            if reasons
            else "VALID",
            "reasons": reasons,
            "root": root,
            "zf": children,
        }
        rows[identity] = candidate
    if set(previous) - set(rows):
        raise ValueError("El snapshot MP9 ya no contiene raíces del catálogo publicado")
    pending = [
        {
            "id_produccion": production_id(plant, origin, identifier),
            "origen_fenix": origin,
            "id_zc_raiz": identifier,
            "published": False,
        }
        for origin, identifier in sorted(pending_roots)
    ]
    return rows, observations, issues, pending
