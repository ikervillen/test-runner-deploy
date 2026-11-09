"""MIA component: per-defect rejection rate versus the historical reference."""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from app.domain.customer_score import CustomerScoreComponent, MiaScoreDetail
from app.domain.models import BranchFile, Production, TrendRequest
from app.domain.score_config import ScoreConfig
from app.domain.score_math import (
    _mapping,
    _mia_absolute_rate_factor,
    _sample_sigma,
    _sequence,
    _sigma_score,
)
from app.infrastructure.aggregates.mia import MiaAggregateResolver

from .capacity import selected_branches
from .labels import defect_label
from .sources import branches as branch_source
from .sources.inspected import InspectedTotalsSource
from .types import DefectRate, RateScan, Windows


class MiaScorer:
    def __init__(
        self,
        *,
        inspected: InspectedTotalsSource,
        aggregates: MiaAggregateResolver | None,
        labels: Mapping[int, str],
    ) -> None:
        self._inspected = inspected
        self._aggregates = aggregates
        self._labels = labels

    def production_rates(
        self,
        request: TrendRequest,
        productions: tuple[Production, ...],
        *,
        respect_branch_selection: bool,
    ) -> RateScan:
        """Rejection rate per defect and production. Reference runs ignore the branch filter."""
        rates: list[DefectRate] = []
        files_read = rows_scanned = 0
        warnings: set[str] = set()
        for production in productions:
            branches = (
                selected_branches(production.branches, request)
                if respect_branch_selection
                else production.branches
            )
            if not branches:
                continue
            aggregate = self._aggregate_rates(production, request, branches)
            if aggregate is not None:
                files_read += 1
                rows_scanned += aggregate.height
                rates.extend(_rates_from_aggregate(production.id, aggregate))
                continue
            files_read += len(branches)
            rows_scanned += sum(branch.rows for branch in branches)
            inspected, inspected_files, inspected_rows = self._inspected.production_total(
                production, request, branches
            )
            files_read += inspected_files
            rows_scanned += inspected_rows
            rejected = branch_source.rejected_by_defect(branches, request.filters)
            if inspected <= 0 and rejected.height > 0:
                inspected = branch_source.inspected_total(branches, request.filters)
                if inspected > 0:
                    warnings.add(
                        f"MIA usa denominador legacy para {production.id}: "
                        "no hay parquet de inspeccionadas reales."
                    )
            rates.extend(_rates_from_rejected(production.id, rejected, inspected))
        return RateScan(tuple(rates), files_read, rows_scanned, tuple(sorted(warnings)))

    def shift_rates(
        self,
        production: Production,
        request: TrendRequest,
        branches: tuple[BranchFile, ...],
        windows: Windows,
        shift_id: str,
    ) -> tuple[DefectRate, ...]:
        aggregate = self._aggregate_rates(
            production, request, branches, shift_id=shift_id.rsplit(" | ", 1)[-1]
        )
        if aggregate is not None:
            return tuple(_rates_from_aggregate(shift_id, aggregate))
        inspected = self._inspected.shift_total(production, request, branches, windows)
        if inspected <= 0:
            return ()
        rejected = branch_source.rejected_by_defect(branches, request.filters, windows)
        return tuple(_rates_from_rejected(shift_id, rejected, inspected))

    def score(
        self,
        production_id: str,
        selected_ids: set[str],
        current_rates: tuple[DefectRate, ...],
        historical_rates: tuple[DefectRate, ...],
        config: ScoreConfig,
    ) -> tuple[tuple[MiaScoreDetail, ...], CustomerScoreComponent | None]:
        if not bool(config.mia.get("enabled", True)):
            return (), None
        configured_codes = {int(code) for code in _sequence(config.mia.get("defect_codes"))}
        scope = _mapping(config.mia.get("historical_scope"))
        min_samples = int(scope.get("min_samples", 3))
        exclude_current = bool(scope.get("exclude_current_production", True))
        weight = float(_mapping(config.weights).get("mia_defect", 1.0))
        # Stable order prevents Polars hash-group order from changing ties/rounding.
        historical = sorted(historical_rates, key=lambda r: (r.production_id, r.defect_code))
        current = [
            rate
            for rate in sorted(current_rates, key=lambda r: r.defect_code)
            if rate.production_id == production_id
            and (not configured_codes or rate.defect_code in configured_codes)
        ]
        details: list[MiaScoreDetail] = []
        for rate in current:
            samples = [
                item.percent
                for item in historical
                if item.defect_code == rate.defect_code
                and (not exclude_current or item.production_id not in selected_ids)
            ]
            if len(samples) < min_samples:
                continue
            details.append(self._detail(rate, samples, weight, config))
        if not details:
            return (), None
        total = sum(item.contribution for item in details)
        top = max(details, key=lambda item: item.contribution)
        component = CustomerScoreComponent(
            component="MIA",
            label="MIA individual",
            score=round(total / max(len(details) * weight, 1.0), 4),
            weight=round(len(details) * weight, 4),
            contribution=round(total, 4),
            details=f"Principal MIA: {top.defect_code} - {top.defect_label}",
        )
        return tuple(sorted(details, key=lambda item: item.contribution, reverse=True)), component

    def _detail(
        self, rate: DefectRate, samples: list[float], weight: float, config: ScoreConfig
    ) -> MiaScoreDetail:
        mean_value = sum(samples) / len(samples)
        sigma = _sample_sigma(samples, mean_value)
        z_score: float | None
        if sigma <= 0:
            fixed = float(_mapping(config.mia.get("zero_sigma_policy")).get("fixed_score", 5.0))
            score = fixed if rate.percent > mean_value else 0.0
            z_score = None
        else:
            z_score = (rate.percent - mean_value) / sigma
            score = _sigma_score(z_score, config)
        score *= _mia_absolute_rate_factor(rate.percent, config)
        return MiaScoreDetail(
            defect_code=rate.defect_code,
            defect_label=defect_label(rate.defect_code, self._labels)
            or f"Defecto {rate.defect_code}",
            rejected_count=round(rate.rejected, 4),
            inspected_count=round(rate.inspected, 4),
            current_percent=round(rate.percent, 4),
            historical_mean_percent=round(mean_value, 4),
            historical_sigma_percent=round(sigma, 4),
            z_score=round(z_score, 4) if z_score is not None else None,
            score=round(score, 4),
            weight=weight,
            contribution=round(score * weight, 4),
            samples=len(samples),
        )

    def _aggregate_rates(
        self,
        production: Production,
        request: TrendRequest,
        branches: tuple[BranchFile, ...],
        *,
        shift_id: str | None = None,
    ) -> pl.DataFrame | None:
        if self._aggregates is None or not _aggregate_applies(request, production, branches):
            return None
        return self._aggregates.rates_for(production, shift_id)


