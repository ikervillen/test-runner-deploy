from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final, TypeAlias, cast

import polars as pl
from openpyxl import load_workbook

CellValue: TypeAlias = str | int | float | datetime | date | None

SOURCE_COLUMNS: Final[tuple[str, ...]] = (
    "LISTA_RECH_MAQ_IS",
    "LISTA_RECH_PLANTA",
    "LISTA_RECH_FECHA",
    "LISTA_RECH_FECHA_REAL",
    "LISTA_RECH_MOLDE",
    "LISTA_RECH_RAMAL",
    "LISTA_RECH_COD_MAQ_ZF",
    "LISTA_RECH_INSPECC",
    "LISTA_RECH_RECH",
    "LISTA_RECH_RECH_AUTO",
    "LISTA_RECH_CAVIDAD",
    "LISTA_RECH_CAVIDAD_TOTAL",
    "LISTA_RECH_FECHA_R",
    "LISTA_RECH_MAQ_IS_RAMAL",
    "LISTA_RECH_MODELO",
    "LISTA_RECH_MODELO_COLOR",
    "LISTA_RECH_MODELO_SIN_COLOR",
    "LISTA_RECH_ID_MODELO",
    "LISTA_RECH_SECCION",
    "LISTA_RECH_ID_MAQUINA",
    "LISTA_RECH_DESCRIPCION_CORTA",
    "LISTA_RECH_NUM_SECCIONES",
    "LISTA_RECH_NUM_SECCIONES_OPERATIVAS",
    "LISTA_RECH_ID_NUM_GOTAS_SIMUL",
    "LISTA_RECH_NUM_CAVIDADES",
    "LISTA_RECH_HORA",
    "LISTA_RECH_soloHORA",
    "LISTA_RECH_dia",
    "LISTA_RECH_HORA_AGRUPADA",
    "LISTA_RECH_TURNO",
    "LISTA_RECH_TURNO_CORTO",
    "LISTA_RECH_TURNO_ORDEN",
    "LISTA_RECH_HORA_INI_TURNO",
    "LISTA_RECH_HORA_FIN_TURNO",
    "LISTA_RECH_TURNO_DIA",
    "LISTA_RECH_TURNO_NUEVO",
    "LISTA_RECH_JOBCHANGE",
)

OUTPUT_SCHEMA: Final[pl.Schema] = pl.Schema(
    {
        "production_id": pl.String(),
        "source_file": pl.String(),
        "source_row": pl.Int64(),
        "machine": pl.String(),
        "machine_branch": pl.String(),
        "timestamp": pl.Datetime("us"),
        "date": pl.Date(),
        "inspected_total": pl.Int64(),
        "rejected_total": pl.Int64(),
        "auto_rejected_total": pl.Int64(),
        "mold": pl.Int64(),
        "branch_code": pl.String(),
        "zf_machine_code": pl.Int64(),
        "cavity": pl.String(),
        "cavity_total": pl.String(),
        "model": pl.String(),
        "model_color": pl.String(),
        "model_without_color": pl.String(),
        "model_id": pl.String(),
        "section": pl.String(),
        "machine_id": pl.String(),
        "short_description": pl.String(),
        "sections": pl.Int64(),
        "operative_sections": pl.Int64(),
        "gobs": pl.Int64(),
        "cavities": pl.Int64(),
        "hour": pl.String(),
        "hour_only": pl.String(),
        "day": pl.String(),
        "grouped_hour": pl.String(),
        "shift": pl.String(),
        "short_shift": pl.String(),
        "shift_order": pl.Int64(),
        "shift_start": pl.String(),
        "shift_end": pl.String(),
        "shift_day": pl.String(),
        "new_shift": pl.String(),
        "jobchange": pl.String(),
    }
)


@dataclass(frozen=True, slots=True)
class ProductionInterval:
    production_id: str
    machine: str
    machine_branch: str
    production_start_day: date
    production_end_day: date
    data_start: datetime
    data_end: datetime


@dataclass(frozen=True, slots=True)
class PartitionManifestRow:
    production_id: str
    machine: str
    production_start_day: date
    production_end_day: date
    rows: int
    data_start: datetime
    data_end: datetime
    inspected_sum: int
    rejected_sum: int
    auto_rejected_sum: int
    source_file_count: int
    file_size_bytes: int
    relative_path: str


@dataclass(frozen=True, slots=True)
class PartitionResult:
    source_rows: int
    assigned_rows: int
    unassigned_rows: int
    partition_count: int
    manifest_rows: tuple[PartitionManifestRow, ...]


@dataclass(slots=True)
class _StagingWriter:
    file: object
    writer: csv.DictWriter[str]


