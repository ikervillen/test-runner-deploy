"""Interval arithmetic on datetime windows (cavity-minute exposure)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import polars as pl

from .types import Window, Windows


def minutes(window: Window) -> float:
    start, end = window
    return max((end - start).total_seconds() / 60, 0.0)


def merged_interval_summary(
    intervals_by_key: Mapping[tuple[str, str], list[Window]],
) -> tuple[int, float]:
    """Count and total duration of intervals after merging overlaps within each key."""
    merged_count = 0
    total_minutes = 0.0
    for intervals in intervals_by_key.values():
        ordered = sorted(intervals)
        if not ordered:
            continue
        current_start, current_end = ordered[0]
        for start, end in ordered[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
                continue
            merged_count += 1
            total_minutes += minutes((current_start, current_end))
            current_start, current_end = start, end
        merged_count += 1
        total_minutes += minutes((current_start, current_end))
    return merged_count, total_minutes


def clip_to_windows(start: datetime, end: datetime, windows: Windows) -> list[Window]:
    """Portions of [start, end] that fall inside each window; empty portions are dropped."""
    clipped: list[Window] = []
    for window_start, window_end in windows:
        clipped_start = max(start, window_start)
        clipped_end = min(end, window_end)
        if clipped_end > clipped_start:
            clipped.append((clipped_start, clipped_end))
    return clipped


def overlap_minutes(start: datetime, end: datetime, windows: Windows) -> float:
    return sum(minutes(window) for window in clip_to_windows(start, end, windows))


def within_windows_expr(column: str, windows: Windows) -> pl.Expr:
    expression: pl.Expr | None = None
    for start, end in windows:
        current = (pl.col(column) >= start) & (pl.col(column) < end)
        expression = current if expression is None else expression | current
    return expression if expression is not None else pl.lit(False)
