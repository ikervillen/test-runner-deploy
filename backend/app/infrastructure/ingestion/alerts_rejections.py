from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final, TypeAlias, cast

import polars as pl
from openpyxl import load_workbook

CellValue: TypeAlias = str | int | float | datetime | date | None
EventKey: TypeAlias = tuple[CellValue, ...]

SOURCE_COLUMNS: Final[tuple[str, ...]] = (
    "Planta",
    "Línea",
    "Defecto",
    "Descripción",
    "Fecha inicio V.",
    "Año/Mes",
    "Cav.",
    "Origen",
    "Tipo",
    "Modelo",
    "Fecha fin V.",
    "Minutos",
    "Peso Teor.",
    "Secc. Mod.",
    "c/m",
    "CR",
    "Pérdidas",
    "Color",
    "Moldes",
    "Día",
    "Agrupación",
)

OUTPUT_SCHEMA: Final[pl.Schema] = pl.Schema(
    {
        "event_id": pl.String(),
        "plant": pl.String(),
        "line": pl.Int64(),
        "machine": pl.String(),
        "defect_code": pl.Int64(),
        "defect_description": pl.String(),
        "event_start": pl.Datetime("us"),
        "event_end": pl.Datetime("us"),
        "start_date": pl.Date(),
        "reported_year_month": pl.Datetime("us"),
        "cavity": pl.String(),
        "origin": pl.String(),
        "event_type": pl.String(),
        "model": pl.String(),
        "duration_minutes": pl.Float64(),
        "theoretical_weight": pl.Float64(),
        "modified_sections": pl.Int64(),
        "containers_per_minute": pl.Float64(),
        "pull_rate": pl.Float64(),
        "losses": pl.Float64(),
        "color": pl.String(),
        "molds": pl.Int64(),
        "day_context": pl.String(),
        "grouping": pl.String(),
        "source_files": pl.List(pl.String()),
    }
)


@dataclass(frozen=True, slots=True)
class PartitionManifestRow:
    machine: str
    start_date: date
    rows: int
    event_start: datetime
    event_end: datetime
    source_file_count: int
    file_size_bytes: int
    relative_path: str


@dataclass(frozen=True, slots=True)
class PartitionResult:
    source_rows: int
    unique_rows: int
    duplicate_rows_removed: int
    partition_count: int
    manifest_rows: tuple[PartitionManifestRow, ...]


def partition_alerts_rejections(
    source_paths: Sequence[Path],
    output_root: Path,
    *,
    compression_level: int = 6,
) -> PartitionResult:
    """Normalize reports and partition events by machine and event start date."""
    if not source_paths:
        raise ValueError("At least one source report is required.")
    if output_root.exists():
        raise FileExistsError(f"Output directory already exists: {output_root}")

    events_by_key: dict[EventKey, dict[str, object]] = {}
    sources_by_key: defaultdict[EventKey, set[str]] = defaultdict(set)
    source_rows = 0

    for source_path in source_paths:
        rows = _read_report(source_path)
        for row in rows:
            source_rows += 1
            key = tuple(row[column] for column in SOURCE_COLUMNS)
            events_by_key.setdefault(key, _normalize_event(row))
            sources_by_key[key].add(source_path.name)

    normalized_events: list[dict[str, object]] = []
    for key, event in events_by_key.items():
        event["event_id"] = _event_id(key)
        event["source_files"] = sorted(sources_by_key[key])
        normalized_events.append(event)

    output_root.mkdir(parents=True)
    manifest_rows = _write_partitions(
        normalized_events,
        output_root,
        compression_level=compression_level,
    )
    result = PartitionResult(
        source_rows=source_rows,
        unique_rows=len(normalized_events),
        duplicate_rows_removed=source_rows - len(normalized_events),
        partition_count=len(manifest_rows),
        manifest_rows=tuple(manifest_rows),
    )
    _write_manifest(output_root, result)
    _write_metadata(output_root, source_paths, result, compression_level)
    return result


def _read_report(source_path: Path) -> Iterable[dict[str, CellValue]]:
    workbook = load_workbook(source_path, read_only=True, data_only=True)
    try:
        if len(workbook.worksheets) != 1:
            raise ValueError(f"{source_path.name}: expected one worksheet.")
        worksheet = workbook.worksheets[0]
        rows = worksheet.iter_rows(values_only=True)
        header = tuple(str(value).strip() if value is not None else "" for value in next(rows))
        if header != SOURCE_COLUMNS:
            raise ValueError(
                f"{source_path.name}: unexpected columns. "
                f"Expected {SOURCE_COLUMNS!r}, received {header!r}."
            )
        for values in rows:
            if all(value is None for value in values):
                continue
            yield dict(zip(SOURCE_COLUMNS, cast(tuple[CellValue, ...], values), strict=True))
    finally:
        workbook.close()


