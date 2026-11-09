from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import (
    AlignmentMode,
    ComparisonLevel,
    MetricId,
    MultipleTestingMethod,
    PlotId,
    WindowId,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    status: str
    data_root_available: bool


class ReadinessResponse(StrictModel):
    status: str
    data_version: str | None
    missing_resources: list[str]


class BranchCatalog(StrictModel):
    id: str
    rows: int
    data_start: datetime
    data_end: datetime


class ProductionCatalog(StrictModel):
    id: str
    line: str
    start_day: date
    end_day: date
    branches: list[BranchCatalog]
    bottle_model_id: str | None = None
    segment_id: str | None = None
    dar_profile_id: str | None = None


class OptionCatalog(StrictModel):
    id: str
    label: str


class FilterOptions(StrictModel):
    defect_codes: list[int]
    cavities: list[str]
    colors: list[str]
    sections: list[str]
    machine_codes: list[int]


class CatalogResponse(StrictModel):
    lines: list[str]
    productions: list[ProductionCatalog]
    metrics: list[OptionCatalog]
    windows: list[OptionCatalog]
    filters: FilterOptions
    max_productions_per_query: int
    source: str = "legacy"
    publication_id: str | None = None


class TrendFilterRequest(StrictModel):
    defect_codes: list[int] = Field(default_factory=list)
    cavities: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    machine_codes: list[int] = Field(default_factory=list)


class TrendQueryRequest(StrictModel):
    production_ids: Annotated[list[str], Field(min_length=1)]
    branches: list[str] = Field(default_factory=list)
    metric: MetricId
    window: WindowId
    filters: TrendFilterRequest = Field(default_factory=TrendFilterRequest)


class TrendPointResponse(StrictModel):
    timestamp: datetime
    value: float


class TrendSeriesResponse(StrictModel):
    branch: str
    points: list[TrendPointResponse]


class ProductionTrendResponse(StrictModel):
    production_id: str
    line: str
    start: datetime
    end: datetime
    series: list[TrendSeriesResponse]


class QueryEcho(StrictModel):
    metric: MetricId
    window: WindowId


class QueryMeta(StrictModel):
    query_time_ms: float
    files_read: int
    rows_scanned: int
    cache_hit: bool


class TrendQueryResponse(StrictModel):
    query: QueryEcho
    productions: list[ProductionTrendResponse]
    meta: QueryMeta


class AlertEventMarkerResponse(StrictModel):
    timestamp: datetime
    end_timestamp: datetime
    event_type: str
    defect_code: int | None
    defect_description: str
    marker_group: str


class ProductionAlertEventsResponse(StrictModel):
    production_id: str
    line: str
    start: datetime
    end: datetime
    events: list[AlertEventMarkerResponse]


class AlertEventsResponse(StrictModel):
    productions: list[ProductionAlertEventsResponse]
    meta: QueryMeta


class StatisticalOptionsRequest(StrictModel):
    comparison_level: ComparisonLevel = ComparisonLevel.PRODUCTION
    reference_production_id: str | None = None
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1)
    multiple_testing_method: MultipleTestingMethod = MultipleTestingMethod.BENJAMINI_HOCHBERG
    minimum_relevant_difference: float = Field(default=0.0, ge=0)
    industrial_threshold: float | None = Field(default=None, ge=0)
    alignment: AlignmentMode = AlignmentMode.RELATIVE
    bootstrap_samples: int = Field(default=10_000, ge=1_000, le=50_000)
    bootstrap_seed: int = 4_835_242
    requested_plots: list[PlotId] = Field(
        default_factory=lambda: [
            PlotId.RANKING,
            PlotId.DAILY_BOXPLOT,
            PlotId.DAILY_VIOLIN,
            PlotId.RELATIVE_SERIES,
            PlotId.PARETO,
            PlotId.HEATMAP,
            PlotId.FOREST_PLOT,
        ]
    )


