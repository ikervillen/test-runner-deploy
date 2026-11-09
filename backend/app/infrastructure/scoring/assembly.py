"""Combine component scores into one weighted penalty (same for productions and shifts)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.customer_score import CustomerScoreCause, CustomerScoreComponent, DarScore, ZcScore
from app.domain.score_config import ScoreConfig
from app.domain.score_math import _status, main_causes

from .dar import dar_component
from .zc import zc_component


@dataclass(frozen=True, slots=True)
class Composite:
    components: tuple[CustomerScoreComponent, ...]
    total_penalty: float
    evaluated_weight: float
    normalized_score: float
    status: str
    main_causes: tuple[CustomerScoreCause, ...]


def compose(
    *,
    mia: CustomerScoreComponent | None,
    aql: Iterable[CustomerScoreComponent],
    dar: DarScore | None,
    zc: ZcScore | None,
    config: ScoreConfig,
) -> Composite:
    """Only components with positive weight take part; the penalty is their weighted sum."""
    components = tuple(
        item
        for item in (mia, *aql, dar_component(dar), zc_component(zc))
        if item is not None and item.weight > 0
    )
    total_penalty = round(sum(item.contribution for item in components), 4)
    evaluated_weight = round(sum(item.weight for item in components), 4)
    return Composite(
        components=components,
        total_penalty=total_penalty,
        evaluated_weight=evaluated_weight,
        normalized_score=round(total_penalty / evaluated_weight if evaluated_weight else 0.0, 4),
        status=_status(total_penalty, config),
        main_causes=main_causes(components, total_penalty, config),
    )
