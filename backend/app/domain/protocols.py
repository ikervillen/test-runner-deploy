from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.domain.customer_quality import CustomerQualityAnalysis
from app.domain.customer_score import CustomerScoreAnalysis
from app.domain.models import AvailableFilters, Production, TrendRequest


class CatalogRepository(Protocol):
    def list_productions(self) -> tuple[Production, ...]: ...

    def get_productions(self, production_ids: tuple[str, ...]) -> tuple[Production, ...]: ...

    def version(self) -> int: ...


class MetadataRepository(Protocol):
    def available_filters(self, productions: tuple[Production, ...]) -> AvailableFilters: ...


class CustomerQualityRepository(Protocol):
    def analyze(self, productions: tuple[Production, ...]) -> CustomerQualityAnalysis: ...


class CustomerScoreRepository(Protocol):
    def analyze(
        self,
        request: TrendRequest,
        productions: tuple[Production, ...],
        reference_productions: tuple[Production, ...],
        *,
        score_config_override: Mapping[str, Any] | None = None,
    ) -> CustomerScoreAnalysis: ...
