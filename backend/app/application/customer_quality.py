from __future__ import annotations

import time
from dataclasses import asdict

from app.api.errors import ApplicationError
from app.domain.protocols import CatalogRepository, CustomerQualityRepository
from app.schemas.customer_quality import (
    ComplaintResponse,
    CustomerQualityRequest,
    CustomerQualityResponse,
    CustomerQualityValidationRowResponse,
    ProductionCustomerQualityResponse,
)


class CalculateCustomerQuality:
    def __init__(
        self,
        catalog_repository: CatalogRepository,
        customer_quality_repository: CustomerQualityRepository,
        max_productions: int,
    ) -> None:
        self._catalog_repository = catalog_repository
        self._customer_quality_repository = customer_quality_repository
        self._max_productions = max_productions

    def execute(self, payload: CustomerQualityRequest) -> CustomerQualityResponse:
        started = time.perf_counter()
        production_ids = tuple(dict.fromkeys(payload.selection.production_ids))
        effective_max = max(
            self._max_productions,
            len(self._catalog_repository.list_productions()),
        )
        if len(production_ids) > effective_max:
            raise ApplicationError(
                "TOO_MANY_PRODUCTIONS",
                f"Selecciona como maximo {self._max_productions} producciones.",
            )

        productions = self._catalog_repository.get_productions(production_ids)
        analysis = self._customer_quality_repository.analyze(productions)
        return CustomerQualityResponse(
            status="completed",
            rows=[
                ProductionCustomerQualityResponse(
                    production_id=row.production_id,
                    label=row.label.value,
                    relevant_count=row.relevant_count,
                    observed_count=row.observed_count,
                    excluded_count=row.excluded_count,
                    worst_reason=row.worst_reason,
                    explanation=row.explanation,
                    complaints=[
                        ComplaintResponse(**{**asdict(item), "treatment": item.treatment.value})
                        for item in row.complaints
                    ],
                )
                for row in analysis.rows
            ],
            validation_rows=[
                CustomerQualityValidationRowResponse(
                    **{**asdict(row), "customer_label": row.customer_label.value}
                )
                for row in analysis.validation_rows
            ],
            warnings=list(analysis.warnings),
            meta={
                "query_time_ms": (time.perf_counter() - started) * 1000,
                "productions": len(productions),
                "definition_version": "customer-quality-v1",
            },
        )
