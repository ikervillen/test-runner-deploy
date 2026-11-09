from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import polars as pl

from app.domain.models import Production
from app.infrastructure.aggregates.mia import MiaBundle, build_mia_aggregates
from app.infrastructure.aggregates.zc import build_zc_aggregate, zc_bundle_payload
from app.infrastructure.repositories.catalog import CsvCatalogRepository


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    relative_path: str
    exists: bool
    size_bytes: int | None
    modified_ns: int | None


@dataclass(frozen=True, slots=True)
class AlertBundle:
    production_id: str
    line: str
    relative_path: str | None
    rows: int
    sources: tuple[SourceFingerprint, ...]


@dataclass(frozen=True, slots=True)
class AlertAggregateRelease:
    release_id: str
    path: Path
    bundles: tuple[AlertBundle, ...]


class AlertAggregateResolver:
    """Resuelve solo paquetes que siguen siendo identicos a sus fuentes A/R."""

    def __init__(self, root: Path, alerts_root: Path) -> None:
        self._root = root.resolve()
        self._alerts_root = alerts_root.resolve()

    def paths_for(self, production: Production) -> tuple[Path, ...] | None:
        metadata = self._metadata()
        if metadata is None:
            return None
        bundles = metadata.get("bundles")
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
            return ()
        release_root = self._release_root()
        if release_root is None:
            return None
        path = (release_root / relative_path).resolve()
        if not _is_relative_to(path, release_root) or not path.is_file():
            return None
        return (path,)

    def _metadata(self) -> dict[str, object] | None:
        release_root = self._release_root()
        if release_root is None:
            return None
        path = release_root / "metadata.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _release_root(self) -> Path | None:
        pointer = self._root / "current.json"
        try:
            raw = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        release_id = raw.get("release_id") if isinstance(raw, dict) else None
        if not isinstance(release_id, str) or not release_id:
            return None
        path = (self._root / "releases" / release_id).resolve()
        return path if _is_relative_to(path, self._root) and path.is_dir() else None

    def _sources_match(self, bundle: dict[str, object]) -> bool:
        sources = bundle.get("sources")
        if not isinstance(sources, list):
            return False
        for item in sources:
            if not isinstance(item, dict):
                return False
            relative_path = item.get("relative_path")
            if not isinstance(relative_path, str):
                return False
            path = (self._alerts_root / relative_path).resolve()
            if not _is_relative_to(path, self._alerts_root):
                return False
            exists = path.is_file()
            if exists != item.get("exists"):
                return False
            if exists and (
                path.stat().st_size != item.get("size_bytes")
                or path.stat().st_mtime_ns != item.get("modified_ns")
            ):
                return False
        return True


def build_alert_aggregates(
    *,
    data_root: Path,
    alerts_root: Path,
    inspected_root: Path,
    hot_end_path: Path,
    output_root: Path,
    release_id: str | None = None,
) -> AlertAggregateRelease:
    """Materializa A/R de cada produccion como una particion consultable exacta."""

    catalog = CsvCatalogRepository(data_root, data_root / "manifest.csv")
    resolved_alerts = alerts_root.resolve()
    resolved_output = output_root.resolve()
    identifier = release_id or datetime.now().strftime("%Y%m%dT%H%M%S")
    target = resolved_output / "releases" / identifier
    if target.exists():
        raise FileExistsError(f"La release de agregados ya existe: {target}")
    staging = resolved_output / f".{identifier}.{uuid4().hex}.staging"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        productions = catalog.list_productions()
        bundles = tuple(
            _build_production_bundle(production, resolved_alerts, staging)
            for production in productions
        )
        mia_bundles = build_mia_aggregates(
            productions=productions,
            data_root=data_root,
            inspected_root=inspected_root,
            staging=staging,
        )
        zc_bundle = build_zc_aggregate(hot_end_path=hot_end_path, staging=staging)
        metadata = {
            "schema_version": 1,
            "release_id": identifier,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "bundles": [
                {
                    **asdict(bundle),
                    "sources": [asdict(source) for source in bundle.sources],
                }
                for bundle in bundles
            ],
            "mia_bundles": [_mia_bundle_payload(bundle) for bundle in mia_bundles],
            "zc_bundle": zc_bundle_payload(zc_bundle),
        }
        (staging / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
        _write_current_pointer(resolved_output, identifier)
        return AlertAggregateRelease(release_id=identifier, path=target, bundles=bundles)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _mia_bundle_payload(bundle: MiaBundle) -> dict[str, object]:
    return {
        **asdict(bundle),
        "sources": [asdict(source) for source in bundle.sources],
    }


def _build_production_bundle(
    production: Production,
    alerts_root: Path,
    staging: Path,
) -> AlertBundle:
    source_paths = _alert_source_paths(alerts_root, production)
    fingerprints = tuple(_fingerprint(path, alerts_root) for path in source_paths)
    existing = tuple(path for path in source_paths if path.is_file())
    if not existing:
        return AlertBundle(production.id, production.line, None, 0, fingerprints)

    start = min(branch.data_start for branch in production.branches)
    end = max(branch.data_end for branch in production.branches)
    frame = pl.scan_parquet([str(path) for path in existing])
    columns = frame.collect_schema().names()
    if "event_end" not in columns:
        frame = frame.with_columns(pl.col("event_start").alias("event_end"))
    frame = frame.with_columns(
        pl.when(pl.col("event_end").is_null())
        .then(pl.col("event_start"))
        .otherwise(pl.col("event_end"))
        .alias("_event_end")
    ).filter((pl.col("event_start") <= end) & (pl.col("_event_end") >= start))
    materialized = frame.drop("_event_end").collect(engine="streaming")
    if materialized.height == 0:
        return AlertBundle(production.id, production.line, None, 0, fingerprints)
    relative = Path("alerts") / production.line / f"{production.id}.parquet"
    destination = staging / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    materialized.write_parquet(destination, compression="zstd", compression_level=6)
    return AlertBundle(
        production.id,
        production.line,
        relative.as_posix(),
        materialized.height,
        fingerprints,
    )


def _alert_source_paths(alerts_root: Path, production: Production) -> tuple[Path, ...]:
    paths: list[Path] = []
    current = production.start_day
    while current <= production.end_day:
        paths.append(alerts_root / production.line / f"{current.isoformat()}.parquet")
        current += timedelta(days=1)
    return tuple(paths)


def _fingerprint(path: Path, root: Path) -> SourceFingerprint:
    if not path.is_file():
        return SourceFingerprint(path.relative_to(root).as_posix(), False, None, None)
    stat = path.stat()
    return SourceFingerprint(
        path.relative_to(root).as_posix(),
        True,
        stat.st_size,
        stat.st_mtime_ns,
    )


def _write_current_pointer(root: Path, release_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".current.{uuid4().hex}.json"
    temporary.write_text(
        json.dumps({"release_id": release_id}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, root / "current.json")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
