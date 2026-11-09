"""Unattended publication rules shared by `watch` and external schedulers."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .common import IntegrityError
from .publications import envelope

CUTOFF_LAG = timedelta(hours=1)
CUTOFF_STEP_MINUTES = 10


def industrial_cutoff(moment: datetime) -> datetime:
    """Latest closed ten-minute industrial cutoff at a local wall-clock moment."""
    if moment.tzinfo is not None:
        raise IntegrityError("Use a local industrial moment; UTC conversion is not implicit")
    lagged = moment - CUTOFF_LAG
    minute = lagged.minute // CUTOFF_STEP_MINUTES * CUTOFF_STEP_MINUTES
    return lagged.replace(minute=minute, second=0, microsecond=0)


def require_continuous_source(input_root: Path) -> None:
    _, incoming = envelope(input_root)
    if incoming.get("source_mode") != "continuous":
        raise IntegrityError(
            "Unattended publication requires a continuous confirmed producer; "
            "the isolated offline snapshot cannot certify future delivery"
        )
