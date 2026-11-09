"""Shared, unchanged scalar score formulas used by legacy and Gold readers.

Extraction only: rounding and data-selection policies remain with their callers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from math import sqrt
from typing import Any

from app.domain.customer_score import CustomerScoreCause, CustomerScoreComponent


@dataclass(frozen=True, slots=True)
class _ScoreConfig:
    version: int
    raw: Mapping[str, Any]

    @property
    def weights(self) -> Mapping[str, Any]:
        return _mapping(self.raw.get("weights"))

    @property
    def mia(self) -> Mapping[str, Any]:
        return _mapping(self.raw.get("mia"))

    @property
    def aql(self) -> Mapping[str, Any]:
        return _mapping(self.raw.get("aql"))

    @property
    def dar(self) -> Mapping[str, Any]:
        return _mapping(self.raw.get("dar"))

    @property
    def zc(self) -> Mapping[str, Any]:
        return _mapping(self.raw.get("zc"))

    @property
    def score(self) -> Mapping[str, Any]:
        return _mapping(self.raw.get("score"))


def _sigma_score(z_score: float, config: _ScoreConfig) -> float:
    if z_score <= 0:
        return 0.0
    points = [
        (float(item["z"]), float(item["score"]))
        for item in _sequence(_mapping(config.mia.get("sigma_curve")).get("points"))
        if isinstance(item, Mapping) and "z" in item and "score" in item
    ] or [(0.0, 0.0), (1.0, 1.0), (2.0, 5.0), (2.72, 10.0)]
    points = sorted(points)
    for (left_z, left_score), (right_z, right_score) in pairwise(points):
        if left_z <= z_score <= right_z:
            ratio = (z_score - left_z) / max(right_z - left_z, 0.0001)
            return left_score + ratio * (right_score - left_score)
    return float(_mapping(config.mia.get("sigma_curve")).get("cap_score", 10.0))


def _mia_absolute_rate_factor(current_percent: float, config: _ScoreConfig) -> float:
    factor_config = _mapping(config.mia.get("absolute_rate_factor"))
    if not bool(factor_config.get("enabled", False)):
        return 1.0
    minimum = float(factor_config.get("min_relevant_percent", 0.4) or 0.4)
    span = float(factor_config.get("full_weight_span_percent", 2.0) or 2.0)
    if span <= 0:
        return 1.0
    return min(1.0, max(0.0, (current_percent - minimum) / span))


def _zero_aql_score(exposure_percent: float, section: Mapping[str, Any]) -> float:
    policy = _mapping(section.get("zero_or_near_zero_aql_policy"))
    if not bool(policy.get("enabled", True)):
        return 10.0 if exposure_percent > 0 else 0.0
    low_threshold = float(policy.get("low_presence_threshold_percent", 0.01) or 0.01)
    clear_threshold = float(policy.get("clear_presence_threshold_percent", 0.05) or 0.05)
    low_score = float(policy.get("low_presence_score", 5.0) or 5.0)
    clear_score = float(policy.get("clear_presence_score", 10.0) or 10.0)
    if exposure_percent <= 0:
        return 0.0
    if exposure_percent <= low_threshold:
        return low_score
    if exposure_percent < clear_threshold:
        return low_score
    return clear_score


def _zero_aql_threshold_label(exposure_percent: float, section: Mapping[str, Any]) -> str:
    policy = _mapping(section.get("zero_or_near_zero_aql_policy"))
    if not bool(policy.get("enabled", True)):
        return "Presencia > 0" if exposure_percent > 0 else "Sin presencia"
    low_threshold = float(policy.get("low_presence_threshold_percent", 0.01) or 0.01)
    clear_threshold = float(policy.get("clear_presence_threshold_percent", 0.05) or 0.05)
    if exposure_percent <= 0:
        return "Sin presencia"
    if exposure_percent <= low_threshold:
        return f"0 < exposicion <= {low_threshold:.2f} %"
    if exposure_percent < clear_threshold:
        return f"{low_threshold:.2f} % < exposicion < {clear_threshold:.2f} %"
    return f"exposicion >= {clear_threshold:.2f} %"


def _dar_score(exposure_percent: float, section: Mapping[str, Any]) -> float:
    thresholds = _mapping(section.get("progressive_thresholds_percent"))
    if not thresholds:
        return _zero_aql_score(exposure_percent, section)
    if not bool(thresholds.get("enabled", True)):
        return _zero_aql_score(exposure_percent, section)
    score_5_threshold = float(thresholds.get("score_5", 0.1) or 0.1)
    score_10_threshold = float(thresholds.get("score_10", 0.4) or 0.4)
    if exposure_percent <= 0:
        return 0.0
    if score_5_threshold <= 0 or score_10_threshold <= score_5_threshold:
        return 10.0 if exposure_percent > 0 else 0.0
    if exposure_percent < score_5_threshold:
        return 5.0 * exposure_percent / score_5_threshold
    if exposure_percent < score_10_threshold:
        span = score_10_threshold - score_5_threshold
        return 5.0 + 5.0 * (exposure_percent - score_5_threshold) / span
    return 10.0


def _dar_threshold_label(exposure_percent: float, section: Mapping[str, Any]) -> str:
    thresholds = _mapping(section.get("progressive_thresholds_percent"))
    if not thresholds or not bool(thresholds.get("enabled", True)):
        return _zero_aql_threshold_label(exposure_percent, section)
    score_5_threshold = float(thresholds.get("score_5", 0.1) or 0.1)
    score_10_threshold = float(thresholds.get("score_10", 0.4) or 0.4)
    if exposure_percent <= 0:
        return "Sin presencia"
    if score_5_threshold <= 0 or score_10_threshold <= score_5_threshold:
        return "Presencia DAR"
    if exposure_percent < score_5_threshold:
        return f"0 < exposicion < {score_5_threshold:.2f} %"
    if exposure_percent < score_10_threshold:
        return f"{score_5_threshold:.2f} % <= exposicion < {score_10_threshold:.2f} %"
    return f"exposicion >= {score_10_threshold:.2f} %"


def _aql_band_score(ratio: float) -> float:
    return min(10.0, max(0.0, 5.0 * ratio))


def _status(total_penalty: float, config: _ScoreConfig) -> str:
    thresholds = _mapping(config.score.get("interpretation"))
    if total_penalty <= float(thresholds.get("good_max", 40.0)):
        return "good"
    if total_penalty <= float(thresholds.get("observed_max", 80.0)):
        return "observed"
    if total_penalty <= float(thresholds.get("bad_max", 120.0)):
        return "bad"
    return "critical"


def _sample_sigma(values: list[float], mean_value: float) -> float:
    if len(values) < 2:
        return 0.0
    return sqrt(sum((value - mean_value) ** 2 for value in values) / (len(values) - 1))


def main_causes(
    components: tuple[CustomerScoreComponent, ...],
    total_penalty: float,
    config: _ScoreConfig,
) -> tuple[CustomerScoreCause, ...]:
    """Components that explain the penalty: the largest one plus near ties, up to a limit."""
    positive = sorted(
        (component for component in components if component.contribution > 0),
        key=lambda component: component.contribution,
        reverse=True,
    )
    if not positive:
        return ()
    main_config = _mapping(config.score.get("main_cause"))
    include_ties = bool(main_config.get("include_ties", True))
    margin = float(main_config.get("tie_relative_margin", 0.1))
    limit = int(main_config.get("max_causes", 2))
    best = positive[0].contribution
    selected = [
        component
        for component in positive
        if component is positive[0]
        or (include_ties and best > 0 and (best - component.contribution) / best <= margin)
    ][:limit]
    return tuple(
        CustomerScoreCause(
            component=component.component,
            label=component.label,
            contribution=component.contribution,
            share_of_total_penalty=round(
                component.contribution / total_penalty if total_penalty else 0.0,
                4,
            ),
        )
        for component in selected
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> tuple[Any, ...]:
    return tuple(value) if isinstance(value, list | tuple) else ()
