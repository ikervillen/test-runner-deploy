"""AQL component: cavity-minutes affected by alerts, per severity category."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

import polars as pl

from app.domain.customer_score import AqlCategoryScore, CustomerScoreComponent
from app.domain.models import Production, TrendRequest
from app.domain.score_config import SEVERITIES, ScoreConfig, as_float, as_int
from app.domain.score_math import _aql_band_score, _mapping, _zero_aql_score

from . import defects as defect_columns
from .capacity import active_cavities, cavity_minutes, production_bounds, selected_branches
from .defects import AlertDefectResolver
from .intervals import overlap_minutes
from .labels import top_code, top_defect_label
from .sources.alerts import AlertEventSource
from .types import EMPTY_AQL, AqlResult, AqlScan, Windows


@dataclass
class _Exposure:
    """Affected cavity-minutes accumulated per severity and per defect code."""

    minutes: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    by_code: dict[str, dict[int, float]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(float))
    )
    labels: dict[int, str] = field(default_factory=dict)

    def add(self, severity: str | None, code: int | None, label: str, duration: float) -> None:
        if code is None or severity not in SEVERITIES or duration <= 0:
            return
        self.minutes[severity] += duration
        self.by_code[severity][code] += duration
        self.labels.setdefault(code, label)


class AqlScorer:
    def __init__(
        self,
        *,
        alerts: AlertEventSource,
        defects: AlertDefectResolver,
        labels: Mapping[int, str],
    ) -> None:
        self._alerts = alerts
        self._defects = defects
        self._labels = labels

    def score_productions(
        self, request: TrendRequest, productions: tuple[Production, ...], config: ScoreConfig
    ) -> AqlScan:
        by_production: dict[str, AqlResult] = {}
        available_by_production: dict[str, float] = {}
        warnings: set[str] = set()
        files_read = rows_scanned = 0
        if not bool(config.aql.get("enabled", True)):
            return AqlScan(by_production, available_by_production, 0, 0, ())
        for production in productions:
            branches = selected_branches(production.branches, request)
            if not branches:
                continue
            start, end = production_bounds(branches)
            available = cavity_minutes(((start, end),), active_cavities(branches, request, config))
            available_by_production[production.id] = available
            paths = self._alerts.existing_paths(production)
            if not paths:
                warnings.add(
                    f"AQL no evaluado con avisos/rechazos para {production.id}: sin ficheros."
                )
                continue
            files_read += len(paths)
            events = self._alerts.clipped(paths, request, start, end)
            rows_scanned += events.height
            exposure = _Exposure()
            for row in events.iter_rows(named=True):
                resolved = self._defects.resolve(
                    as_int(row["defect_code"]), row["defect_description"]
                )
                exposure.add(
                    resolved.severity,
                    resolved.code,
                    resolved.description,
                    float(row["_duration_minutes"] or 0.0),
                )
            by_production[production.id] = self._evaluate(
                exposure,
                available,
                config,
                details_suffix=" desde avisos/rechazos",
                warnings=warnings,
            )
        return AqlScan(
            by_production,
            available_by_production,
            files_read,
            rows_scanned,
            tuple(sorted(warnings)),
        )

    def score_shift(
        self, events: pl.DataFrame, windows: Windows, available: float, config: ScoreConfig
    ) -> AqlResult:
        """Events must be annotated by `AlertDefectResolver.annotate`."""
        exposure = _Exposure()
        if events.height > 0 and windows:
            for row in events.iter_rows(named=True):
                code = as_int(row.get(defect_columns.CODE_COLUMN))
                if code is None:
                    resolved = self._defects.resolve(
                        as_int(row.get("defect_code")), row.get("defect_description")
                    )
                    code, severity, label = resolved.code, resolved.severity, resolved.description
                else:
                    severity = row.get(defect_columns.SEVERITY_COLUMN)
                    label = str(row.get(defect_columns.DESCRIPTION_COLUMN) or "")
                start, end = row.get("_event_start"), row.get("_event_end")
                if not isinstance(start, datetime) or not isinstance(end, datetime):
                    continue
                exposure.add(severity, code, label, overlap_minutes(start, end, windows))
        return self._evaluate(exposure, available, config, details_suffix="", warnings=None)

    def _evaluate(
        self,
        exposure: _Exposure,
        available: float,
        config: ScoreConfig,
        *,
        details_suffix: str,
        warnings: set[str] | None,
    ) -> AqlResult:
        limits = _mapping(config.aql.get("category_limits_percent"))
        weights = _mapping(_mapping(config.weights).get("categories"))
        categories: list[AqlCategoryScore] = []
        components: list[CustomerScoreComponent] = []
        for severity in SEVERITIES:
            limit = as_float(limits.get(severity))
            weight = as_float(weights.get(severity)) or 0.0
            if limit is None or weight <= 0:
                if warnings is not None:
                    warnings.add(f"AQL no evaluado para {severity}: falta limite o peso.")
                continue
            affected = exposure.minutes[severity]
            percent = affected / available * 100 if available else 0.0
            if limit <= 0:
                ratio, score = None, _zero_aql_score(percent, _mapping(config.aql))
            else:
                ratio, score = percent / limit, _aql_band_score(percent / limit)
            code = top_code(exposure.by_code[severity])
            categories.append(
                AqlCategoryScore(
                    category=severity,
                    affected_cavity_minutes=round(affected, 4),
                    total_cavity_minutes=round(available, 4),
                    exposure_percent=round(percent, 4),
                    aql_percent=round(limit, 4),
                    ratio=round(ratio, 4) if ratio is not None else None,
                    score=round(score, 4),
                    weight=round(weight, 4),
                    contribution=round(score * weight, 4),
                    top_defect_code=code,
                    top_defect_label=top_defect_label(code, exposure.labels, self._labels),
                )
            )
            components.append(
                CustomerScoreComponent(
                    component=severity,
                    label=severity,
                    score=round(score, 4),
                    weight=round(weight, 4),
                    contribution=round(score * weight, 4),
                    details=f"{percent:.2f} % min-cavidad vs AQL {limit:.2f} %{details_suffix}",
                )
            )
        return AqlResult(tuple(categories), tuple(components)) if categories else EMPTY_AQL
