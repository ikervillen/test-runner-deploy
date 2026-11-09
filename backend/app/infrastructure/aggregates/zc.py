from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import polars as pl
from openpyxl import load_workbook

ZC_OBSERVATION_SCHEMA = pl.Schema(
    {
        "timestamp": pl.Datetime(),
        "line": pl.String(),
        "rueda": pl.Float64(),
        "theoretical": pl.Float64(),
    }
)
ZC_TO_LINE = {14: "AV23", 16: "AV32"}


@dataclass(frozen=True, slots=True)
class ZcSourceFingerprint:
    source_path: str
    exists: bool
    size_bytes: int | None
    modified_ns: int | None


@dataclass(frozen=True, slots=True)
class ZcBundle:
    relative_path: str | None
    rows: int
    source: ZcSourceFingerprint


class ZcAggregateResolver:
    """Lee ZC materializado solo si el Excel trazado sigue siendo el mismo."""

    def __init__(self, root: Path, hot_end_path: Path) -> None:
        self._root = root.resolve()
        self._hot_end_path = hot_end_path.resolve()

    def observations(self) -> pl.DataFrame | None:
        metadata, release_root = self._metadata_and_root()
        if metadata is None or release_root is None:
            return None
        bundle = metadata.get("zc_bundle")
        if not isinstance(bundle, dict) or not self._source_matches(bundle):
            return None
        relative_path = bundle.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            return pl.DataFrame(schema=ZC_OBSERVATION_SCHEMA)
        path = (release_root / relative_path).resolve()
        if not _is_relative_to(path, release_root) or not path.is_file():
            return None
        return pl.read_parquet(path)

    def _metadata_and_root(self) -> tuple[dict[str, object] | None, Path | None]:
        pointer = self._root / "current.json"
        try:
            raw = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, None
        release_id = raw.get("release_id") if isinstance(raw, dict) else None
        if not isinstance(release_id, str) or not release_id:
            return None, None
        release_root = (self._root / "releases" / release_id).resolve()
        if not _is_relative_to(release_root, self._root) or not release_root.is_dir():
            return None, None
        try:
            metadata = json.loads((release_root / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, None
        return (metadata, release_root) if isinstance(metadata, dict) else (None, None)

    def _source_matches(self, bundle: dict[str, object]) -> bool:
        source = bundle.get("source")
        if not isinstance(source, dict) or source.get("source_path") != str(self._hot_end_path):
            return False
        exists = self._hot_end_path.is_file()
        if exists != source.get("exists"):
            return False
        if not exists:
            return True
        stat = self._hot_end_path.stat()
        return stat.st_size == source.get("size_bytes") and stat.st_mtime_ns == source.get(
            "modified_ns"
        )


def build_zc_aggregate(*, hot_end_path: Path, staging: Path) -> ZcBundle:
    source = _fingerprint(hot_end_path)
    if not hot_end_path.is_file():
        return ZcBundle(None, 0, source)
    rows = _read_rows(hot_end_path)
    if not rows:
        return ZcBundle(None, 0, source)
    relative = Path("zc") / "observations.parquet"
    destination = staging / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=ZC_OBSERVATION_SCHEMA).write_parquet(
        destination,
        compression="zstd",
        compression_level=6,
        statistics=True,
    )
    return ZcBundle(relative.as_posix(), len(rows), source)


def zc_bundle_payload(bundle: ZcBundle) -> dict[str, object]:
    return {**asdict(bundle), "source": asdict(bundle.source)}


def _read_rows(path: Path) -> list[dict[str, object]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.worksheets[0]
        headers = tuple(
            str(value).strip() if value is not None else ""
            for value in next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True))
        )
        index = {header.upper(): position for position, header in enumerate(headers)}
        required = ("FECHA", "ID_LINEA_ZC", "RUEDA", "TEORICO_A", "TEORICO_B")
        if any(column not in index for column in required):
            return []
        rows: list[dict[str, object]] = []
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            timestamp = row[index["FECHA"]]
            line_id = _as_int(row[index["ID_LINEA_ZC"]])
            line = ZC_TO_LINE.get(line_id) if line_id is not None else None
            rueda = _as_float(row[index["RUEDA"]])
            theoretical = (_as_float(row[index["TEORICO_A"]]) or 0.0) + (
                _as_float(row[index["TEORICO_B"]]) or 0.0
            )
            if (
                isinstance(timestamp, datetime)
                and line is not None
                and rueda is not None
                and theoretical > 0
            ):
                rows.append(
                    {
                        "timestamp": timestamp,
                        "line": line,
                        "rueda": rueda,
                        "theoretical": theoretical,
                    }
                )
        return rows
    finally:
        workbook.close()


def _fingerprint(path: Path) -> ZcSourceFingerprint:
    resolved = path.resolve()
    if not resolved.is_file():
        return ZcSourceFingerprint(str(resolved), False, None, None)
    stat = resolved.stat()
    return ZcSourceFingerprint(str(resolved), True, stat.st_size, stat.st_mtime_ns)


def _as_int(value: object) -> int | None:
    number = _as_float(value)
    return int(number) if number is not None else None


def _as_float(value: object) -> float | None:
    if not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
