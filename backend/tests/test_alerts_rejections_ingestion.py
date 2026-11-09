from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import polars as pl
from openpyxl import Workbook

from app.infrastructure.ingestion.alerts_rejections import (
    SOURCE_COLUMNS,
    partition_alerts_rejections,
)


def test_partitions_by_machine_and_start_date_and_deduplicates(tmp_path: Path) -> None:
    first = tmp_path / "Informe Avisos Rechazos1.xlsx"
    second = tmp_path / "Informe Avisos Rechazos2.xlsx"
    duplicate = _source_row(line=23, start=datetime(2024, 2, 13, 8, 15))
    _write_report(first, [duplicate, _source_row(line=32, start=datetime(2024, 2, 13, 9, 0))])
    _write_report(second, [duplicate, _source_row(line=23, start=datetime(2024, 2, 14, 7, 0))])

    output = tmp_path / "avisos_rechazos_parquet"
    result = partition_alerts_rejections([first, second], output)

    assert result.source_rows == 4
    assert result.unique_rows == 3
    assert result.duplicate_rows_removed == 1
    assert result.partition_count == 3

    day = pl.read_parquet(output / "AV23" / "2024-02-13.parquet")
    assert day.height == 1
    assert day.item(0, "machine") == "AV23"
    assert day.item(0, "defect_code") == 401
    assert day.item(0, "source_files").to_list() == [first.name, second.name]

    with (output / "manifest.csv").open(encoding="utf-8", newline="") as file:
        manifest = list(csv.DictReader(file))
    assert len(manifest) == 3

    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["defect_correlation"]["status"] == "unresolved"


def test_accepts_whitespace_in_optional_numeric_cells(tmp_path: Path) -> None:
    report = tmp_path / "Informe Avisos Rechazos.xlsx"
    row = _source_row(line=23, start=datetime(2024, 2, 13, 8, 15))
    row[13] = "\n    "
    _write_report(report, [row])

    output = tmp_path / "output"
    partition_alerts_rejections([report], output)

    day = pl.read_parquet(output / "AV23" / "2024-02-13.parquet")
    assert day.item(0, "modified_sections") is None


def _write_report(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(SOURCE_COLUMNS))
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def _source_row(*, line: int, start: datetime) -> list[object]:
    return [
        "AV",
        line,
        401,
        "Junta boquillera-boquillera",
        start,
        start,
        "10F",
        "Tiempo Real",
        "Aviso ZF",
        "4835/242",
        start.replace(minute=start.minute + 5),
        5.0,
        500.0,
        12,
        83.0,
        6.92,
        0.01,
        "F",
        3,
        "Cambio",
        "Grupo",
    ]
