"""DAR component: deduplicated cavity-minutes of customer-critical defect groups."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import polars as pl

from app.domain.customer_score import CustomerScoreComponent, DarBreakdown, DarScore
from app.domain.models import Production, TrendRequest
from app.domain.score_config import ScoreConfig, as_float, as_int
from app.domain.score_math import (
    _aql_band_score,
    _dar_score,
    _dar_threshold_label,
    _mapping,
)

from . import defects as defect_columns
from .capacity import production_bounds
from .defects import AlertDefectResolver
from .intervals import clip_to_windows, merged_interval_summary
from .sources.alerts import AlertEventSource
from .types import Window, Windows

# (group, cavity) -> affected intervals. Overlaps within a key count once.
Intervals = dict[tuple[str, str], list[Window]]
ObservedCodes = dict[str, set[int]]


class DarScorer:
    def __init__(self, *, alerts: AlertEventSource, defects: AlertDefectResolver) -> None:
        self._alerts = alerts
        self._defects = defects

    def score_productions(
        self,
        request: TrendRequest,
        productions: tuple[Production, ...],
        available_cavity_minutes: Mapping[str, float],
        config: ScoreConfig,
    ) -> dict[str, DarScore]:
        result: dict[str, DarScore] = {}
        if not bool(config.dar.get("enabled", True)):
            return result
        for production in productions:
            intervals: Intervals = defaultdict(list)
            observed: ObservedCodes = defaultdict(set)
            paths = self._alerts.existing_paths(production)
            if paths:
                start, end = production_bounds(production.branches)
                events = self._alerts.clipped(paths, request, start, end)
                for row in events.iter_rows(named=True):
                    resolved = self._defects.resolve(
                        as_int(row.get("defect_code")), row.get("defect_description")
                    )
                    group = self._defects.dar_group(production, resolved.code, resolved.description)
                    _collect(
                        intervals,
                        observed,
                        code=resolved.code,
                        group=group,
                        cavity=row.get("cavity"),
                        spans=_span(row.get("_clipped_start"), row.get("_clipped_end")),
                    )
            result[production.id] = _build(
                intervals, observed, available_cavity_minutes.get(production.id, 0.0), config
            )
        return result

    def score_shift(
        self,
        events: pl.DataFrame,
        windows: Windows,
        available: float,
        config: ScoreConfig,
        production: Production,
    ) -> DarScore:
        """Events must be annotated by `AlertDefectResolver.annotate`."""
        intervals: Intervals = defaultdict(list)
        observed: ObservedCodes = defaultdict(set)
        if events.height > 0 and windows:
            for row in events.iter_rows(named=True):
                code = as_int(row.get(defect_columns.CODE_COLUMN))
                group = row.get(defect_columns.DAR_GROUP_COLUMN)
                if code is None:
                    resolved = self._defects.resolve(
                        as_int(row.get("defect_code")), row.get("defect_description")
                    )
                    code = resolved.code
                    group = self._defects.dar_group(production, code, resolved.description)
                start, end = row.get("_event_start"), row.get("_event_end")
                spans = (
                    clip_to_windows(start, end, windows)
                    if isinstance(start, datetime) and isinstance(end, datetime)
                    else []
                )
                _collect(
                    intervals,
                    observed,
                    code=code,
                    group=group,
                    cavity=row.get("cavity"),
                    spans=spans,
                )
        return _build(intervals, observed, available, config)


def dar_component(score: DarScore | None) -> CustomerScoreComponent | None:
    if score is None:
        return None
    return CustomerScoreComponent(
        component="DAR",
        label="DAR",
        score=score.score,
        weight=score.weight,
        contribution=score.contribution,
        details=score.details,
    )


def _span(start: Any, end: Any) -> list[Window]:
    if isinstance(start, datetime) and isinstance(end, datetime):
        return [(start, end)]
    return []


def _collect(
    intervals: Intervals,
    observed: ObservedCodes,
    *,
    code: int | None,
    group: Any,
    cavity: Any,
    spans: list[Window],
) -> None:
    if code is None or not isinstance(group, str) or not spans:
        return
    observed[group].add(code)
    intervals[(group, str(cavity or "-"))].extend(spans)


def _build(
    intervals: Intervals, observed: ObservedCodes, available: float, config: ScoreConfig
) -> DarScore:
    event_count, affected = merged_interval_summary(intervals)
    exposure = affected / available * 100 if available else 0.0
    aql = as_float(config.dar.get("aql_percent"))
    if aql is None:
        score = 0.0
    elif aql <= 0:
        score = _dar_score(exposure, _mapping(config.dar))
    else:
        score = _aql_band_score(exposure / aql)
    weight = float(_mapping(config.weights).get("dar", 8.0))
    return DarScore(
        event_count=event_count,
        affected_minutes=round(affected, 4),
        total_cavity_minutes=round(available, 4),
        exposure_percent=round(exposure, 4),
        aql_percent=round(aql, 4) if aql is not None else None,
        score=round(score, 4),
        weight=round(weight, 4),
        contribution=round(score * weight, 4),
        details=(
            f"{event_count} eventos DAR deduplicados; "
            f"{exposure:.4f} % min-cavidad; "
            f"{_observed_summary(observed)}."
        ),
        breakdown=_breakdown(intervals, observed, affected, available, _mapping(config.dar)),
    )


def _observed_summary(observed: ObservedCodes) -> str:
    if not observed:
        return "sin eventos DAR normalizados"
    return "; ".join(
        f"{group}: codigos normalizados {sorted(codes)}"
        for group, codes in sorted(observed.items())
    )


def _breakdown(
    intervals: Intervals,
    observed: ObservedCodes,
    total_affected: float,
    total_available: float,
    dar_config: Mapping[str, Any],
) -> tuple[DarBreakdown, ...]:
    rows: list[DarBreakdown] = []
    for group in sorted({group for group, _ in intervals} | set(observed)):
        event_count, affected = merged_interval_summary(
            {key: spans for key, spans in intervals.items() if key[0] == group}
        )
        exposure = affected / total_available * 100 if total_available else 0.0
        rows.append(
            DarBreakdown(
                group=group,
                codes=tuple(sorted(observed.get(group, set()))),
                event_count=event_count,
                affected_minutes=round(affected, 4),
                share_percent=round(affected / total_affected * 100 if total_affected else 0.0, 4),
                exposure_percent=round(exposure, 4),
                score=round(_dar_score(exposure, dar_config), 4),
                threshold_label=_dar_threshold_label(exposure, dar_config),
            )
        )
    return tuple(sorted(rows, key=lambda row: row.affected_minutes, reverse=True))
