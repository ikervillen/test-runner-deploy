from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
from openpyxl import load_workbook

from app.infrastructure.repositories.alert_defect_normalizer import ExcelAlertDefectNormalizer


class ExcelDefectCatalog:
    def __init__(
        self,
        path: Path,
        *,
        severity_path: Path | None = None,
        alerts_root: Path | None = None,
        alert_defect_normalizer: ExcelAlertDefectNormalizer | None = None,
    ) -> None:
        self._path = path.resolve()
        self._severity_path = severity_path.resolve() if severity_path is not None else None
        self._alerts_root = alerts_root.resolve() if alerts_root is not None else None
        self._alert_defect_normalizer = alert_defect_normalizer
        self._version: tuple[int, int, int] | None = None
        self._mia_labels: dict[int, str] = {}
        self._alert_labels: dict[int, str] = {}

    def labels(self) -> dict[int, str]:
        return self.alert_rejection_labels()

    def mia_labels(self) -> dict[int, str]:
        self._refresh()
        return self._mia_labels

    def alert_rejection_labels(self) -> dict[int, str]:
        self._refresh()
        return self._alert_labels

    def _refresh(self) -> None:
        version = self._file_version()
        if self._version == version:
            return
        if version == (0, 0, 0):
            self._version = version
            self._mia_labels = {}
            self._alert_labels = {}
            return

        alert_labels = self._alerts_rejections_labels()
        if self._alert_defect_normalizer is not None:
            alert_labels.update(self._alert_defect_normalizer.normalized_labels())
        else:
            alert_labels.update(self._severity_labels())

        self._version = version
        self._mia_labels = self._cod_def_labels()
        self._alert_labels = alert_labels

    def label(self, code: int | None) -> str:
        if code is None:
            return "Sin dato"
        description = self.labels().get(code)
        return f"{code} - {description}" if description else f"{code} - Sin descripcion en COD_DEF"

    def _file_version(self) -> tuple[int, int, int]:
        catalog_version = self._path.stat().st_mtime_ns if self._path.exists() else 0
        severity_version = (
            self._severity_path.stat().st_mtime_ns
            if self._severity_path is not None and self._severity_path.exists()
            else 0
        )
        alert_file_versions = (
            (path.stat().st_mtime_ns for path in self._alerts_root.rglob("*.parquet"))
            if self._alerts_root is not None and self._alerts_root.exists()
            else ()
        )
        alerts_version = max(alert_file_versions, default=0)
        return catalog_version, severity_version, alerts_version

    def _cod_def_labels(self) -> dict[int, str]:
        if not self._path.exists():
            return {}

        labels: dict[int, str] = {}
        workbook = load_workbook(self._path, read_only=True, data_only=True)
        try:
            worksheet = workbook.worksheets[0]
            for row in worksheet.iter_rows(min_row=2, values_only=True):
                code = _as_int(row[0] if len(row) > 0 else None)
                description = _clean_text(row[1] if len(row) > 1 else None)
                if code is None or not description:
                    continue
                labels[code] = description
        finally:
            workbook.close()
        return labels

    def _severity_labels(self) -> dict[int, str]:
        if self._severity_path is None or not self._severity_path.exists():
            return {}

        labels: dict[int, str] = {}
        workbook = load_workbook(self._severity_path, read_only=True, data_only=True)
        try:
            worksheet = workbook.worksheets[0]
            for row in worksheet.iter_rows(min_row=3, values_only=True):
                old_code = _as_int(row[0] if len(row) > 0 else None)
                old_description = _clean_text(row[1] if len(row) > 1 else None)
                new_code = _as_int(row[2] if len(row) > 2 else None)
                new_description = _clean_text(row[3] if len(row) > 3 else None)
                if old_code is not None and old_description:
                    labels[old_code] = old_description
                if new_code is not None and new_description:
                    labels[new_code] = new_description
        finally:
            workbook.close()
        return labels

    def _alerts_rejections_labels(self) -> dict[int, str]:
        if self._alerts_root is None or not self._alerts_root.exists():
            return {}

        labels: dict[int, str] = {}
        for path in self._alerts_root.rglob("*.parquet"):
            try:
                frame = pl.read_parquet(path, columns=["defect_code", "defect_description"])
            except (OSError, pl.exceptions.PolarsError):
                continue
            for code, description in frame.unique().iter_rows():
                defect_code = _as_int(code)
                label = _clean_text(description)
                if defect_code is not None and label:
                    labels.setdefault(defect_code, label)
        return labels


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value)) if isinstance(value, (int, float, str)) else None
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
