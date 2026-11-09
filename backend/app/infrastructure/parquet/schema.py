from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from app.domain.models import TrendFilters, WindowId

DATE_COLUMN = "LISTA_DEFECTOS_FECHA_REAL"
BRANCH_COLUMN = "LISTA_DEFECTOS_MAQ_IS_RAMAL"
REJECTED_COLUMN = "LISTA_DEFECTOS_RECH"
INSPECTED_COLUMN = "LISTA_DEFECTOS_INSPECC"
DEFECT_COLUMN = "LISTA_DEFECTOS_COD_DEF"
CAVITY_COLUMN = "LISTA_DEFECTOS_CAVIDAD_TOTAL"
COLOR_COLUMN = "LISTA_DEFECTOS_MODELO_COLOR"
SECTION_COLUMN = "LISTA_DEFECTOS_SECCION"
MACHINE_CODE_COLUMN = "LISTA_DEFECTOS_COD_MAQ_ZF"

FILTER_COLUMNS: Mapping[str, str] = {
    "defect_codes": DEFECT_COLUMN,
    "cavities": CAVITY_COLUMN,
    "colors": COLOR_COLUMN,
    "sections": SECTION_COLUMN,
    "machine_codes": MACHINE_CODE_COLUMN,
}

WINDOW_MINUTES: Mapping[WindowId, int] = {
    WindowId.TEN_MINUTES: 10,
    WindowId.ONE_HOUR: 60,
    WindowId.FOUR_HOURS: 240,
    WindowId.EIGHT_HOURS: 480,
}

WINDOW_MILLISECONDS: Mapping[WindowId, int] = {
    window: minutes * 60 * 1000 for window, minutes in WINDOW_MINUTES.items()
}
SHIFT_WINDOW_OFFSET_MS = 6 * 60 * 60 * 1000


def apply_filters(
    lazy_frame: pl.LazyFrame,
    filters: TrendFilters,
    *,
    include_defect_codes: bool,
) -> pl.LazyFrame:
    values_by_filter: Mapping[str, tuple[int | str, ...]] = {
        "defect_codes": filters.defect_codes,
        "cavities": filters.cavities,
        "colors": filters.colors,
        "sections": filters.sections,
        "machine_codes": filters.machine_codes,
    }
    for filter_name, values in values_by_filter.items():
        if filter_name == "defect_codes" and not include_defect_codes:
            continue
        if values:
            lazy_frame = lazy_frame.filter(pl.col(FILTER_COLUMNS[filter_name]).is_in(list(values)))
    return lazy_frame


def bucket_expression(window: WindowId, four_hour_offset_ms: int) -> pl.Expr:
    duration_ms = WINDOW_MILLISECONDS[window]
    offset_ms = _window_offset_ms(window, four_hour_offset_ms)
    epoch_ms = pl.col(DATE_COLUMN).dt.epoch(time_unit="ms")
    bucket_ms = ((epoch_ms - offset_ms) // duration_ms) * duration_ms + offset_ms
    return bucket_ms.cast(pl.Datetime("ms")).alias("_bucket")


def _window_offset_ms(window: WindowId, four_hour_offset_ms: int) -> int:
    if window is WindowId.FOUR_HOURS:
        return four_hour_offset_ms
    if window is WindowId.EIGHT_HOURS:
        return SHIFT_WINDOW_OFFSET_MS
    return 0
