from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

from app.infrastructure.parquet.outliers import filter_rejection_outlier_windows
from app.infrastructure.parquet.schema import (
    BRANCH_COLUMN,
    DATE_COLUMN,
    REJECTED_COLUMN,
)


def test_rejection_outlier_filter_removes_tenfold_window() -> None:
    start = datetime(2024, 2, 13, 7)
    frame = pl.DataFrame(
        {
            DATE_COLUMN: [start + timedelta(minutes=10 * index) for index in range(21)],
            BRANCH_COLUMN: ["AV23-2"] * 21,
            REJECTED_COLUMN: [50] * 20 + [501],
        }
    )

    filtered = filter_rejection_outlier_windows(
        frame.lazy(),
        multiplier=10,
        minimum_windows=20,
    ).collect()

    assert filtered.height == 20
    assert filtered[REJECTED_COLUMN].max() == 50


def test_rejection_outlier_filter_preserves_short_series() -> None:
    start = datetime(2024, 2, 13, 7)
    frame = pl.DataFrame(
        {
            DATE_COLUMN: [start + timedelta(minutes=10 * index) for index in range(5)],
            BRANCH_COLUMN: ["AV23-2"] * 5,
            REJECTED_COLUMN: [10, 11, 9, 12, 500],
        }
    )

    filtered = filter_rejection_outlier_windows(
        frame.lazy(),
        multiplier=10,
        minimum_windows=20,
    ).collect()

    assert filtered.height == 5
    assert filtered[REJECTED_COLUMN].max() == 500
