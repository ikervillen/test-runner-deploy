"""Resolve each result's original dimensions, amounts and configuration before display."""

from .presentation import score_payload


def project(query, score, production, tables, *, config=None):
    source = query.fork(score.get("basis_publication_id"))
    if config is None:
        config = source.config(score["config_id"])["score"]
    labels = {
        (r["espacio_codigo"], r["codigo"]): r["descripcion"] for r in source.rows("defect_catalog")
    }
    model = production["codigo_modelo_botella"]
    mapping = next(
        (r for r in source.rows("model_segment_catalog") if r["codigo_modelo_botella"] == model), {}
    )
    profiles = [
        r
        for r in source.rows("dar_profile_members")
        if r["profile_id"] == mapping.get("profile_id")
    ]
    amounts = tables
    if source is not query:
        amounts = {
            **tables,
            **{
                name: source.rows(name, [production["id_produccion"]])
                for name in ("ar_window_bases", "ar_window_exposures")
            },
        }
    return score_payload(score, amounts, config, labels, profiles)
