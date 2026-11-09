from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import polars as pl

from app.domain.models import Production
from app.domain.shifts import shift_windows
from app.infrastructure.parquet.schema import (
    DATE_COLUMN,
    DEFECT_COLUMN,
    REJECTED_COLUMN,
)

MIA_RATE_SCHEMA = pl.Schema(
    {
        "scope": pl.String(),
        "shift_id": pl.String(),
        "defect_code": pl.Int64(),
        "rejected": pl.Float64(),
        "inspected": pl.Float64(),
    }
)


@dataclass(frozen=True, slots=True)
class MiaSourceFingerprint:
    source: str
    relative_path: str
    exists: bool
    size_bytes: int | None
    modified_ns: int | None


@dataclass(frozen=True, slots=True)
class MiaBundle:
    production_id: str
    line: str
    relative_path: str | None
    rows: int
    sources: tuple[MiaSourceFingerprint, ...]


class MiaAggregateResolver:
    """Lee tasas MIA preagregadas solo si sus dos fuentes siguen vigentes."""

    def __init__(self, root: Path, data_root: Path, inspected_root: Path) -> None:
        self._root = root.resolve()
        self._data_root = data_root.resolve()
        self._inspected_root = inspected_root.resolve()
        self._frame_cache: dict[tuple[str, str], pl.DataFrame] = {}

    def rates_for(self, production: Production, shift_id: str | None = None) -> pl.DataFrame | None:
        metadata, release_root = self._metadata_and_root()
        if metadata is None or release_root is None:
            return None
        bundles = metadata.get("mia_bundles")
        if not isinstance(bundles, list):
            return None
        bundle = next(
            (
                item
                for item in bundles
                if isinstance(item, dict) and item.get("production_id") == production.id
            ),
            None,
        )
        if not isinstance(bundle, dict) or not self._sources_match(bundle):
            return None
        relative_path = bundle.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            return pl.DataFrame(schema=MIA_RATE_SCHEMA)
        path = (release_root / relative_path).resolve()
        if not _is_relative_to(path, release_root) or not path.is_file():
            return None
        cache_key = (str(path), str(path.stat().st_mtime_ns))
        frame = self._frame_cache.get(cache_key)
        if frame is None:
            self._frame_cache = {cache_key: pl.read_parquet(path)}
            frame = self._frame_cache[cache_key]
        scope = "production" if shift_id is None else "shift"
        filtered = frame.filter(pl.col("scope") == scope)
        if shift_id is not None:
            filtered = filtered.filter(pl.col("shift_id") == shift_id)
        return filtered

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

    def _sources_match(self, bundle: dict[str, object]) -> bool:
        sources = bundle.get("sources")
        if not isinstance(sources, list):
            return False
        for item in sources:
            if not isinstance(item, dict):
                return False
            source = item.get("source")
            relative_path = item.get("relative_path")
            if source not in {"mia", "inspected"} or not isinstance(relative_path, str):
                return False
            root = self._data_root if source == "mia" else self._inspected_root
            path = (root / relative_path).resolve()
            if not _is_relative_to(path, root):
                return False
            exists = path.is_file()
            if exists != item.get("exists"):
                return False
            if exists:
                stat = path.stat()
                if stat.st_size != item.get("size_bytes") or stat.st_mtime_ns != item.get(
                    "modified_ns"
                ):
                    return False
        return True


def build_mia_aggregates(
    *,
    productions: tuple[Production, ...],
    data_root: Path,
    inspected_root: Path,
    staging: Path,
) -> tuple[MiaBundle, ...]:
    return tuple(
        _build_production_bundle(production, data_root.resolve(), inspected_root.resolve(), staging)
        for production in productions
    )


def _build_production_bundle(
    production: Production,
    data_root: Path,
    inspected_root: Path,
    staging: Path,
) -> MiaBundle:
    inspection_path = inspected_root / production.line / f"{production.id}.parquet"
    sources = tuple(
        [_fingerprint("mia", branch.path, data_root) for branch in production.branches]
        + [_fingerprint("inspected", inspection_path, inspected_root)]
    )
    if not inspection_path.is_file() or not production.branches:
        return MiaBundle(production.id, production.line, None, 0, sources)

    mia = (
        pl.scan_parquet([str(branch.path) for branch in production.branches])
        .select([DATE_COLUMN, DEFECT_COLUMN, REJECTED_COLUMN])
        .collect(engine="streaming")
    )
    inspected = (
        pl.scan_parquet(inspection_path)
        .select(["timestamp", "machine_branch", "inspected_total"])
        .collect(engine="streaming")
    )
    rows = _production_rows(mia, inspected)
    start = min(branch.data_start for branch in production.branches)
    end = max(branch.data_end for branch in production.branches)
    for window in shift_windows(start, end):
        rows.extend(
            _shift_rows(
                mia,
                inspected,
                window.id,
                window.start,
                window.end,
            )
        )
    if not rows:
        return MiaBundle(production.id, production.line, None, 0, sources)
    relative = Path("mia") / production.line / f"{production.id}.parquet"
    path = staging / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=MIA_RATE_SCHEMA).write_parquet(
        path,
        compression="zstd",
        compression_level=6,
        statistics=True,
    )
    return MiaBundle(production.id, production.line, relative.as_posix(), len(rows), sources)


def _production_rows(mia: pl.DataFrame, inspected: pl.DataFrame) -> list[dict[str, object]]:
    total_inspected = _inspected_total(inspected)
    return _rate_rows("production", "", mia, total_inspected)


def _shift_rows(
    mia: pl.DataFrame,
    inspected: pl.DataFrame,
    shift_id: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, object]]:
    filtered_mia = mia.filter((pl.col(DATE_COLUMN) >= start) & (pl.col(DATE_COLUMN) < end))
    filtered_inspected = inspected.filter(
        (pl.col("timestamp") >= start) & (pl.col("timestamp") < end)
    )
    return _rate_rows("shift", shift_id, filtered_mia, _inspected_total(filtered_inspected))


def _rate_rows(
    scope: str,
    shift_id: str,
    mia: pl.DataFrame,
    inspected: float,
) -> list[dict[str, object]]:
    if inspected <= 0 or mia.height == 0:
        return []
    aggregated = (
        mia.group_by(DEFECT_COLUMN)
        .agg(pl.col(REJECTED_COLUMN).sum().cast(pl.Float64).alias("rejected"))
        .filter(pl.col("rejected") > 0)
    )
    return [
        {
            "scope": scope,
            "shift_id": shift_id,
            "defect_code": int(defect_code),
            "rejected": float(rejected),
            "inspected": inspected,
        }
        for defect_code, rejected in aggregated.iter_rows()
        if defect_code is not None
    ]


def _inspected_total(frame: pl.DataFrame) -> float:
    if frame.height == 0:
        return 0.0
    value = (
        frame.group_by(["timestamp", "machine_branch"])
        .agg(pl.col("inspected_total").max().alias("inspected"))
        .select(pl.col("inspected").sum().cast(pl.Float64).alias("inspected"))
        .item(0, "inspected")
    )
    return float(value or 0.0)


def _fingerprint(source: str, path: Path, root: Path) -> MiaSourceFingerprint:
    relative_path = path.resolve().relative_to(root.resolve()).as_posix()
    if not path.is_file():
        return MiaSourceFingerprint(source, relative_path, False, None, None)
    stat = path.stat()
    return MiaSourceFingerprint(source, relative_path, True, stat.st_size, stat.st_mtime_ns)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
