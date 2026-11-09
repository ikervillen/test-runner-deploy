from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path


class MetricId(StrEnum):
    REJECTED_COUNT = "rejected_count"
    INSPECTED_COUNT = "inspected_count"
    REJECTION_RATE = "rejection_rate"


class WindowId(StrEnum):
    TEN_MINUTES = "10m"
    ONE_HOUR = "1h"
    FOUR_HOURS = "4h"
    EIGHT_HOURS = "8h"


class ComparisonLevel(StrEnum):
    PRODUCTION = "production"
    BRANCH = "branch"


class MultipleTestingMethod(StrEnum):
    BENJAMINI_HOCHBERG = "benjamini_hochberg"
    HOLM = "holm"


class AlignmentMode(StrEnum):
    CALENDAR = "calendar"
    RELATIVE = "relative"


class PlotId(StrEnum):
    RANKING = "ranking"
    DAILY_BOXPLOT = "daily_boxplot"
    DAILY_VIOLIN = "daily_violin"
    RELATIVE_SERIES = "relative_series"
    PARETO = "pareto"
    HEATMAP = "heatmap"
    FOREST_PLOT = "forest_plot"
    PAIRWISE_MATRIX = "pairwise_matrix"


@dataclass(frozen=True, slots=True)
class BranchFile:
    branch: str
    path: Path
    rows: int
    data_start: datetime
    data_end: datetime


@dataclass(frozen=True, slots=True)
class Production:
    id: str
    line: str
    start_day: date
    end_day: date
    branches: tuple[BranchFile, ...]
    bottle_model_id: str | None = None
    segment_id: str | None = None
    dar_profile_id: str | None = None


@dataclass(frozen=True, slots=True)
class TrendFilters:
    defect_codes: tuple[int, ...] = ()
    cavities: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    machine_codes: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class TrendRequest:
    production_ids: tuple[str, ...]
    branches: tuple[str, ...]
    metric: MetricId
    window: WindowId
    filters: TrendFilters


@dataclass(frozen=True, slots=True)
class TrendPoint:
    timestamp: datetime
    value: float


@dataclass(frozen=True, slots=True)
class TrendSeries:
    branch: str
    points: tuple[TrendPoint, ...]


@dataclass(frozen=True, slots=True)
class ProductionTrend:
    production_id: str
    line: str
    start: datetime
    end: datetime
    series: tuple[TrendSeries, ...]


@dataclass(frozen=True, slots=True)
class QueryStats:
    files_read: int
    rows_scanned: int


@dataclass(frozen=True, slots=True)
class TrendResult:
    productions: tuple[ProductionTrend, ...]
    stats: QueryStats


@dataclass(frozen=True, slots=True)
class AvailableFilters:
    defect_codes: tuple[int, ...]
    cavities: tuple[str, ...]
    colors: tuple[str, ...]
    sections: tuple[str, ...]
    machine_codes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class StatisticalOptions:
    comparison_level: ComparisonLevel
    reference_production_id: str | None
    confidence_level: float
    multiple_testing_method: MultipleTestingMethod
    minimum_relevant_difference: float
    industrial_threshold: float | None
    alignment: AlignmentMode
    bootstrap_samples: int
    bootstrap_seed: int
    requested_plots: tuple[PlotId, ...]


@dataclass(frozen=True, slots=True)
class StatisticalRequest:
    trend: TrendRequest
    options: StatisticalOptions


@dataclass(frozen=True, slots=True)
class StatisticalPoint:
    production_id: str
    line: str
    branch: str
    timestamp: datetime
    elapsed_minutes: float
    rejected_count: float
    nominal_exposure: float
    proxy_rate_per_1000: float


@dataclass(frozen=True, slots=True)
class DefectStatisticalPoint:
    production_id: str
    timestamp: datetime
    defect_code: int
    rejected_count: float


@dataclass(frozen=True, slots=True)
class ContributorCount:
    production_id: str
    dimension: str
    category: str
    rejected_count: float
    label: str | None = None


@dataclass(frozen=True, slots=True)
class StatisticalDataset:
    points: tuple[StatisticalPoint, ...]
    contributors: tuple[ContributorCount, ...]
    files_read: int
    rows_scanned: int
    defect_points: tuple[DefectStatisticalPoint, ...] = ()


@dataclass(frozen=True, slots=True)
class DescriptiveResult:
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


@dataclass(frozen=True, slots=True)
class StatisticalTestResult:
    name: str
    statistic: float
    p_value: float
    interpretation: str


@dataclass(frozen=True, slots=True)
class PairwiseResult:
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


