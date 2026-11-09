from __future__ import annotations

from app.schemas.api import StrictModel, TrendQueryRequest


class CustomerQualityRequest(StrictModel):
    selection: TrendQueryRequest


class ComplaintResponse(StrictModel):
    production_id: str
    complaint_id: str
    defect_description: str
    customer_group: str
    treatment: str
    reason: str


class ProductionCustomerQualityResponse(StrictModel):
    production_id: str
    label: str
    relevant_count: int
    observed_count: int
    excluded_count: int
    worst_reason: str | None
    explanation: str
    complaints: list[ComplaintResponse]


class CustomerQualityValidationRowResponse(StrictModel):
    production_id: str
    customer_label: str
    relevant_count: int
    observed_count: int
    excluded_count: int
    worst_reason: str | None
    internal_risk_flag: bool
    validation_case: str
    interpretation: str


class CustomerQualityResponse(StrictModel):
    status: str
    rows: list[ProductionCustomerQualityResponse]
    validation_rows: list[CustomerQualityValidationRowResponse]
    warnings: list[str]
    meta: dict[str, float | int | str]
