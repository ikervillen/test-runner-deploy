"""Composition root for the legacy customer score: wires sources and scorers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl

from app.domain.customer_score import (
    CustomerScoreAnalysis,
    CustomerScoreMeta,
    DarScore,
    ProductionCustomerScore,
    ShiftCustomerScore,
    ZcScore,
)
from app.domain.models import Production, TrendRequest
from app.domain.score_config import (
    ScoreConfig,
    apply_score_config_override,
    effective_score_config,
)
from app.domain.shifts import shift_windows
from app.infrastructure.aggregates.alerts import AlertAggregateResolver
from app.infrastructure.aggregates.mia import MiaAggregateResolver
from app.infrastructure.aggregates.zc import ZcAggregateResolver
from app.infrastructure.repositories.alert_defect_normalizer import ExcelAlertDefectNormalizer
from app.infrastructure.repositories.segment_dar_profiles import JsonSegmentDarProfileRepository

from .aql import AqlScorer
from .assembly import compose
from .capacity import active_cavities, cavity_minutes, production_bounds, selected_branches
from .catalog import SeverityWorkbook
from .config import load_score_config
from .dar import DarScorer
from .defects import AlertDefectResolver
from .mia import MiaScorer
from .sources.alerts import AlertEventSource
from .sources.inspected import InspectedTotalsSource
from .types import EMPTY_AQL, AqlResult, DefectRate
from .zc import ZcScorer


class PolarsCustomerScoreRepository:
    """Implements `CustomerScoreRepository` over Parquet/Excel inputs."""

    def __init__(
        self,
        config_path: Path,
        severity_path: Path,
        alerts_root: Path,
        inspected_totals_root: Path,
        hot_end_path: Path,
        defect_labels: Mapping[int, str] | None,
        *,
        alert_defect_labels: Mapping[int, str] | None = None,
        alert_defect_normalizer: ExcelAlertDefectNormalizer | None = None,
        segment_dar_profiles: JsonSegmentDarProfileRepository | None = None,
        quality_aggregate_root: Path | None = None,
        data_root: Path | None = None,
    ) -> None:
        self._config_path = config_path.resolve()
        mia_labels = dict(defect_labels or {})
        alert_labels = dict(alert_defect_labels or defect_labels or {})
        alerts_root = alerts_root.resolve()
        inspected_root = inspected_totals_root.resolve()
        hot_end = hot_end_path.resolve()
        aggregates = quality_aggregate_root

        self.alerts = AlertEventSource(
            alerts_root,
            AlertAggregateResolver(aggregates, alerts_root) if aggregates is not None else None,
        )
        self.defects = AlertDefectResolver(
            normalizer=alert_defect_normalizer,
            severity_workbook=SeverityWorkbook(severity_path),
            alert_labels=alert_labels,
            segment_dar_profiles=segment_dar_profiles,
        )
        self.mia = MiaScorer(
            inspected=InspectedTotalsSource(inspected_root),
            aggregates=(
                MiaAggregateResolver(aggregates, data_root, inspected_root)
                if aggregates is not None and data_root is not None
                else None
            ),
            labels=mia_labels,
        )
        self.aql = AqlScorer(alerts=self.alerts, defects=self.defects, labels=alert_labels)
        self.dar = DarScorer(alerts=self.alerts, defects=self.defects)
        self.zc = ZcScorer(
            hot_end,
            ZcAggregateResolver(aggregates, hot_end) if aggregates is not None else None,
        )

    def analyze(
        self,
        request: TrendRequest,
        productions: tuple[Production, ...],
        reference_productions: tuple[Production, ...],
        *,
        score_config_override: Mapping[str, Any] | None = None,
    ) -> CustomerScoreAnalysis:
        config = apply_score_config_override(
            load_score_config(self._config_path), score_config_override
        )
        selected_ids = {production.id for production in productions}

        current = self.mia.production_rates(request, productions, respect_branch_selection=True)
        historical = self.mia.production_rates(
            request, reference_productions, respect_branch_selection=False
        )
        aql = self.aql.score_productions(request, productions, config)
        dar = self.dar.score_productions(request, productions, aql.available_cavity_minutes, config)
        zc, zc_warnings = self.zc.score_productions(productions, config)

        rows = [
            self._production_row(
                production,
                request,
                selected_ids,
                current.rates,
                historical.rates,
                aql.by_production.get(production.id, EMPTY_AQL),
                dar.get(production.id),
                zc.get(production.id),
                config,
            )
            for production in productions
        ]
        warnings = {*current.warnings, *historical.warnings, *aql.warnings, *zc_warnings}
        return CustomerScoreAnalysis(
            productions=tuple(sorted(rows, key=lambda row: row.total_penalty, reverse=True)),
            warnings=tuple(sorted(warnings)),
            meta=CustomerScoreMeta(
                query_time_ms=0.0,
                config_version=config.version,
                files_read=current.files_read + historical.files_read + aql.files_read,
                rows_scanned=current.rows_scanned + historical.rows_scanned + aql.rows_scanned,
                effective_config=effective_score_config(config),
            ),
        )

    def _production_row(
        self,
        production: Production,
        request: TrendRequest,
        selected_ids: set[str],
        current_rates: tuple[DefectRate, ...],
        historical_rates: tuple[DefectRate, ...],
        aql: AqlResult,
        dar: DarScore | None,
        zc: ZcScore | None,
        config: ScoreConfig,
    ) -> ProductionCustomerScore:
        mia_details, mia_component = self.mia.score(
            production.id, selected_ids, current_rates, historical_rates, config
        )
        composite = compose(mia=mia_component, aql=aql.components, dar=dar, zc=zc, config=config)
        warnings = (
            () if composite.components else ("Sin componentes evaluables para esta produccion.",)
        )
        return ProductionCustomerScore(
            production_id=production.id,
            line=production.line,
            total_penalty=composite.total_penalty,
            normalized_score=composite.normalized_score,
            evaluated_weight=composite.evaluated_weight,
            status=composite.status,
            main_causes=composite.main_causes,
            components=composite.components,
            mia_details=mia_details,
            aql_categories=aql.categories,
            dar=dar,
            zc=zc,
            shift_scores=self._shift_rows(
                request, production, selected_ids, historical_rates, config
            ),
            warnings=warnings,
        )

    def _shift_rows(
        self,
        request: TrendRequest,
        production: Production,
        selected_ids: set[str],
        historical_rates: tuple[DefectRate, ...],
        config: ScoreConfig,
    ) -> tuple[ShiftCustomerScore, ...]:
        branches = selected_branches(production.branches, request)
        if not branches:
            return ()
        start, end = production_bounds(branches)
        cavities = active_cavities(branches, request, config)
        paths = self.alerts.existing_paths(production)
        events = (
            self.alerts.overlapping(paths, request, start, end)
            if paths
            else AlertEventSource.empty()
        )
        events = self.defects.annotate(production, events)
        windows = shift_windows(start, end)
        zc_by_shift = self.zc.score_by_shift(production, config, windows)

        rows: list[ShiftCustomerScore] = []
        for window in windows:
            span = ((window.start, window.end),)
            shift_events = events.filter(
                (pl.col("_event_start") < window.end) & (pl.col("_event_end") > window.start)
            )
            available = cavity_minutes(span, cavities)
            shift_id = f"{production.id} | {window.id}"
            current_rates = self.mia.shift_rates(production, request, branches, span, shift_id)
            mia_details, mia_component = self.mia.score(
                shift_id, selected_ids, current_rates, historical_rates, config
            )
            aql = self.aql.score_shift(shift_events, span, available, config)
            dar = self.dar.score_shift(shift_events, span, available, config, production)
            zc = zc_by_shift.get(window.id)
            composite = compose(
                mia=mia_component, aql=aql.components, dar=dar, zc=zc, config=config
            )
            rows.append(
                ShiftCustomerScore(
                    shift=window.label,
                    operational_day=window.operational_day,
                    shift_name=window.shift_name,
                    start=window.start.isoformat(),
                    end=window.end.isoformat(),
                    total_penalty=composite.total_penalty,
                    normalized_score=composite.normalized_score,
                    evaluated_weight=composite.evaluated_weight,
                    status=composite.status,
                    main_causes=composite.main_causes,
                    components=composite.components,
                    mia_details=mia_details,
                    aql_categories=aql.categories,
                    dar=dar,
                    zc=zc,
                    warnings=(),
                )
            )
        return tuple(rows)