@dataclass(frozen=True, slots=True)
class StabilityResult:
    production_id: str
    worst_branch: str
    branch_rate_spread: float
    branch_rate_standard_deviation: float
    branch_rates: dict[str, float]


@dataclass(frozen=True, slots=True)
class ControlResult:
    entity_id: str
    center_line: float
    signal_count: int
    out_of_control_ratio: float
    cusum_signal_count: int
    ewma_signal_count: int
    lag1_autocorrelation: float | None
    durbin_watson: float | None


@dataclass(frozen=True, slots=True)
class ContributorResult:
    production_id: str
    dimension: str
    category: str
    rejected_count: float
    share: float
    label: str | None = None


@dataclass(frozen=True, slots=True)
class QualityDimension:
    dimension: str
    value: float
    unit: str
    comparison_percentile: float | None
    interpretation: str


@dataclass(frozen=True, slots=True)
class QualityIndicator:
    indicator: str
    label: str
    value: float
    unit: str
    direction: str
    comparison_percentile: float | None
    score: float | None
    interpretation: str


@dataclass(frozen=True, slots=True)
class QualitySubscore:
    dimension: str
    label: str
    score: float | None
    indicators: tuple[QualityIndicator, ...]


@dataclass(frozen=True, slots=True)
class QualityScorecard:
    overall_score: float | None
    classification: str
    is_comparative: bool
    comparison_productions: int
    methodology: str
    subscores: tuple[QualitySubscore, ...]
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualitySignalPoint:
    timestamp: datetime
    observed: float
    baseline: float
    ewma: float
    robust_z_score: float
    cusum_positive: float
    anomalous: bool


@dataclass(frozen=True, slots=True)
class QualityEpisode:
    start: datetime
    end: datetime
    windows: int
    duration_minutes: int
    maximum: float
    area_above_baseline: float


@dataclass(frozen=True, slots=True)
class QualityChangePoint:
    timestamp: datetime
    before_median: float
    after_median: float
    absolute_change: float
    relative_change: float | None


@dataclass(frozen=True, slots=True)
class ExplanatoryFactor:
    factor: str
    rate_ratio: float
    confidence_low: float
    confidence_high: float
    p_value: float
    interpretation: str


@dataclass(frozen=True, slots=True)
class ExplanatoryModel:
    family: str
    observations: int
    pseudo_r_squared: float | None
    overdispersion: float | None
    factors: tuple[ExplanatoryFactor, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductionQualityProfile:
    production_id: str
    comparison_productions: int
    dimensions: tuple[QualityDimension, ...]
    signal_series: tuple[QualitySignalPoint, ...]
    episodes: tuple[QualityEpisode, ...]
    change_points: tuple[QualityChangePoint, ...]
    branch_rates: dict[str, float]
    explanatory_model: ExplanatoryModel
    scorecard: QualityScorecard
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StatisticalAnalysis:
    descriptive: tuple[DescriptiveResult, ...]
    global_tests: tuple[StatisticalTestResult, ...]
    pairwise: tuple[PairwiseResult, ...]
    stability: tuple[StabilityResult, ...]
    control: tuple[ControlResult, ...]
    contributors: tuple[ContributorResult, ...]
    quality_profiles: tuple[ProductionQualityProfile, ...]
    shift_analysis: ShiftAnalysis
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShiftScheduleItem:
    name: str
    start_hour: int
    end_hour: int


@dataclass(frozen=True, slots=True)
class ShiftMetric:
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


@dataclass(frozen=True, slots=True)
class ShiftDayCell:
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


@dataclass(frozen=True, slots=True)
class ShiftDefectContribution:
    production_id: str
    shift: str
    defect_code: int
    rejected_count: float
    share: float
    lift_vs_production: float | None


@dataclass(frozen=True, slots=True)
class ShiftStatisticalTest:
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


@dataclass(frozen=True, slots=True)
class ShiftComparison:
    shift: str
    production_id: str
    median_delta_vs_production: float
    p95_delta_vs_production: float
    anomalous_delta_vs_production: float
    rank_within_shift: int


@dataclass(frozen=True, slots=True)
class ShiftAnalysis:
    schedule: tuple[ShiftScheduleItem, ...]
    production_shift_metrics: tuple[ShiftMetric, ...]
    branch_shift_metrics: tuple[ShiftMetric, ...]
    day_shift_matrix: tuple[ShiftDayCell, ...]
    defect_contributions: tuple[ShiftDefectContribution, ...]
    same_shift_comparisons: tuple[ShiftComparison, ...]
    statistical_tests: tuple[ShiftStatisticalTest, ...]
    findings: tuple[str, ...]
    warnings: tuple[str, ...]
