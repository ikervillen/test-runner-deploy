"""Unattended publication rules shared by `watch` and external schedulers.

The scheduler's moment is only a ceiling: each production is calculated as far as
its sources accredit (`app.gold.coverage`). No fixed lag is applied.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .common import IntegrityError
from .coverage import floor_step
from .publications import envelope


def attempt_cutoff(moment: datetime) -> datetime:
    """Ceiling for an attempt at a local wall-clock moment: the last ten-minute step."""
    if moment.tzinfo is not None:
        raise IntegrityError("Use a local industrial moment; UTC conversion is not implicit")
    return floor_step(moment)


def require_continuous_source(input_root: Path) -> None:
    _, incoming = envelope(input_root)
    if incoming.get("source_mode") != "continuous":
        raise IntegrityError(
            "Unattended publication requires a continuous confirmed producer; "
            "the isolated offline snapshot cannot certify future delivery"
        )
