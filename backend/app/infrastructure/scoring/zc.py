"""ZC component: hot-end yield (rueda / theoretical) against the line target."""

from __future__ import annotations

import threading
from collections import defaultdict
from pathlib import Path

from app.domain.customer_score import CustomerScoreComponent, ZcScore
from app.domain.models import Production
from app.domain.score_config import ScoreConfig
from app.domain.score_math import _mapping
from app.domain.shifts import ShiftWindow, find_shift_window
from app.infrastructure.aggregates.zc import ZcAggregateResolver

from .capacity import production_bounds
from .sources import hot_end
from .types import ZcObservation

Points = list[tuple[float, float]]  # (rueda, theoretical)


class ZcScorer:
    def __init__(self, hot_end_path: Path, aggregates: ZcAggregateResolver | None) -> None:
        self._hot_end_path = hot_end_path
        self._aggregates = aggregates
        self._lock = threading.Lock()
        self._version = -1
        self._cache: tuple[ZcObservation, ...] = ()
        self._error: str | None = None

    def score_productions(
        self, productions: tuple[Production, ...], config: ScoreConfig
    ) -> tuple[dict[str, ZcScore], tuple[str, ...]]:
        result: dict[str, ZcScore] = {}
        if not bool(config.zc.get("enabled", True)):
            return result, ()
        observations, error = self.observations()
        if error is not None:
            return result, (error,)
        bounds = {
            production.id: (production.line, *production_bounds(production.branches))
            for production in productions
            if production.branches
        }
        points: dict[str, Points] = defaultdict(list)
        for observation in observations:
            for production_id, (line, start, end) in bounds.items():
                if observation.line == line and start <= observation.timestamp <= end:
                    points[production_id].append((observation.rueda, observation.theoretical))
        for production in productions:
            if points.get(production.id):
                result[production.id] = _score(points[production.id], production.line, config)
        return result, ()

    def score_by_shift(
        self, production: Production, config: ScoreConfig, windows: tuple[ShiftWindow, ...]
    ) -> dict[str, ZcScore]:
        if not bool(config.zc.get("enabled", True)) or not self._hot_end_path.exists():
            return {}
        start, end = production_bounds(production.branches)
        points: dict[str, Points] = defaultdict(list)
        observations, _ = self.observations()
        for observation in observations:
            if observation.line != production.line or not start <= observation.timestamp <= end:
                continue
            window = find_shift_window(observation.timestamp, windows)
            if window is not None:
                points[window.id].append((observation.rueda, observation.theoretical))
        return {
            shift: _score(shift_points, production.line, config)
            for shift, shift_points in points.items()
        }

    def observations(self) -> tuple[tuple[ZcObservation, ...], str | None]:
        """Observations cached by workbook mtime; the aggregate wins when published."""
        if not self._hot_end_path.exists():
            return (), hot_end.MISSING_WORKBOOK
        version = self._hot_end_path.stat().st_mtime_ns
        with self._lock:
            if version != self._version:
                aggregate = self._aggregates.observations() if self._aggregates else None
                if aggregate is not None:
                    self._cache, self._error = hot_end.from_aggregate(aggregate), None
                else:
                    self._cache, self._error = hot_end.from_workbook(self._hot_end_path)
                self._version = version
            return self._cache, self._error


def zc_component(score: ZcScore | None) -> CustomerScoreComponent | None:
    if score is None:
        return None
    return CustomerScoreComponent(
        component="ZC",
        label="Rendimiento ZC",
        score=score.score,
        weight=score.weight,
        contribution=score.contribution,
        details=(
            f"Rendimiento {score.actual_percent:.2f} % vs objetivo {score.target_percent:.2f} %"
            if score.actual_percent is not None
            else "Sin dato ZC"
        ),
    )


def _score(points: Points, line: str, config: ScoreConfig) -> ZcScore:
    target_config = _mapping(config.zc.get("target_percent"))
    target = float(_mapping(target_config.get("by_line")).get(line, target_config["default"]))
    divisor = float(config.zc.get("penalty_divisor_points", 2.0))
    weight = float(_mapping(config.weights).get("zc", 2.0))
    actual = sum(rueda for rueda, _ in points) / sum(theoretical for _, theoretical in points) * 100
    loss = max(0.0, target - actual)
    score = min(10.0, loss / max(divisor, 0.0001))
    return ZcScore(
        target_percent=round(target, 4),
        actual_percent=round(actual, 4),
        loss_points=round(loss, 4),
        score=round(score, 4),
        weight=round(weight, 4),
        contribution=round(score * weight, 4),
        windows=len(points),
    )
