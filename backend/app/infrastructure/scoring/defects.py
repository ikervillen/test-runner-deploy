"""Resolve alert/rejection defects to catalog code, severity and DAR group."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import polars as pl

from app.domain.models import Production
from app.domain.score_config import as_int
from app.infrastructure.repositories.alert_defect_normalizer import (
    ExcelAlertDefectNormalizer,
    NormalizedAlertDefect,
)
from app.infrastructure.repositories.segment_dar_profiles import JsonSegmentDarProfileRepository

from .catalog import SeverityWorkbook, dar_group_from_description
from .labels import defect_label

# Columns added by `annotate` so shift scorers do not re-normalize every event.
CODE_COLUMN = "_normalized_code"
DESCRIPTION_COLUMN = "_normalized_description"
SEVERITY_COLUMN = "_normalized_severity"
DAR_GROUP_COLUMN = "_dar_group"


@dataclass(frozen=True, slots=True)
class ResolvedDefect:
    code: int | None
    description: str
    severity: str | None


class AlertDefectResolver:
    def __init__(
        self,
        *,
        normalizer: ExcelAlertDefectNormalizer | None,
        severity_workbook: SeverityWorkbook,
        alert_labels: Mapping[int, str],
        segment_dar_profiles: JsonSegmentDarProfileRepository | None,
    ) -> None:
        self._normalizer = normalizer
        self._severity_workbook = severity_workbook
        self._alert_labels = alert_labels
        self._segment_dar_profiles = segment_dar_profiles

    def resolve(self, source_code: int | None, description: object) -> ResolvedDefect:
        """Catalog code (falling back to the source code), description and severity."""
        normalized = self._normalize(source_code, str(description or ""))
        code = normalized.code if normalized.code is not None else source_code
        return ResolvedDefect(code, normalized.description, normalized.severity)

    def dar_group(self, production: Production, code: int | None, description: str) -> str | None:
        if self._segment_dar_profiles is None:
            return dar_group_from_description(description)
        return self._segment_dar_profiles.profile_for(production).group_for_code(code)

    def annotate(self, production: Production, events: pl.DataFrame) -> pl.DataFrame:
        """Add resolved code, description, severity and DAR group columns to alert events."""
        if events.height == 0:
            return events
        codes: list[int | None] = []
        descriptions: list[str] = []
        severities: list[str | None] = []
        groups: list[str | None] = []
        for row in events.iter_rows(named=True):
            resolved = self.resolve(as_int(row.get("defect_code")), row.get("defect_description"))
            codes.append(resolved.code)
            descriptions.append(resolved.description)
            severities.append(resolved.severity)
            groups.append(self.dar_group(production, resolved.code, resolved.description))
        return events.with_columns(
            pl.Series(CODE_COLUMN, codes, dtype=pl.Int64),
            pl.Series(DESCRIPTION_COLUMN, descriptions, dtype=pl.Utf8),
            pl.Series(SEVERITY_COLUMN, severities, dtype=pl.Utf8),
            pl.Series(DAR_GROUP_COLUMN, groups, dtype=pl.Utf8),
        )

    def _normalize(self, source_code: int | None, description: str) -> NormalizedAlertDefect:
        if self._normalizer is not None:
            return self._normalizer.normalize(source_code, description)
        clean = description.strip()
        severity_by_code = self._severity_workbook.severity_by_code()
        return NormalizedAlertDefect(
            source_code=source_code,
            source_description=clean,
            code=source_code,
            description=clean or defect_label(source_code, self._alert_labels) or "",
            severity=severity_by_code.get(source_code) if source_code is not None else None,
            zone=None,
            resolution="legacy_fallback",
        )
