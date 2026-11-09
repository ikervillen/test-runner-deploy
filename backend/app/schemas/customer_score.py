from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.api import StrictModel, TrendQueryRequest


class ScoreCategoryLimitsOverride(StrictModel):
    Critico: float | None = None
    Grave: float | None = None
    Mayor: float | None = None
    Menor: float | None = None
    Leve: float | None = None


class ScoreAqlOverride(StrictModel):
    category_limits_percent: ScoreCategoryLimitsOverride | None = None


class ScoreWeightsOverride(StrictModel):
    mia_defect: float | None = None
    categories: ScoreCategoryLimitsOverride | None = None
    dar: float | None = None
    zc: float | None = None


class ScoreDarThresholdsOverride(StrictModel):
    score_5: float | None = None
    score_10: float | None = None


class ScoreDarOverride(StrictModel):
    progressive_thresholds_percent: ScoreDarThresholdsOverride | None = None


class ScoreZcTargetOverride(StrictModel):
    default: float | None = None


class ScoreZcOverride(StrictModel):
    target_percent: ScoreZcTargetOverride | None = None


class ScoreInterpretationOverride(StrictModel):
    good_max: float | None = None
    observed_max: float | None = None
    bad_max: float | None = None


class ScoreStatusOverride(StrictModel):
    interpretation: ScoreInterpretationOverride | None = None


class ScoreConfigOverride(StrictModel):
    aql: ScoreAqlOverride | None = None
    weights: ScoreWeightsOverride | None = None
    dar: ScoreDarOverride | None = None
    zc: ScoreZcOverride | None = None
    score: ScoreStatusOverride | None = None


class CustomerScoreRequest(StrictModel):
    selection: TrendQueryRequest
    score_config_override: ScoreConfigOverride | None = None


class CustomerScoreCauseResponse(StrictModel):
    component: str
    label: str
    contribution: float
    share_of_total_penalty: float


class CustomerScoreComponentResponse(StrictModel):
    component: str
    label: str
    score: float
    weight: float
    contribution: float
    details: str


class MiaScoreDetailResponse(StrictModel):
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


class AqlCategoryScoreResponse(StrictModel):
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


class DarBreakdownResponse(StrictModel):
    group: str
    codes: list[int]
    event_count: int
    affected_minutes: float
    share_percent: float
    exposure_percent: float
    score: float
    threshold_label: str


class DarScoreResponse(StrictModel):
    event_count: int
    affected_minutes: float
    total_cavity_minutes: float
    exposure_percent: float
    aql_percent: float | None
    score: float
    weight: float
    contribution: float
    details: str
    breakdown: list[DarBreakdownResponse]


class ZcScoreResponse(StrictModel):
    target_percent: float
    actual_percent: float | None
    loss_points: float | None
    score: float
    weight: float
    contribution: float
    windows: int


class ShiftCustomerScoreResponse(StrictModel):
    shift: str
    operational_day: str
    shift_name: str
    start: str
    end: str
    total_penalty: float
    normalized_score: float
    evaluated_weight: float
    status: str
    main_causes: list[CustomerScoreCauseResponse]
    components: list[CustomerScoreComponentResponse]
    mia_details: list[MiaScoreDetailResponse]
    aql_categories: list[AqlCategoryScoreResponse]
    dar: DarScoreResponse | None
    zc: ZcScoreResponse | None
    warnings: list[str]


class ProductionCustomerScoreResponse(StrictModel):
    production_id: str
    line: str
    total_penalty: float
    normalized_score: float
    evaluated_weight: float
    status: str
    main_causes: list[CustomerScoreCauseResponse]
    components: list[CustomerScoreComponentResponse]
    mia_details: list[MiaScoreDetailResponse]
    aql_categories: list[AqlCategoryScoreResponse]
    dar: DarScoreResponse | None
    zc: ZcScoreResponse | None
    shift_scores: list[ShiftCustomerScoreResponse]
    warnings: list[str]


class CustomerScoreMetaResponse(StrictModel):
    query_time_ms: float
    config_version: int
    files_read: int
    rows_scanned: int
    effective_config: dict[str, Any]
    cache_hit: bool = False
    publication_id: str | None = None
    source: str = "legacy"
    cutoff: str | None = None


class CustomerScoreResponse(StrictModel):
    status: str
    productions: list[ProductionCustomerScoreResponse]
    warnings: list[str]
    meta: CustomerScoreMetaResponse
    pending_productions: list[dict[str, Any]] = Field(default_factory=list)