def partition_inspected_totals(
    source_paths: Sequence[Path],
    production_manifest_path: Path,
    output_root: Path,
    *,
    compression_level: int = 6,
) -> PartitionResult:
    """Normalize inspected bottle totals and partition rows by production."""
    if not source_paths:
        raise ValueError("At least one inspected-total workbook is required.")
    if output_root.exists():
        raise FileExistsError(f"Output directory already exists: {output_root}")

    intervals = _read_production_intervals(production_manifest_path)
    intervals_by_branch: dict[str, list[ProductionInterval]] = defaultdict(list)
    for interval in intervals:
        intervals_by_branch[interval.machine_branch].append(interval)

    source_rows = 0
    assigned_rows = 0

    output_root.mkdir(parents=True)
    staging_dir = output_root / "_staging_csv"
    staging_dir.mkdir()
    writers: dict[str, _StagingWriter] = {}
    try:
        for source_path in source_paths:
            workbook_rows = _read_workbook(source_path)
            for source_row, row in workbook_rows:
                source_rows += 1
                timestamp = _required_datetime(
                    row["LISTA_RECH_FECHA_REAL"], "LISTA_RECH_FECHA_REAL"
                )
                machine_branch = _required_text(
                    row["LISTA_RECH_MAQ_IS_RAMAL"], "LISTA_RECH_MAQ_IS_RAMAL"
                )
                matched = _find_interval(intervals_by_branch[machine_branch], timestamp)
                if matched is None:
                    continue
                assigned_rows += 1
                normalized = _normalize_row(
                    row,
                    source_file=source_path.name,
                    source_row=source_row,
                    interval=matched,
                    timestamp=timestamp,
                )
                _write_staging_row(staging_dir, writers, matched.production_id, normalized)
    finally:
        for staging_writer in writers.values():
            staging_writer.file.close()  # type: ignore[attr-defined]

    manifest_rows = _write_partitions_from_staging(
        staging_dir,
        output_root,
        compression_level=compression_level,
    )
    shutil.rmtree(staging_dir)
    result = PartitionResult(
        source_rows=source_rows,
        assigned_rows=assigned_rows,
        unassigned_rows=source_rows - assigned_rows,
        partition_count=len(manifest_rows),
        manifest_rows=tuple(manifest_rows),
    )
    _write_manifest(output_root, result)
    _write_metadata(
        output_root,
        source_paths,
        production_manifest_path,
        result,
        compression_level,
    )
    return result


def _read_production_intervals(manifest_path: Path) -> list[ProductionInterval]:
    with manifest_path.open(encoding="utf-8", newline="") as file:
        rows = csv.DictReader(file)
        return [
            ProductionInterval(
                production_id=row["production_id"],
                machine=row["machine"],
                machine_branch=row["machine_branch"],
                production_start_day=date.fromisoformat(row["production_start_day"]),
                production_end_day=date.fromisoformat(row["production_end_day"]),
                data_start=datetime.fromisoformat(row["data_start"]),
                data_end=datetime.fromisoformat(row["data_end"]),
            )
            for row in rows
        ]


def _read_workbook(source_path: Path) -> Iterable[tuple[int, dict[str, CellValue]]]:
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
        for index, values in enumerate(rows, start=2):
            if all(value is None for value in values):
                continue
            yield (
                index,
                dict(zip(SOURCE_COLUMNS, cast(tuple[CellValue, ...], values), strict=True)),
            )
    finally:
        workbook.close()


def _find_interval(
    intervals: Sequence[ProductionInterval],
    timestamp: datetime,
) -> ProductionInterval | None:
    for interval in intervals:
        if interval.data_start <= timestamp <= interval.data_end:
            return interval
    return None