def _aggregate_applies(
    request: TrendRequest, production: Production, branches: tuple[BranchFile, ...]
) -> bool:
    """Published aggregates cover whole productions without row-level filters."""
    filters = request.filters
    if any(
        (
            filters.defect_codes,
            filters.cavities,
            filters.colors,
            filters.sections,
            filters.machine_codes,
        )
    ):
        return False
    if not request.branches:
        return True
    selected = set(request.branches)
    return all(branch.branch in selected for branch in production.branches) and len(
        branches
    ) == len(production.branches)


def _rates_from_aggregate(owner_id: str, aggregate: pl.DataFrame) -> list[DefectRate]:
    return [
        DefectRate(
            production_id=owner_id,
            defect_code=int(defect_code),
            rejected=float(rejected),
            inspected=float(inspected),
            percent=float(rejected) / float(inspected) * 100,
        )
        for defect_code, rejected, inspected in aggregate.select(
            ["defect_code", "rejected", "inspected"]
        ).iter_rows()
        if defect_code is not None and float(inspected or 0) > 0
    ]


def _rates_from_rejected(
    owner_id: str, rejected: pl.DataFrame, inspected: float
) -> list[DefectRate]:
    if inspected <= 0:
        return []
    return [
        DefectRate(
            production_id=owner_id,
            defect_code=int(defect_code),
            rejected=float(value),
            inspected=inspected,
            percent=float(value) / inspected * 100,
        )
        for defect_code, value in rejected.iter_rows()
        if defect_code is not None
    ]
