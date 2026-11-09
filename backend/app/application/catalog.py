from __future__ import annotations

from app.domain.models import MetricId, WindowId
from app.domain.protocols import CatalogRepository, MetadataRepository
from app.schemas.api import (
    BranchCatalog,
    CatalogResponse,
    FilterOptions,
    OptionCatalog,
    ProductionCatalog,
)

METRIC_LABELS = {
    MetricId.REJECTED_COUNT: "Rechazos registrados",
    MetricId.INSPECTED_COUNT: "Botellas inspeccionadas",
    MetricId.REJECTION_RATE: "Porcentaje de rechazo",
}

WINDOW_LABELS = {
    WindowId.TEN_MINUTES: "10 minutos",
    WindowId.ONE_HOUR: "1 hora",
    WindowId.FOUR_HOURS: "4 horas",
    WindowId.EIGHT_HOURS: "8 horas / turno",
}


class GetCatalog:
    def __init__(
        self,
        catalog_repository: CatalogRepository,
        metadata_repository: MetadataRepository,
        max_productions_per_query: int,
    ) -> None:
        self._catalog_repository = catalog_repository
        self._metadata_repository = metadata_repository
        self._max_productions = max_productions_per_query

    def execute(self) -> CatalogResponse:
        productions = self._catalog_repository.list_productions()
        filters = self._metadata_repository.available_filters(productions)
        return CatalogResponse(
            lines=sorted({production.line for production in productions}),
            productions=[
                ProductionCatalog(
                    id=production.id,
                    line=production.line,
                    start_day=production.start_day,
                    end_day=production.end_day,
                    bottle_model_id=production.bottle_model_id,
                    segment_id=production.segment_id,
                    dar_profile_id=production.dar_profile_id,
                    branches=[
                        BranchCatalog(
                            id=branch.branch,
                            rows=branch.rows,
                            data_start=branch.data_start,
                            data_end=branch.data_end,
                        )
                        for branch in production.branches
                    ],
                )
                for production in productions
            ],
            metrics=[
                OptionCatalog(id=metric.value, label=label)
                for metric, label in METRIC_LABELS.items()
            ],
            windows=[
                OptionCatalog(id=window.value, label=label)
                for window, label in WINDOW_LABELS.items()
            ],
            filters=FilterOptions(
                defect_codes=list(filters.defect_codes),
                cavities=list(filters.cavities),
                colors=list(filters.colors),
                sections=list(filters.sections),
                machine_codes=list(filters.machine_codes),
            ),
            max_productions_per_query=len(productions),
        )