def _normalize_row(
    row: dict[str, CellValue],
    *,
    source_file: str,
    source_row: int,
    interval: ProductionInterval,
    timestamp: datetime,
) -> dict[str, object]:
    return {
        "production_id": interval.production_id,
        "source_file": source_file,
        "source_row": source_row,
        "machine": interval.machine,
        "machine_branch": interval.machine_branch,
        "timestamp": timestamp,
        "date": timestamp.date(),
        "inspected_total": _required_int(row["LISTA_RECH_INSPECC"], "LISTA_RECH_INSPECC"),
        "rejected_total": _required_int(row["LISTA_RECH_RECH"], "LISTA_RECH_RECH"),
        "auto_rejected_total": _required_int(row["LISTA_RECH_RECH_AUTO"], "LISTA_RECH_RECH_AUTO"),
        "mold": _optional_int(row["LISTA_RECH_MOLDE"]),
        "branch_code": _optional_text(row["LISTA_RECH_RAMAL"]),
        "zf_machine_code": _optional_int(row["LISTA_RECH_COD_MAQ_ZF"]),
        "cavity": _optional_text(row["LISTA_RECH_CAVIDAD"]),
        "cavity_total": _optional_text(row["LISTA_RECH_CAVIDAD_TOTAL"]),
        "model": _optional_text(row["LISTA_RECH_MODELO"]),
        "model_color": _optional_text(row["LISTA_RECH_MODELO_COLOR"]),
        "model_without_color": _optional_text(row["LISTA_RECH_MODELO_SIN_COLOR"]),
        "model_id": _optional_text(row["LISTA_RECH_ID_MODELO"]),
        "section": _optional_text(row["LISTA_RECH_SECCION"]),
        "machine_id": _optional_text(row["LISTA_RECH_ID_MAQUINA"]),
        "short_description": _optional_text(row["LISTA_RECH_DESCRIPCION_CORTA"]),
        "sections": _optional_int(row["LISTA_RECH_NUM_SECCIONES"]),
        "operative_sections": _optional_int(row["LISTA_RECH_NUM_SECCIONES_OPERATIVAS"]),
        "gobs": _optional_int(row["LISTA_RECH_ID_NUM_GOTAS_SIMUL"]),
        "cavities": _optional_int(row["LISTA_RECH_NUM_CAVIDADES"]),
        "hour": _optional_text(row["LISTA_RECH_HORA"]),
        "hour_only": _optional_text(row["LISTA_RECH_soloHORA"]),
        "day": _optional_text(row["LISTA_RECH_dia"]),
        "grouped_hour": _optional_text(row["LISTA_RECH_HORA_AGRUPADA"]),
        "shift": _optional_text(row["LISTA_RECH_TURNO"]),
        "short_shift": _optional_text(row["LISTA_RECH_TURNO_CORTO"]),
        "shift_order": _optional_int(row["LISTA_RECH_TURNO_ORDEN"]),
        "shift_start": _optional_text(row["LISTA_RECH_HORA_INI_TURNO"]),
        "shift_end": _optional_text(row["LISTA_RECH_HORA_FIN_TURNO"]),
        "shift_day": _optional_text(row["LISTA_RECH_TURNO_DIA"]),
        "new_shift": _optional_text(row["LISTA_RECH_TURNO_NUEVO"]),
        "jobchange": _optional_text(row["LISTA_RECH_JOBCHANGE"]),
    }


def _write_staging_row(
    staging_dir: Path,
    writers: dict[str, _StagingWriter],
    production_id: str,
    row: dict[str, object],
) -> None:
    staging_writer = writers.get(production_id)
    if staging_writer is None:
        file = (staging_dir / f"{production_id}.csv").open("w", encoding="utf-8", newline="")
        writer = csv.DictWriter(file, fieldnames=list(OUTPUT_SCHEMA))
        writer.writeheader()
        staging_writer = _StagingWriter(file=file, writer=writer)
        writers[production_id] = staging_writer
    staging_writer.writer.writerow({key: _csv_value(value) for key, value in row.items()})


def _write_partitions_from_staging(
    staging_dir: Path,
    output_root: Path,
    *,
    compression_level: int,
) -> list[PartitionManifestRow]:
    manifest_rows: list[PartitionManifestRow] = []
    for staging_path in sorted(staging_dir.glob("*.csv")):
        production_id = staging_path.stem
        frame = _deduplicate_by_timestamp_branch(_read_staged_frame(staging_path)).sort(
            ["machine_branch", "timestamp", "source_row"]
        )
        machine = cast(str, frame.item(0, "machine"))
        production_start_day = cast(date, frame["date"].min())
        production_end_day = cast(date, frame["date"].max())
        relative_path = Path(machine) / f"{production_id}.parquet"
        destination = output_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(
            destination,
            compression="zstd",
            compression_level=compression_level,
            statistics=True,
        )
        manifest_rows.append(
            PartitionManifestRow(
                production_id=production_id,
                machine=machine,
                production_start_day=production_start_day,
                production_end_day=production_end_day,
                rows=frame.height,
                data_start=cast(datetime, frame["timestamp"].min()),
                data_end=cast(datetime, frame["timestamp"].max()),
                inspected_sum=cast(int, frame["inspected_total"].sum()),
                rejected_sum=cast(int, frame["rejected_total"].sum()),
                auto_rejected_sum=cast(int, frame["auto_rejected_total"].sum()),
                source_file_count=frame.select(pl.col("source_file").n_unique()).item(),
                file_size_bytes=destination.stat().st_size,
                relative_path=relative_path.as_posix(),
            )
        )
    return manifest_rows


