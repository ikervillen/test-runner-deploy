"""Alert/rejection events (avisos_rechazos) overlapping a production interval."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

from app.domain.models import Production, TrendRequest
from app.infrastructure.aggregates.alerts import AlertAggregateResolver

EVENT_COLUMNS = ["defect_code", "defect_description", "cavity", "_event_start", "_event_end"]
CLIPPED_COLUMNS = [
    "defect_code",
    "defect_description",
    "cavity",
    "_clipped_start",
    "_clipped_end",
    "_duration_minutes",
]


class AlertEventSource:
    def __init__(self, alerts_root: Path, aggregates: AlertAggregateResolver | None) -> None:
        self._alerts_root = alerts_root
        self._aggregates = aggregates

    def existing_paths(self, production: Production) -> tuple[Path, ...]:
        """Published aggregate if available, else one daily file per production day."""
        if self._aggregates is not None:
            aggregate_paths = self._aggregates.paths_for(production)
            if aggregate_paths is not None:
                return tuple(path for path in aggregate_paths if path.exists())
        return tuple(path for path in _daily_paths(self._alerts_root, production) if path.exists())

    @staticmethod
    def empty() -> pl.DataFrame:
        """Typed empty frame so downstream filters on event columns never fail."""
        return pl.DataFrame(
            schema={
                "defect_code": pl.Int64,
                "defect_description": pl.Utf8,
                "cavity": pl.Utf8,
                "_event_start": pl.Datetime("us"),
                "_event_end": pl.Datetime("us"),
            }
        )

    @staticmethod
    def overlapping(
        paths: tuple[Path, ...], request: TrendRequest, start: datetime, end: datetime
    ) -> pl.DataFrame:
        """Events touching [start, end] with their original bounds."""
        return (
            _overlapping(paths, request, start, end)
            .select(EVENT_COLUMNS)
            .collect(engine="streaming")
        )

    @staticmethod
    def clipped(
        paths: tuple[Path, ...], request: TrendRequest, start: datetime, end: datetime
    ) -> pl.DataFrame:
        """Events clipped to [start, end] with their clipped duration in minutes."""
        return (
            _overlapping(paths, request, start, end)
            .with_columns(
                pl.max_horizontal(pl.col("_event_start"), pl.lit(start)).alias("_clipped_start"),
                pl.min_horizontal(pl.col("_event_end"), pl.lit(end)).alias("_clipped_end"),
            )
            .filter(pl.col("_clipped_end") > pl.col("_clipped_start"))
            .with_columns(
                (
                    (
                        pl.col("_clipped_end").dt.epoch(time_unit="ms")
                        - pl.col("_clipped_start").dt.epoch(time_unit="ms")
                    )
                    / 60000.0
                ).alias("_duration_minutes")
            )
            .select(CLIPPED_COLUMNS)
            .collect(engine="streaming")
        )


def _overlapping(
    paths: tuple[Path, ...], request: TrendRequest, start: datetime, end: datetime
) -> pl.LazyFrame:
    frame = pl.scan_parquet([str(path) for path in paths])
    columns = frame.collect_schema().names()
    if "event_end" not in columns:
        frame = frame.with_columns(pl.col("event_start").alias("event_end"))
    if "cavity" not in columns:
        frame = frame.with_columns(pl.lit("-").alias("cavity"))
    if "defect_description" not in columns:
        frame = frame.with_columns(pl.lit("").alias("defect_description"))
    frame = frame.with_columns(
        pl.col("event_start").alias("_event_start"),
        pl.coalesce(pl.col("event_end"), pl.col("event_start")).alias("_event_end"),
    ).filter((pl.col("_event_start") <= end) & (pl.col("_event_end") >= start))
    filters = request.filters
    if filters.defect_codes:
        frame = frame.filter(pl.col("defect_code").is_in(list(filters.defect_codes)))
    if filters.cavities:
        frame = frame.filter(pl.col("cavity").is_in(list(filters.cavities)))
    if filters.colors:
        frame = frame.filter(pl.col("color").is_in(list(filters.colors)))
    return frame


def _daily_paths(alerts_root: Path, production: Production) -> tuple[Path, ...]:
    paths: list[Path] = []
    current = production.start_day
    while current <= production.end_day:
        paths.append(alerts_root / production.line / f"{current.isoformat()}.parquet")
        current += timedelta(days=1)
    return tuple(paths)
