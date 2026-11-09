"""Operational shifts: three fixed eight-hour windows per calendar day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

SHIFT_STARTS: tuple[tuple[str, int], ...] = (("Manana", 6), ("Tarde", 14), ("Noche", 22))
SHIFT_HOURS = 8


@dataclass(frozen=True, slots=True)
class ShiftWindow:
    id: str
    label: str
    operational_day: str
    shift_name: str
    start: datetime
    end: datetime


def shift_windows(production_start: datetime, production_end: datetime) -> tuple[ShiftWindow, ...]:
    """Shift windows overlapping the production, clipped to its bounds and ordered by start.

    The day before the production is included because the night shift of that day
    ends inside the production.
    """
    windows: list[ShiftWindow] = []
    current_day = (production_start - timedelta(days=1)).date()
    while current_day <= production_end.date():
        for shift_name, start_hour in SHIFT_STARTS:
            start = datetime.combine(current_day, datetime.min.time()).replace(hour=start_hour)
            clipped_start = max(start, production_start)
            clipped_end = min(start + timedelta(hours=SHIFT_HOURS), production_end)
            if clipped_end <= clipped_start:
                continue
            operational_day = current_day.isoformat()
            windows.append(
                ShiftWindow(
                    id=f"{operational_day}_{shift_name}",
                    label=f"{operational_day} {shift_name}",
                    operational_day=operational_day,
                    shift_name=shift_name,
                    start=clipped_start,
                    end=clipped_end,
                )
            )
        current_day += timedelta(days=1)
    return tuple(sorted(windows, key=lambda window: window.start))


def find_shift_window(timestamp: datetime, windows: tuple[ShiftWindow, ...]) -> ShiftWindow | None:
    for window in windows:
        if window.start <= timestamp < window.end:
            return window
    return None
