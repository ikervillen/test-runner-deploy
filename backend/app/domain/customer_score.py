from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CustomerScoreCause:
    component: str
    label: str
    contribution: float
    share_of_total_penalty: float


@dataclass(frozen=True, slots=True)
class CustomerScoreComponent:
    component: str
    label: str
    score: float
    weight: float
    contribution: float
    details: str


@dataclass(frozen=True, slots=True)
class MiaScoreDetail:
    defect_code: int
    defect_label: str
    rejected_count: float
    inspected_count: float
    current_percent: float
    historical_mean_percent: float | None
    historical_sigma_percent: float | None
    z_score: float | None
    score: float
    weight: float
    contribution: float
    samples: int


@dataclass(frozen=True, slots=True)
class AqlCategoryScore:
    category: str
    affected_cavity_minutes: float
    total_cavity_minutes: float
    exposure_percent: float
    aql_percent: float | None
    ratio: float | None
    score: float
    weight: float
    contribution: float
    top_defect_code: int | None
    top_defect_label: str | None


@dataclass(frozen=True, slots=True)
class DarBreakdown:
    group: str
    codes: tuple[int, ...]
    event_count: int
    affected_minutes: float
    share_percent: float
    exposure_percent: float
    score: float
    threshold_label: str


@dataclass(frozen=True, slots=True)
class DarScore:
    event_count: int
    affected_minutes: float
    total_cavity_minutes: float
    exposure_percent: float
    aql_percent: float | None
    score: float
    weight: float
    contribution: float
    details: str
    breakdown: tuple[DarBreakdown, ...]


@dataclass(frozen=True, slots=True)
class ZcScore:
    target_percent: float
    actual_percent: float | None
    loss_points: float | None
    score: float
    weight: float
    contribution: float
    windows: int


@dataclass(frozen=True, slots=True)
class ShiftCustomerScore:
    shift: str
    operational_day: str
    shift_name: str
    start: str
    end: str
    total_penalty: float
    normalized_score: float
    evaluated_weight: float
    status: str
    main_causes: tuple[CustomerScoreCause, ...]
    components: tuple[CustomerScoreComponent, ...]
    mia_details: tuple[MiaScoreDetail, ...]
    aql_categories: tuple[AqlCategoryScore, ...]
    dar: DarScore | None
    zc: ZcScore | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductionCustomerScore:
    production_id: str
    line: str
    total_penalty: float
    normalized_score: float
    evaluated_weight: float
    status: str
    main_causes: tuple[CustomerScoreCause, ...]
    components: tuple[CustomerScoreComponent, ...]
    mia_details: tuple[MiaScoreDetail, ...]
    aql_categories: tuple[AqlCategoryScore, ...]
    dar: DarScore | None
    zc: ZcScore | None
    shift_scores: tuple[ShiftCustomerScore, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CustomerScoreMeta:
    query_time_ms: float
    config_version: int
    files_read: int
    rows_scanned: int
    effective_config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CustomerScoreAnalysis:
    productions: tuple[ProductionCustomerScore, ...]
    warnings: tuple[str, ...]
    meta: CustomerScoreMeta
