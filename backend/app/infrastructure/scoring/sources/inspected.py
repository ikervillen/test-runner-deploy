"""Real inspected totals per production (inspeccionadas_totales_parquet)."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from app.domain.models import BranchFile, Production, TrendRequest

from ..intervals import within_windows_expr
from ..types import Windows


class InspectedTotalsSource:
    def __init__(self, root: Path) -> None:
        self._root = root

    def production_total(
        self, production: Production, request: TrendRequest, branches: tuple[BranchFile, ...]
    ) -> tuple[float, int, int]:
        """(inspected units, files read, rows scanned); zeros when the file is missing."""
        frame = self._frame(production, request, branches)
        if frame is None:
            return 0.0, 0, 0
        aggregated = (
            _dedupe(frame)
            .select(
                pl.col("inspected").sum().cast(pl.Float64).alias("inspected"),
                pl.len().alias("rows"),
            )
            .collect(engine="streaming")
        )
        return (
            float(aggregated.item(0, "inspected") or 0.0),
            1,
            int(aggregated.item(0, "rows") or 0),
        )

    def shift_total(
        self,
        production: Production,
        request: TrendRequest,
        branches: tuple[BranchFile, ...],
        windows: Windows,
    ) -> float:
        frame = self._frame(production, request, branches)
        if frame is None:
            return 0.0
        value = (
            _dedupe(frame.filter(within_windows_expr("timestamp", windows)))
            .select(pl.col("inspected").sum().cast(pl.Float64).alias("inspected"))
            .collect(engine="streaming")
            .item(0, "inspected")
        )
        return float(value or 0.0)

    def _frame(
        self, production: Production, request: TrendRequest, branches: tuple[BranchFile, ...]
    ) -> pl.LazyFrame | None:
        path = self._root / production.line / f"{production.id}.parquet"
        if not path.exists():
            return None
        frame = pl.scan_parquet(path)
        selected_branches = {branch.branch for branch in branches}
        if selected_branches:
            frame = frame.filter(pl.col("machine_branch").is_in(sorted(selected_branches)))
        filters = request.filters
        if filters.colors:
            frame = frame.filter(pl.col("model_color").is_in(list(filters.colors)))
        if filters.cavities:
            cavities = list(filters.cavities)
            frame = frame.filter(
                pl.col("cavity").is_in(cavities) | pl.col("cavity_total").is_in(cavities)
            )
        if filters.sections:
            frame = frame.filter(pl.col("section").is_in(list(filters.sections)))
        if filters.machine_codes:
            frame = frame.filter(pl.col("zf_machine_code").is_in(list(filters.machine_codes)))
        return frame


def _dedupe(frame: pl.LazyFrame) -> pl.LazyFrame:
    """One inspected total per (timestamp, branch): re-exports repeat the same reading."""
    return frame.group_by(["timestamp", "machine_branch"]).agg(
        pl.col("inspected_total").max().alias("inspected")
    )
