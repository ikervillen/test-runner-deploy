"""Per-branch inspection Parquet (AV-*_parquet/<production>/<branch>.parquet)."""

from __future__ import annotations

import polars as pl

from app.domain.models import BranchFile, TrendFilters
from app.infrastructure.parquet.schema import (
    CAVITY_COLUMN,
    DATE_COLUMN,
    DEFECT_COLUMN,
    INSPECTED_COLUMN,
    REJECTED_COLUMN,
    apply_filters,
)

from ..intervals import within_windows_expr
from ..types import Windows


def rejected_by_defect(
    branches: tuple[BranchFile, ...], filters: TrendFilters, windows: Windows | None = None
) -> pl.DataFrame:
    """Columns: defect_code, rejected (> 0 only)."""
    frame = apply_filters(_scan(branches), filters, include_defect_codes=True)
    if windows is not None:
        frame = frame.filter(within_windows_expr(DATE_COLUMN, windows))
    return (
        frame.group_by(DEFECT_COLUMN)
        .agg(pl.col(REJECTED_COLUMN).sum().cast(pl.Float64).alias("rejected"))
        .filter(pl.col("rejected") > 0)
        .select(pl.col(DEFECT_COLUMN).alias("defect_code"), "rejected")
        .collect(engine="streaming")
    )


def inspected_total(branches: tuple[BranchFile, ...], filters: TrendFilters) -> float:
    """Legacy denominator from the branch files, used when no real totals exist."""
    frame = apply_filters(_scan(branches), filters, include_defect_codes=False)
    value = (
        frame.select(pl.col(INSPECTED_COLUMN).sum().cast(pl.Float64).alias("inspected"))
        .collect(engine="streaming")
        .item(0, "inspected")
    )
    return float(value or 0.0)


def distinct_cavities(branches: tuple[BranchFile, ...], filters: TrendFilters) -> int:
    frame = apply_filters(_scan(branches), filters, include_defect_codes=False)
    return frame.select([CAVITY_COLUMN]).unique().collect(engine="streaming").height


def _scan(branches: tuple[BranchFile, ...]) -> pl.LazyFrame:
    return pl.scan_parquet([str(branch.path) for branch in branches])
