from __future__ import annotations

from datetime import date

from app.domain.customer_quality import CustomerQualityLabel
from app.domain.models import Production
from app.infrastructure.repositories.customer_quality import StaticCustomerQualityRepository


def test_customer_quality_marks_multiple_product_complaints_as_critical() -> None:
    result = StaticCustomerQualityRepository().analyze((_production("AV32_PROD_005"),))

    row = result.rows[0]
    assert row.label == CustomerQualityLabel.CRITICAL
    assert row.relevant_count == 4
    assert row.observed_count == 0
    assert row.excluded_count == 0


def test_customer_quality_keeps_specification_claim_as_observed() -> None:
    result = StaticCustomerQualityRepository().analyze((_production("AV32_PROD_006"),))

    row = result.rows[0]
    assert row.label == CustomerQualityLabel.OBSERVED
    assert row.relevant_count == 0
    assert row.observed_count == 1


def test_customer_quality_excludes_wrong_label_from_manufacturing_quality() -> None:
    result = StaticCustomerQualityRepository().analyze((_production("AV23_PROD_002"),))

    row = result.rows[0]
    assert row.label == CustomerQualityLabel.GOOD
    assert row.relevant_count == 0
    assert row.excluded_count == 1


def _production(production_id: str) -> Production:
    line = production_id.split("_", maxsplit=2)[0]
    return Production(
        id=production_id,
        line=line,
        start_day=date(2024, 1, 1),
        end_day=date(2024, 1, 2),
        branches=(),
    )