def _read_staged_frame(staging_path: Path) -> pl.DataFrame:
    frame = pl.read_csv(staging_path, null_values=[""], try_parse_dates=True)
    expressions = [
        pl.col(name).cast(dtype, strict=False).alias(name) for name, dtype in OUTPUT_SCHEMA.items()
    ]
    return frame.with_columns(expressions).select(list(OUTPUT_SCHEMA))


def _deduplicate_by_timestamp_branch(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        frame.group_by(
            ["production_id", "machine", "machine_branch", "timestamp"],
            maintain_order=True,
        )
        .agg(
            pl.col("source_file").first(),
            pl.col("source_row").min(),
            pl.col("date").first(),
            pl.col("inspected_total").max(),
            pl.col("rejected_total").sum(),
            pl.col("auto_rejected_total").sum(),
            pl.col("mold").first(),
            pl.col("branch_code").first(),
            pl.col("zf_machine_code").first(),
            pl.col("cavity").first(),
            pl.col("cavity_total").first(),
            pl.col("model").first(),
            pl.col("model_color").first(),
            pl.col("model_without_color").first(),
            pl.col("model_id").first(),
            pl.col("section").first(),
            pl.col("machine_id").first(),
            pl.col("short_description").first(),
            pl.col("sections").first(),
            pl.col("operative_sections").first(),
            pl.col("gobs").first(),
            pl.col("cavities").first(),
            pl.col("hour").first(),
            pl.col("hour_only").first(),
            pl.col("day").first(),
            pl.col("grouped_hour").first(),
            pl.col("shift").first(),
            pl.col("short_shift").first(),
            pl.col("shift_order").first(),
            pl.col("shift_start").first(),
            pl.col("shift_end").first(),
            pl.col("shift_day").first(),
            pl.col("new_shift").first(),
            pl.col("jobchange").first(),
        )
        .select(list(OUTPUT_SCHEMA))
    )


def _write_manifest(output_root: Path, result: PartitionResult) -> None:
    fieldnames = tuple(PartitionManifestRow.__dataclass_fields__)
    with (output_root / "manifest.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in result.manifest_rows:
            writer.writerow(
                {
                    "production_id": row.production_id,
                    "machine": row.machine,
                    "production_start_day": row.production_start_day.isoformat(),
                    "production_end_day": row.production_end_day.isoformat(),
                    "rows": row.rows,
                    "data_start": row.data_start.isoformat(sep=" "),
                    "data_end": row.data_end.isoformat(sep=" "),
                    "inspected_sum": row.inspected_sum,
                    "rejected_sum": row.rejected_sum,
                    "auto_rejected_sum": row.auto_rejected_sum,
                    "source_file_count": row.source_file_count,
                    "file_size_bytes": row.file_size_bytes,
                    "relative_path": row.relative_path,
                }
            )


def _write_metadata(
    output_root: Path,
    source_paths: Sequence[Path],
    production_manifest_path: Path,
    result: PartitionResult,
    compression_level: int,
) -> None:
    payload = {
        "source_files": [str(path.resolve()) for path in source_paths],
        "production_manifest": str(production_manifest_path.resolve()),
        "partition_rule": (
            "Rows are assigned to the existing AV-2024-2026 production interval "
            "matching LISTA_RECH_MAQ_IS_RAMAL and LISTA_RECH_FECHA_REAL."
        ),
        "aggregation_rule": (
            "Output rows are deduplicated by production_id, machine, machine_branch "
            "and timestamp. inspected_total uses max per group; rejected totals are summed."
        ),
        "compression": "zstd",
        "compression_level": compression_level,
        "source_rows": result.source_rows,
        "assigned_rows": result.assigned_rows,
        "unassigned_rows": result.unassigned_rows,
        "partition_count": result.partition_count,
        "output_columns": list(OUTPUT_SCHEMA),
    }
    (output_root / "metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"-", "NA", "N/A"}:
            return None
        return int(float(text))
    if isinstance(value, (int, float, str)):
        return int(float(value))
    raise ValueError(f"Expected a numeric value, received {value!r}.")


def _required_datetime(value: CellValue, column: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        return datetime.fromisoformat(value.strip())
    raise ValueError(f"{column} must contain a valid date and time.")


def _csv_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compression-level", type=int, default=6)
    args = parser.parse_args()

    source_paths = sorted(
        path for path in args.source_dir.glob("*.xlsx") if not path.name.startswith("~$")
    )
    result = partition_inspected_totals(
        source_paths,
        args.manifest,
        args.output,
        compression_level=args.compression_level,
    )
    print(
        json.dumps(
            {
                "source_rows": result.source_rows,
                "assigned_rows": result.assigned_rows,
                "unassigned_rows": result.unassigned_rows,
                "partition_count": result.partition_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    _main()
