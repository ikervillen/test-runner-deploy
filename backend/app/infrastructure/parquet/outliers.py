from __future__ import annotations

import polars as pl

from app.infrastructure.parquet.schema import (
    BRANCH_COLUMN,
    DATE_COLUMN,
    REJECTED_COLUMN,
)

OUTLIER_BUCKET_COLUMN = "_quality_bucket"
OUTLIER_TOTAL_COLUMN = "_quality_rejected"


def filter_rejection_outlier_windows(
    source: pl.LazyFrame,
    *,
    multiplier: float,
    minimum_windows: int,
) -> pl.LazyFrame:
    """Remove raw rows belonging to implausible ten-minute rejection windows."""
    prepared = source.with_columns(
        pl.col(DATE_COLUMN).dt.truncate("10m").alias(OUTLIER_BUCKET_COLUMN)
    )
    totals = prepared.group_by([BRANCH_COLUMN, OUTLIER_BUCKET_COLUMN]).agg(
        pl.col(REJECTED_COLUMN).sum().cast(pl.Float64).alias(OUTLIER_TOTAL_COLUMN)
    )
    references = totals.group_by(BRANCH_COLUMN).agg(
        pl.col(OUTLIER_TOTAL_COLUMN).median().alias("_quality_median"),
        pl.len().alias("_quality_window_count"),
    )
    valid_windows = (
        totals.join(references, on=BRANCH_COLUMN)
        .filter(
            ~(
                (pl.col("_quality_window_count") >= minimum_windows)
                & (pl.col("_quality_median") > 0)
                & (pl.col(OUTLIER_TOTAL_COLUMN) > pl.col("_quality_median") * multiplier)
            )
        )
        .select([BRANCH_COLUMN, OUTLIER_BUCKET_COLUMN])
    )
    return prepared.join(
        valid_windows,
        on=[BRANCH_COLUMN, OUTLIER_BUCKET_COLUMN],
        how="inner",
    ).drop(OUTLIER_BUCKET_COLUMN)
