from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict

from app.api.errors import ApplicationError
from app.application.selection import resolve_trend_selection
from app.domain.protocols import CatalogRepository, CustomerScoreRepository
from app.infrastructure.cache import LruCache
from app.schemas.customer_score import (
    CustomerScoreMetaResponse,
    CustomerScoreRequest,
    CustomerScoreResponse,
    ProductionCustomerScoreResponse,
)


class CalculateCustomerScore:
    def __init__(
        self,
        catalog_repository: CatalogRepository,
        customer_score_repository: CustomerScoreRepository,
        max_productions: int,
        cache: LruCache[str, CustomerScoreResponse],
        data_version_token: Callable[[], tuple[tuple[str, int], ...]],
    ) -> None:
        self._catalog_repository = catalog_repository
        self._customer_score_repository = customer_score_repository
        self._max_productions = max_productions
        self._cache = cache
        self._data_version_token = data_version_token

    def execute(self, payload: CustomerScoreRequest) -> CustomerScoreResponse:
        started = time.perf_counter()
        cache_key = self._cache_key(payload)
        response, cache_hit = self._cache.get_or_compute(
            cache_key,
            lambda: self._calculate(payload, started),
        )
        if cache_hit:
            return response.model_copy(
                update={
                    "meta": CustomerScoreMetaResponse(
                        **{
                            **response.meta.model_dump(),
                            "query_time_ms": (time.perf_counter() - started) * 1000,
                            "cache_hit": True,
                        }
                    ),
                }
            )

        return response

    def _calculate(
        self,
        payload: CustomerScoreRequest,
        started: float,
    ) -> CustomerScoreResponse:
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
        request, productions = resolve_trend_selection(
            payload.selection,
            self._catalog_repository,
            self._max_productions,
        )
        analysis = self._customer_score_repository.analyze(
            request,
            productions,
            self._catalog_repository.list_productions(),
            score_config_override=(
                payload.score_config_override.model_dump(exclude_none=True)
                if payload.score_config_override is not None
                else None
            ),
        )
        response = CustomerScoreResponse(
            status="completed",
            productions=[
                ProductionCustomerScoreResponse(**asdict(row)) for row in analysis.productions
            ],
            warnings=list(analysis.warnings),
            meta={
                **asdict(analysis.meta),
                "query_time_ms": (time.perf_counter() - started) * 1000,
                "cache_hit": False,
            },
        )
        return response

    def _cache_key(self, payload: CustomerScoreRequest) -> str:
        return json.dumps(
            {
                "catalog_version": self._catalog_repository.version(),
                "data_version": self._data_version_token(),
                "payload": payload.model_dump(mode="json", exclude_none=True),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
