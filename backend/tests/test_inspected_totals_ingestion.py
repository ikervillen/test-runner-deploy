from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import polars as pl
from openpyxl import Workbook

from app.infrastructure.ingestion.inspected_totals import (
    SOURCE_COLUMNS,
    partition_inspected_totals,
)


def test_partitions_inspected_totals_by_existing_production_intervals(tmp_path: Path) -> None:
    source = tmp_path / "AV23.xlsx"
    _write_workbook(
        source,
        [
            _source_row(
                machine="AV23",
                branch="AV23-1",
                timestamp=datetime(2024, 2, 13, 8, 0),
                inspected=100,
                rejected=5,
            ),
            _source_row(
                machine="AV23",
                branch="AV23-1",
                timestamp=datetime(2024, 2, 13, 8, 0),
                inspected=120,
                rejected=7,
            ),
            _source_row(machine="AV23", branch="AV23-2", timestamp=datetime(2024, 2, 13, 8, 10)),
            _source_row(machine="AV23", branch="AV23-1", timestamp=datetime(2024, 2, 16, 8, 0)),
        ],
    )
    manifest = tmp_path / "production_manifest.csv"
    _write_manifest(manifest)

    output = tmp_path / "inspeccionadas_totales_parquet"
    result = partition_inspected_totals([source], manifest, output)

    assert result.source_rows == 4
    assert result.assigned_rows == 3
    assert result.unassigned_rows == 1
    assert result.partition_count == 1

    production = pl.read_parquet(output / "AV23" / "AV23_PROD_001.parquet")
    assert production.height == 2
    assert production["machine_branch"].to_list() == ["AV23-1", "AV23-2"]
    assert production["inspected_total"].sum() == 220
    assert production["rejected_total"].sum() == 17

    with (output / "manifest.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["production_id"] == "AV23_PROD_001"
    assert rows[0]["inspected_sum"] == "220"
    assert rows[0]["relative_path"] == "AV23/AV23_PROD_001.parquet"

    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["assigned_rows"] == 3
    assert "aggregation_rule" in metadata


def _write_manifest(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "production_id",
                "machine",
                "production_start_day",
                "production_end_day",
                "production_calendar_days",
                "machine_branch",
                "branch_days_with_data",
                "rows",
                "data_start",
                "data_end",
                "file_size_bytes",
                "relative_path",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "production_id": "AV23_PROD_001",
                "machine": "AV23",
                "production_start_day": "2024-02-13",
                "production_end_day": "2024-02-15",
                "production_calendar_days": "3",
                "machine_branch": "AV23-1",
                "branch_days_with_data": "3",
                "rows": "10",
                "data_start": "2024-02-13 07:20:00",
                "data_end": "2024-02-15 12:20:00",
                "file_size_bytes": "1",
                "relative_path": "ignored/AV23-1.parquet",
            }
        )
        writer.writerow(
            {
                "production_id": "AV23_PROD_001",
                "machine": "AV23",
                "production_start_day": "2024-02-13",
                "production_end_day": "2024-02-15",
                "production_calendar_days": "3",
                "machine_branch": "AV23-2",
                "branch_days_with_data": "3",
                "rows": "10",
                "data_start": "2024-02-13 07:20:00",
                "data_end": "2024-02-15 12:20:00",
                "file_size_bytes": "1",
                "relative_path": "ignored/AV23-2.parquet",
            }
        )


def _write_workbook(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(SOURCE_COLUMNS))
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def _source_row(
    *,
    machine: str,
    branch: str,
    timestamp: datetime,
    inspected: int = 100,
    rejected: int = 5,
) -> list[object]:
    return [
        machine,
        "AV",
        timestamp,
        timestamp,
        1,
        1,
        1,
        inspected,
        rejected,
        4,
        "1D",
        20,
        timestamp,
        branch,
        "4835/242",
        "M",
        "4835/242",
        "123",
        "1",
        "1",
        "Descripcion",
        10,
        10,
        2,
        20,
        8,
        "08:00",
        13,
        8,
        "Manana",
        "M",
        1,
        "06:00",
        "14:00",
        "2024-02-13",
        "Manana",
        "",
    ]
