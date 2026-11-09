from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ComplaintTreatment(StrEnum):
    RELEVANT = "relevant"
    OBSERVED = "observed"
    EXCLUDED = "excluded"


class CustomerQualityLabel(StrEnum):
    GOOD = "good"
    OBSERVED = "observed"
    BAD = "bad"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ComplaintRecord:
    production_id: str
    complaint_id: str
    defect_description: str
    customer_group: str
    treatment: ComplaintTreatment
    reason: str


@dataclass(frozen=True, slots=True)
class ProductionCustomerQuality:
    production_id: str
    label: CustomerQualityLabel
    relevant_count: int
    observed_count: int
    excluded_count: int
    worst_reason: str | None
    explanation: str
    complaints: tuple[ComplaintRecord, ...]


@dataclass(frozen=True, slots=True)
class CustomerQualityValidationRow:
    production_id: str
    customer_label: CustomerQualityLabel
    relevant_count: int
    observed_count: int
    excluded_count: int
    worst_reason: str | None
    internal_risk_flag: bool
    validation_case: str
    interpretation: str


@dataclass(frozen=True, slots=True)
class CustomerQualityAnalysis:
    rows: tuple[ProductionCustomerQuality, ...]
    validation_rows: tuple[CustomerQualityValidationRow, ...]
    warnings: tuple[str, ...]
