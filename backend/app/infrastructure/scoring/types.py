"""Small immutable records exchanged between scorers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.customer_score import AqlCategoryScore, CustomerScoreComponent

Window = tuple[datetime, datetime]
Windows = tuple[Window, ...]


@dataclass(frozen=True, slots=True)
class DefectRate:
    production_id: str
    defect_code: int
    rejected: float
    inspected: float
    percent: float


@dataclass(frozen=True, slots=True)
class RateScan:
    rates: tuple[DefectRate, ...]
    files_read: int
    rows_scanned: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AqlResult:
    categories: tuple[AqlCategoryScore, ...]
    components: tuple[CustomerScoreComponent, ...]


EMPTY_AQL = AqlResult((), ())


@dataclass(frozen=True, slots=True)
class AqlScan:
    by_production: dict[str, AqlResult]
    available_cavity_minutes: dict[str, float]
    files_read: int
    rows_scanned: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ZcObservation:
    timestamp: datetime
    line: str
    rueda: float
    theoretical: float