def _normalize_event(row: dict[str, CellValue]) -> dict[str, object]:
    plant = _required_text(row["Planta"], "Planta")
    line = _required_int(row["Línea"], "Línea")
    event_start = _required_datetime(row["Fecha inicio V."], "Fecha inicio V.")
    event_end = _required_datetime(row["Fecha fin V."], "Fecha fin V.")
    if event_end < event_start:
        raise ValueError("Fecha fin V. cannot be earlier than Fecha inicio V.")

    return {
        "event_id": "",
        "plant": plant,
        "line": line,
        "machine": f"{plant}{line}",
        "defect_code": _required_int(row["Defecto"], "Defecto"),
        "defect_description": _required_text(row["Descripción"], "Descripción"),
        "event_start": event_start,
        "event_end": event_end,
        "start_date": event_start.date(),
        "reported_year_month": _required_datetime(row["Año/Mes"], "Año/Mes"),
        "cavity": _optional_text(row["Cav."]),
        "origin": _optional_text(row["Origen"]),
        "event_type": _optional_text(row["Tipo"]),
        "model": _optional_text(row["Modelo"]),
        "duration_minutes": _optional_float(row["Minutos"]),
        "theoretical_weight": _optional_float(row["Peso Teor."]),
        "modified_sections": _optional_int(row["Secc. Mod."]),
        "containers_per_minute": _optional_float(row["c/m"]),
        "pull_rate": _optional_float(row["CR"]),
        "losses": _optional_float(row["Pérdidas"]),
        "color": _optional_text(row["Color"]),
        "molds": _optional_int(row["Moldes"]),
        "day_context": _optional_text(row["Día"]),
        "grouping": _optional_text(row["Agrupación"]),
        "source_files": [],
    }


def _write_partitions(
    events: Sequence[dict[str, object]],
    output_root: Path,
    *,
    compression_level: int,
) -> list[PartitionManifestRow]:
    frame = pl.DataFrame(events, schema=OUTPUT_SCHEMA).sort(["machine", "event_start", "event_id"])
    manifest_rows: list[PartitionManifestRow] = []

    for partition in frame.partition_by(["machine", "start_date"], maintain_order=True):
        machine = cast(str, partition.item(0, "machine"))
        start_date = cast(date, partition.item(0, "start_date"))
        relative_path = Path(machine) / f"{start_date.isoformat()}.parquet"
        destination = output_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        partition.write_parquet(
            destination,
            compression="zstd",
            compression_level=compression_level,
            statistics=True,
        )
        source_files = partition.select(pl.col("source_files").explode().unique()).height
        manifest_rows.append(
            PartitionManifestRow(
                machine=machine,
                start_date=start_date,
                rows=partition.height,
                event_start=cast(datetime, partition["event_start"].min()),
                event_end=cast(datetime, partition["event_end"].max()),
                source_file_count=source_files,
                file_size_bytes=destination.stat().st_size,
                relative_path=relative_path.as_posix(),
            )
        )
    return manifest_rows


def _write_manifest(output_root: Path, result: PartitionResult) -> None:
    fieldnames = tuple(PartitionManifestRow.__dataclass_fields__)
    with (output_root / "manifest.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in result.manifest_rows:
            writer.writerow(
                {
                    "machine": row.machine,
                    "start_date": row.start_date.isoformat(),
                    "rows": row.rows,
                    "event_start": row.event_start.isoformat(sep=" "),
                    "event_end": row.event_end.isoformat(sep=" "),
                    "source_file_count": row.source_file_count,
                    "file_size_bytes": row.file_size_bytes,
                    "relative_path": row.relative_path,
                }
            )


def _write_metadata(
    output_root: Path,
    source_paths: Sequence[Path],
    result: PartitionResult,
    compression_level: int,
) -> None:
    payload = {
        "source_files": [str(path.resolve()) for path in source_paths],
        "partition_rule": "Machine (Planta + Línea) and calendar date of Fecha inicio V.",
        "deduplication_rule": "Exact equality across all 21 source columns.",
        "compression": "zstd",
        "compression_level": compression_level,
        "source_rows": result.source_rows,
        "unique_rows": result.unique_rows,
        "duplicate_rows_removed": result.duplicate_rows_removed,
        "partition_count": result.partition_count,
        "output_columns": list(OUTPUT_SCHEMA),
        "defect_correlation": {
            "status": "unresolved",
            "reason": (
                "The report uses SIP_DEFECTO codes while AV-2024-2026 uses "
                "SIP_MP9_DEFECTO_MIA codes. No validated master mapping was found."
            ),
        },
    }
    (output_root / "metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _event_id(key: EventKey) -> str:
    serialized = json.dumps(key, ensure_ascii=False, default=_json_default, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported event identity value: {type(value)!r}")


def _required_text(value: CellValue, column: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{column} is required.")
    return text


def _optional_text(value: CellValue) -> str:
    return "" if value is None else str(value).strip()


def _required_int(value: CellValue, column: str) -> int:
    converted = _optional_int(value)
    if converted is None:
        raise ValueError(f"{column} is required.")
    return converted


def _optional_int(value: CellValue) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float, str)):
        return int(float(value))
    raise ValueError(f"Expected a numeric value, received {value!r}.")


def _optional_float(value: CellValue) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float, str)):
        return float(value)
    raise ValueError(f"Expected a numeric value, received {value!r}.")


def _required_datetime(value: CellValue, column: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        return datetime.fromisoformat(value.strip())
    raise ValueError(f"{column} must contain a valid date and time.")
