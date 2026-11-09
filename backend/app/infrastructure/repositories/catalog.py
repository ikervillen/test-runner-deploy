from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import TypedDict, cast

from app.api.errors import ApplicationError
from app.domain.models import BranchFile, Production


class ManifestRow(TypedDict):
    production_id: str
    machine: str
    production_start_day: str
    production_end_day: str
    production_calendar_days: str
    machine_branch: str
    branch_days_with_data: str
    rows: str
    data_start: str
    data_end: str
    file_size_bytes: str
    relative_path: str
    bottle_model_id: str
    segment_id: str
    dar_profile_id: str


class CsvCatalogRepository:
    def __init__(self, data_root: Path, manifest_path: Path) -> None:
        self._data_root = data_root.resolve()
        self._manifest_path = manifest_path.resolve()
        self._cached_version = -1
        self._cached_productions: tuple[Production, ...] = ()

    def version(self) -> int:
        try:
            return self._manifest_path.stat().st_mtime_ns
        except FileNotFoundError as exc:
            raise ApplicationError(
                "CATALOG_NOT_FOUND",
                "No se encuentra el catalogo de producciones.",
                status_code=503,
                details={"manifest": str(self._manifest_path)},
            ) from exc

    def list_productions(self) -> tuple[Production, ...]:
        version = self.version()
        if version != self._cached_version:
            self._cached_productions = self._load()
            self._cached_version = version
        return self._cached_productions

    def get_productions(self, production_ids: tuple[str, ...]) -> tuple[Production, ...]:
        by_id = {production.id: production for production in self.list_productions()}
        missing = sorted(set(production_ids) - by_id.keys())
        if missing:
            raise ApplicationError(
                "PRODUCTION_NOT_FOUND",
                "Una o varias producciones no existen.",
                status_code=404,
                details={"production_ids": missing},
            )
        return tuple(by_id[production_id] for production_id in production_ids)

    def _load(self) -> tuple[Production, ...]:
        grouped: dict[str, list[ManifestRow]] = defaultdict(list)
        try:
            with self._manifest_path.open(encoding="utf-8", newline="") as file:
                for row in csv.DictReader(file):
                    typed_row = cast(ManifestRow, row)
                    grouped[typed_row["production_id"]].append(typed_row)
        except (OSError, TypeError, ValueError) as exc:
            raise ApplicationError(
                "INVALID_CATALOG",
                "El catalogo de producciones no es valido.",
                status_code=500,
            ) from exc

        productions = []
        for production_id, rows in sorted(grouped.items()):
            first = rows[0]
            bottle_model_id = _optional_value(first.get("bottle_model_id"))
            segment_id = _optional_value(first.get("segment_id"))
            dar_profile_id = _optional_value(first.get("dar_profile_id"))
            _validate_production_metadata(
                production_id,
                rows,
                bottle_model_id,
                segment_id,
                dar_profile_id,
            )
            branches = tuple(
                BranchFile(
                    branch=row["machine_branch"],
                    path=self._safe_path(row["relative_path"]),
                    rows=int(row["rows"]),
                    data_start=datetime.fromisoformat(row["data_start"]),
                    data_end=datetime.fromisoformat(row["data_end"]),
                )
                for row in sorted(rows, key=lambda item: item["machine_branch"])
            )
            productions.append(
                Production(
                    id=production_id,
                    line=first["machine"],
                    start_day=date.fromisoformat(first["production_start_day"]),
                    end_day=date.fromisoformat(first["production_end_day"]),
                    branches=branches,
                    bottle_model_id=bottle_model_id,
                    segment_id=segment_id,
                    dar_profile_id=dar_profile_id,
                )
            )
        return tuple(productions)

    def _safe_path(self, relative_path: str) -> Path:
        candidate = (self._data_root / relative_path).resolve()
        if not candidate.is_relative_to(self._data_root):
            raise ApplicationError(
                "INVALID_DATA_PATH",
                "El catalogo contiene una ruta fuera del directorio permitido.",
                status_code=500,
            )
        if not candidate.is_file():
            raise ApplicationError(
                "DATA_FILE_NOT_FOUND",
                "No se encuentra un archivo Parquet del catalogo.",
                status_code=503,
                details={"path": str(candidate)},
            )
        return candidate


def _optional_value(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _validate_production_metadata(
    production_id: str,
    rows: list[ManifestRow],
    bottle_model_id: str | None,
    segment_id: str | None,
    dar_profile_id: str | None,
) -> None:
    expected = (bottle_model_id, segment_id, dar_profile_id)
    for row in rows[1:]:
        values = (
            _optional_value(row.get("bottle_model_id")),
            _optional_value(row.get("segment_id")),
            _optional_value(row.get("dar_profile_id")),
        )
        if values != expected:
            raise ApplicationError(
                "INVALID_CATALOG",
                "Los metadatos de botella, segmento o perfil DAR no coinciden entre ramales.",
                status_code=500,
                details={"production_id": production_id},
            )
