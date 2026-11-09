from __future__ import annotations

import polars as pl

from app.domain.models import AvailableFilters, Production
from app.infrastructure.parquet.schema import FILTER_COLUMNS


class ParquetMetadataRepository:
    def __init__(self) -> None:
        self._cached_paths: tuple[str, ...] = ()
        self._cached_filters: AvailableFilters | None = None

    def available_filters(self, productions: tuple[Production, ...]) -> AvailableFilters:
        paths = tuple(
            sorted(str(branch.path) for production in productions for branch in production.branches)
        )
        if paths != self._cached_paths or self._cached_filters is None:
            frame = (
                pl.scan_parquet(list(paths))
                .select(list(FILTER_COLUMNS.values()))
                .collect(engine="streaming")
            )
            self._cached_filters = AvailableFilters(
                defect_codes=self._integers(frame, FILTER_COLUMNS["defect_codes"]),
                cavities=self._strings(frame, FILTER_COLUMNS["cavities"]),
                colors=self._strings(frame, FILTER_COLUMNS["colors"]),
                sections=self._strings(frame, FILTER_COLUMNS["sections"]),
                machine_codes=self._integers(frame, FILTER_COLUMNS["machine_codes"]),
            )
            self._cached_paths = paths
        return self._cached_filters

    @staticmethod
    def _integers(frame: pl.DataFrame, column: str) -> tuple[int, ...]:
        values = frame.get_column(column).drop_nulls().unique().sort().to_list()
        return tuple(int(value) for value in values)

    @staticmethod
    def _strings(frame: pl.DataFrame, column: str) -> tuple[str, ...]:
        values = frame.get_column(column).drop_nulls().unique().sort().to_list()
        return tuple(str(value) for value in values)