class StatisticalQueryRequest(StrictModel):
    selection: TrendQueryRequest
    options: StatisticalOptionsRequest = Field(default_factory=StatisticalOptionsRequest)


class StatisticsCatalogResponse(StrictModel):
    plots: list[OptionCatalog]
    confidence_levels: list[float]
    multiple_testing_methods: list[OptionCatalog]
    assumed_line_rate_per_minute: float
    exposure_source: str
    exposure_definition_version: str


class ExposureResponse(StrictModel):
    source: str
    assumed_line_rate_per_minute: float
    window_minutes: int
    nominal_line_exposure: float
    branch_allocation: str
    is_measured: bool


class StatisticalSummaryResponse(StrictModel):
    worst_entity_id: str | None
    ranking_basis: str
    headline: str


class DescriptiveStatisticsResponse(StrictModel):
    entity_id: str
    production_id: str
    line: str
    branch: str | None
    rejected_count: float
    nominal_exposure: float
    global_proxy_rate: float
    confidence_low: float
    confidence_high: float
    valid_windows: int
    coverage_ratio: float
    mean: float
    median: float
    minimum: float
    maximum: float
    p10: float
    p25: float
    p75: float
    p90: float
    p95: float
    iqr: float
    standard_deviation: float
    mad: float
    skewness: float | None
    kurtosis: float | None
    above_threshold_ratio: float | None
    maximum_threshold_run: int | None
    theil_sen_slope: float
    trend: str


class StatisticalTestResponse(StrictModel):
    name: str
    statistic: float
    p_value: float
    interpretation: str


class PairwiseComparisonResponse(StrictModel):
    entity_a: str
    entity_b: str
    rate_a: float
    rate_b: float
    difference: float
    difference_ci_low: float
    difference_ci_high: float
    rate_ratio: float | None
    rate_ratio_ci_low: float | None
    rate_ratio_ci_high: float | None
    mann_whitney_u: float
    p_value: float
    adjusted_p_value: float
    significant: bool
    cliffs_delta: float
    hedges_g: float
    kolmogorov_smirnov_statistic: float
    kolmogorov_smirnov_p_value: float
    effect_magnitude: str
    practically_relevant: bool


class StabilityResponse(StrictModel):
    production_id: str
    worst_branch: str
    branch_rate_spread: float
    branch_rate_standard_deviation: float
    branch_rates: dict[str, float]


class ControlSignalResponse(StrictModel):
    entity_id: str
    center_line: float
    signal_count: int
    out_of_control_ratio: float
    cusum_signal_count: int
    ewma_signal_count: int
    lag1_autocorrelation: float | None
    durbin_watson: float | None


class ContributorResponse(StrictModel):
    production_id: str
    dimension: str
    category: str
    rejected_count: float
    share: float
    label: str | None = None


class QualityDimensionResponse(StrictModel):
    dimension: str
    value: float
    unit: str
    comparison_percentile: float | None
    interpretation: str


class QualityIndicatorResponse(StrictModel):
    indicator: str
    label: str
    value: float
    unit: str
    direction: str
    comparison_percentile: float | None
    score: float | None
    interpretation: str


class QualitySubscoreResponse(StrictModel):
    dimension: str
    label: str
    score: float | None
    indicators: list[QualityIndicatorResponse]


class QualityScorecardResponse(StrictModel):
    overall_score: float | None
    classification: str
    is_comparative: bool
    comparison_productions: int
    methodology: str
    subscores: list[QualitySubscoreResponse]
    strengths: list[str]
    weaknesses: list[str]


class QualitySignalPointResponse(StrictModel):
    timestamp: datetime
    observed: float
    baseline: float
    ewma: float
    robust_z_score: float
    cusum_positive: float
    anomalous: bool


class QualityEpisodeResponse(StrictModel):
    start: datetime
    end: datetime
    windows: int
    duration_minutes: int
    maximum: float
    area_above_baseline: float


class QualityChangePointResponse(StrictModel):
    timestamp: datetime
    before_median: float
    after_median: float
    absolute_change: float
    relative_change: float | None


