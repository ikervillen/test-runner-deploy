"""Hot-end (ZC) yield observations from the published aggregate or Rendimiento_ZC.xlsx."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl
from openpyxl import load_workbook

from app.domain.score_config import as_float, as_int
from app.infrastructure.aggregates.zc import ZC_TO_LINE

from ..types import ZcObservation

MISSING_WORKBOOK = "ZC no evaluado: no se encuentra Rendimiento_ZC.xlsx."
MISSING_COLUMNS = "ZC no evaluado: columnas requeridas no disponibles."
_REQUIRED = ("FECHA", "ID_LINEA_ZC", "RUEDA", "TEORICO_A", "TEORICO_B")


def from_aggregate(frame: pl.DataFrame) -> tuple[ZcObservation, ...]:
    return tuple(
        ZcObservation(timestamp, line, float(rueda), float(theoretical))
        for timestamp, line, rueda, theoretical in frame.select(
            ["timestamp", "line", "rueda", "theoretical"]
        ).iter_rows()
        if isinstance(timestamp, datetime)
        and isinstance(line, str)
        and rueda is not None
        and theoretical is not None
        and float(theoretical) > 0
    )


def from_workbook(path: Path) -> tuple[tuple[ZcObservation, ...], str | None]:
    """Observations and an operator-facing error when the sheet is unusable."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.worksheets[0]
        headers = tuple(
            str(value).strip().upper() if value is not None else ""
            for value in next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True))
        )
        index = {header: position for position, header in enumerate(headers)}
        if any(column not in index for column in _REQUIRED):
            return (), MISSING_COLUMNS
        observations: list[ZcObservation] = []
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            timestamp = row[index["FECHA"]]
            zc_line = as_int(row[index["ID_LINEA_ZC"]])
            rueda = as_float(row[index["RUEDA"]])
            theoretical = (as_float(row[index["TEORICO_A"]]) or 0.0) + (
                as_float(row[index["TEORICO_B"]]) or 0.0
            )
            line = ZC_TO_LINE.get(zc_line) if zc_line is not None else None
            if (
                isinstance(timestamp, datetime)
                and line is not None
                and rueda is not None
                and theoretical > 0
            ):
                observations.append(ZcObservation(timestamp, line, rueda, theoretical))
        return tuple(observations), None
    finally:
        workbook.close()
