"""Cavity-minute capacity: the denominator shared by AQL and DAR exposures."""

from __future__ import annotations

from datetime import datetime

from app.domain.models import BranchFile, TrendRequest
from app.domain.score_config import ScoreConfig, as_int
from app.domain.score_math import _mapping

from .intervals import minutes
from .sources import branches as branch_source
from .types import Windows


def production_bounds(branches: tuple[BranchFile, ...]) -> tuple[datetime, datetime]:
    return (
        min(branch.data_start for branch in branches),
        max(branch.data_end for branch in branches),
    )


def selected_branches(
    branches: tuple[BranchFile, ...], request: TrendRequest
) -> tuple[BranchFile, ...]:
    if not request.branches:
        return branches
    return tuple(branch for branch in branches if branch.branch in request.branches)


def active_cavities(
    branches: tuple[BranchFile, ...], request: TrendRequest, config: ScoreConfig
) -> int:
    """Cavities producing in the window: explicit filter, configured value, or observed."""
    minute_cavity = _mapping(config.aql.get("minute_cavity"))
    fallback = as_int(minute_cavity.get("fallback_active_cavities"))
    if request.filters.cavities:
        return len(set(request.filters.cavities))
    if minute_cavity.get("active_cavities_mode") == "configured" and fallback:
        return fallback
    if not branches:
        return fallback or 1
    return branch_source.distinct_cavities(branches, request.filters) or fallback or 1


def cavity_minutes(windows: Windows, cavities: int) -> float:
    return sum(minutes(window) for window in windows) * max(cavities, 1)
