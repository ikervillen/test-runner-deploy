from __future__ import annotations

from app.api.errors import ApplicationError
from app.domain.models import Production, TrendFilters, TrendRequest
from app.domain.protocols import CatalogRepository
from app.schemas.api import TrendQueryRequest


def resolve_trend_selection(
    payload: TrendQueryRequest,
    catalog_repository: CatalogRepository,
    max_productions: int,
) -> tuple[TrendRequest, tuple[Production, ...]]:
    production_ids = tuple(dict.fromkeys(payload.production_ids))
    effective_max = max(max_productions, len(catalog_repository.list_productions()))
    if len(production_ids) > effective_max:
        raise ApplicationError(
            "TOO_MANY_PRODUCTIONS",
            f"Se permiten como máximo {max_productions} producciones.",
            details={"limit": max_productions},
        )

    productions = catalog_repository.get_productions(production_ids)
    valid_branches = {branch.branch for production in productions for branch in production.branches}
    invalid_branches = sorted(set(payload.branches) - valid_branches)
    if invalid_branches:
        raise ApplicationError(
            "INVALID_BRANCH_SELECTION",
            "Uno o varios ramales no pertenecen a las producciones seleccionadas.",
            details={"branches": invalid_branches},
        )

    request = TrendRequest(
        production_ids=production_ids,
        branches=tuple(sorted(set(payload.branches))),
        metric=payload.metric,
        window=payload.window,
        filters=TrendFilters(
            defect_codes=tuple(sorted(set(payload.filters.defect_codes))),
            cavities=tuple(sorted(set(payload.filters.cavities))),
            colors=tuple(sorted(set(payload.filters.colors))),
            sections=tuple(sorted(set(payload.filters.sections))),
            machine_codes=tuple(sorted(set(payload.filters.machine_codes))),
        ),
    )
    return request, productions
