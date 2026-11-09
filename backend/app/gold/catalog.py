"""Catalog projection of a pinned Gold publication: lines, productions, branches and filters."""

from __future__ import annotations

from collections import defaultdict

from app.schemas.api import CatalogResponse

from .query import Query

FILTER_COLUMNS = (("cavities", "cavidad"), ("colors", "color"), ("sections", "seccion"))


def build_catalog(q: Query, *, max_productions_per_query: int) -> CatalogResponse:
    productions = q.rows("productions")
    assignments_by_production = defaultdict(list)
    for assignment in q.rows("production_assignments"):
        assignments_by_production[assignment["id_produccion"]].append(assignment)
    mappings = {r["codigo_modelo_botella"]: r for r in q.rows("model_segment_catalog")}
    inspections = q.rows("mia_inspections")
    by_branch = defaultdict(list)
    for row in inspections:
        by_branch[(row["id_produccion"], row["ramal"])].append(row)

    result = []
    for p in productions:
        start = p["fecha_inicio_zc"]
        if start is None:
            continue
        identity = p["id_produccion"]
        assignments = assignments_by_production.get(identity, [])
        observed_end = max(
            (
                r["periodo_fin"]
                for a in assignments
                for r in by_branch.get((identity, a["ramal"]), [])
            ),
            default=start,
        )
        mapping = mappings.get(p["codigo_modelo_botella"], {})
        branches = []
        for branch in sorted(
            {a["ramal"] for a in assignments if a["zona"] == "ZF" and a["ramal"] is not None}
        ):
            rows = by_branch.get((identity, branch), [])
            branches.append(
                dict(
                    id=f"{p['linea']}-{branch}",
                    rows=len(rows),
                    data_start=min((r["periodo_inicio"] for r in rows), default=start),
                    data_end=max((r["periodo_fin"] for r in rows), default=start),
                )
            )
        result.append(
            dict(
                id=identity,
                line=p["linea"],
                start_day=start.date(),
                end_day=(p["fecha_fin_zf"] or observed_end).date(),
                branches=branches,
                bottle_model_id=p["codigo_modelo_botella"],
                segment_id=mapping.get("segmento_calidad"),
                dar_profile_id=mapping.get("profile_id"),
            )
        )

    filters = dict(
        defect_codes=sorted(
            {
                int(r["codigo"])
                for r in q.rows("defect_catalog")
                if r["espacio_codigo"] == "fenix_mia"
            }
        ),
        machine_codes=sorted({r["codigo_maquina_inspeccion"] for r in inspections}),
    )
    for field, column in FILTER_COLUMNS:
        filters[field] = sorted({r[column] for r in inspections if r[column] is not None})
    return CatalogResponse(
        source="gold",
        publication_id=q.snapshot.publication_id,
        lines=sorted({p["linea"] for p in productions}),
        productions=result,
        metrics=[],
        windows=[],
        filters=filters,
        max_productions_per_query=max_productions_per_query,
    )