class ExplanatoryFactorResponse(StrictModel):
    factor: str
    rate_ratio: float
    confidence_low: float
    confidence_high: float
    p_value: float
    interpretation: str


class ExplanatoryModelResponse(StrictModel):
    family: str
    observations: int
    pseudo_r_squared: float | None
    overdispersion: float | None
    factors: list[ExplanatoryFactorResponse]
    warnings: list[str]


class ProductionQualityProfileResponse(StrictModel):
    production_id: str
    comparison_productions: int
    dimensions: list[QualityDimensionResponse]
    signal_series: list[QualitySignalPointResponse]
    episodes: list[QualityEpisodeResponse]
    change_points: list[QualityChangePointResponse]
    branch_rates: dict[str, float]
    explanatory_model: ExplanatoryModelResponse
    scorecard: QualityScorecardResponse
    warnings: list[str]


class ShiftScheduleItemResponse(StrictModel):
    name: str
    start_hour: int
    end_hour: int


class ShiftMetricResponse(StrictModel):
    production_id: str
    shift: str
    branch: str | None
    windows: int
    expected_windows: int
    coverage_ratio: float
    rejected_count: float
    nominal_exposure: float
    proxy_rate: float
    mean: float
    median: float
    median_ci_low: float
    median_ci_high: float
    p90: float
    p95: float
    mad: float
    iqr: float
    trend_slope: float
    anomalous_ratio: float
    status: str


class ShiftDayCellResponse(StrictModel):
    production_id: str
    operational_day: date
    shift: str
    windows: int
    expected_windows: int
    coverage_ratio: float
    median: float
    p95: float
    proxy_rate: float
    status: str


class ShiftDefectContributionResponse(StrictModel):
    production_id: str
    shift: str
    defect_code: int
    rejected_count: float
    share: float
    lift_vs_production: float | None


class ShiftStatisticalTestResponse(StrictModel):
    scope: str
    target: str
    test_name: str
    statistic: float | None
    p_value: float | None
    adjusted_p_value: float | None
    effect_size: float | None
    effect_label: str
    interpretation: str
    explanation: str


class ShiftComparisonResponse(StrictModel):
    shift: str
    production_id: str
    median_delta_vs_production: float
    p95_delta_vs_production: float
    anomalous_delta_vs_production: float
    rank_within_shift: int


class ShiftAnalysisResponse(StrictModel):
    schedule: list[ShiftScheduleItemResponse]
    production_shift_metrics: list[ShiftMetricResponse]
    branch_shift_metrics: list[ShiftMetricResponse]
    day_shift_matrix: list[ShiftDayCellResponse]
    defect_contributions: list[ShiftDefectContributionResponse]
    same_shift_comparisons: list[ShiftComparisonResponse]
    statistical_tests: list[ShiftStatisticalTestResponse]
    findings: list[str]
    warnings: list[str]


class PlotAssetResponse(StrictModel):
    id: PlotId
    kind: str
    url: str
    title: str


class StatisticalMetaResponse(StrictModel):
    query_time_ms: float
    files_read: int
    rows_scanned: int
    cache_hit: bool
    bootstrap_seed: int
    exposure_definition_version: str


class StatisticalQueryResponse(StrictModel):
    analysis_id: str
    status: str
    definition_version: str
    selection: TrendQueryRequest
    exposure: ExposureResponse
    summary: StatisticalSummaryResponse
    descriptive: list[DescriptiveStatisticsResponse]
    global_tests: list[StatisticalTestResponse]
    pairwise_comparisons: list[PairwiseComparisonResponse]
    stability: list[StabilityResponse]
    control_signals: list[ControlSignalResponse]
    contributors: list[ContributorResponse]
    quality_profiles: list[ProductionQualityProfileResponse]
    shift_analysis: ShiftAnalysisResponse
    plot_assets: list[PlotAssetResponse]
    warnings: list[str]
    meta: StatisticalMetaResponse
