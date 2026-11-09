from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unicodedata import normalize

from openpyxl import load_workbook


@dataclass(frozen=True, slots=True)
class NormalizedAlertDefect:
    """A Vidrala alert/rejection defect expressed in the common MDL catalog."""

    source_code: int | None
    source_description: str
    code: int | None
    description: str
    severity: str | None
    zone: str | None
    resolution: str


@dataclass(frozen=True, slots=True)
class _MdlDefect:
    old_code: int
    new_code: int
    new_description: str
    severity: str | None
    zone: str | None
    vidrala_description: str
    vidrala_fenix_description: str

    @property
    def aliases(self) -> tuple[str, ...]:
        return (
            self.vidrala_description,
            self.vidrala_fenix_description,
            self.new_description,
        )


class ExcelAlertDefectNormalizer:
    """Converts historical Vidrala alerts/rejections to the common MDL convention.

    The available historical reports use the Vidrala legacy convention.  Therefore
    resolution starts from the legacy code and uses the source description only to
    disambiguate cases where one legacy code was split into several new defects.
    """

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._version: int | None = None
        self._by_old_code: dict[int, tuple[_MdlDefect, ...]] = {}

    def normalize(
        self,
        source_code: int | None,
        source_description: str | None,
    ) -> NormalizedAlertDefect:
        self._refresh()
        description = _clean_text(source_description)
        if source_code is None:
            return NormalizedAlertDefect(
                source_code=None,
                source_description=description,
                code=None,
                description=description or "Sin descripcion",
                severity=None,
                zone=None,
                resolution="missing_source_code",
            )

        candidates = self._by_old_code.get(source_code, ())
        if not candidates:
            return NormalizedAlertDefect(
                source_code=source_code,
                source_description=description,
                code=None,
                description=description or f"Codigo legado {source_code}",
                severity=None,
                zone=None,
                resolution="unmapped_legacy_code",
            )

        selected = _select_candidate(description, candidates)
        if selected is not None:
            return NormalizedAlertDefect(
                source_code=source_code,
                source_description=description,
                code=selected.new_code,
                description=selected.new_description,
                severity=selected.severity,
                zone=selected.zone,
                resolution="normalized_legacy",
            )

        common_severity = _common_value(candidate.severity for candidate in candidates)
        common_zone = _common_value(candidate.zone for candidate in candidates)
        return NormalizedAlertDefect(
            source_code=source_code,
            source_description=description,
            code=None,
            description=description or f"Codigo legado {source_code}",
            severity=common_severity,
            zone=common_zone,
            resolution="ambiguous_legacy_mapping",
        )

    def normalized_labels(self) -> dict[int, str]:
        self._refresh()
        return {
            candidate.new_code: candidate.new_description
            for candidates in self._by_old_code.values()
            for candidate in candidates
        }

    def _refresh(self) -> None:
        version = self._path.stat().st_mtime_ns if self._path.exists() else 0
        if self._version == version:
            return
        if version == 0:
            self._version = version
            self._by_old_code = {}
            return

        by_old_code: dict[int, list[_MdlDefect]] = {}
        workbook = load_workbook(self._path, read_only=True, data_only=True)
        try:
            worksheet = workbook.worksheets[0]
            headers = tuple(
                _normalized_text(value)
                for value in next(worksheet.iter_rows(min_row=3, max_row=3, values_only=True))
            )
            required = {
                "zona": 0,
                "criticidad": 1,
                "nuevo": 2,
                "nuevadescripcion": 3,
                "viejo": 4,
                "descripcionanterior": 5,
                "descripcionfenixmax20caracteres": 6,
            }
            if any(headers[position] != expected for expected, position in required.items()):
                raise ValueError("El catalogo MDL no contiene las columnas esperadas de Vidrala.")

            for row in worksheet.iter_rows(min_row=4, values_only=True):
                old_code = _as_int(row[4] if len(row) > 4 else None)
                new_code = _as_int(row[2] if len(row) > 2 else None)
                if old_code is None or new_code is None:
                    continue
                defect = _MdlDefect(
                    old_code=old_code,
                    new_code=new_code,
                    new_description=_clean_text(row[3] if len(row) > 3 else None),
                    severity=_canonical_severity(row[1] if len(row) > 1 else None),
                    zone=_clean_text(row[0] if len(row) > 0 else None) or None,
                    vidrala_description=_clean_text(row[5] if len(row) > 5 else None),
                    vidrala_fenix_description=_clean_text(row[6] if len(row) > 6 else None),
                )
                if defect.new_description:
                    by_old_code.setdefault(old_code, []).append(defect)
        finally:
            workbook.close()

        self._version = version
        self._by_old_code = {
            code: tuple(sorted(candidates, key=lambda item: item.new_code))
            for code, candidates in by_old_code.items()
        }


def _select_candidate(
    source_description: str,
    candidates: tuple[_MdlDefect, ...],
) -> _MdlDefect | None:
    if len(candidates) == 1:
        return candidates[0]
    if not source_description:
        return None

    scored = sorted(
        (
            (_best_text_match(source_description, candidate.aliases), candidate)
            for candidate in candidates
        ),
        key=lambda item: (-item[0], item[1].new_code),
    )
    best_score, best_candidate = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= 0.9 and best_score - second_score >= 0.1:
        return best_candidate
    return None


def _best_text_match(source: str, aliases: tuple[str, ...]) -> float:
    source_key = _normalized_text(source)
    source_words = set(_word_tokens(source))
    best = 0.0
    for alias in aliases:
        alias_key = _normalized_text(alias)
        alias_words = set(_word_tokens(alias))
        if not source_key or not alias_key:
            continue
        if source_key == alias_key:
            best = max(best, 1.0)
            continue
        if source_key in alias_key or alias_key in source_key:
            best = max(best, 0.95)
            continue
        if source_words and alias_words:
            best = max(best, len(source_words & alias_words) / len(source_words | alias_words))
    return best


def _common_value(values: Any) -> str | None:
    distinct = {value for value in values if value}
    return next(iter(distinct)) if len(distinct) == 1 else None


def _as_int(value: object) -> int | None:
    if value is None or not isinstance(value, int | float | str):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clean_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _canonical_severity(value: object) -> str | None:
    key = _normalized_text(value)
    return {
        "critico": "Critico",
        "grave": "Grave",
        "mayor": "Mayor",
        "menor": "Menor",
        "leve": "Leve",
    }.get(key)


def _normalized_text(value: object) -> str:
    ascii_text = normalize("NFKD", _clean_text(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def _word_tokens(value: object) -> tuple[str, ...]:
    ascii_text = normalize("NFKD", _clean_text(value)).encode("ascii", "ignore").decode("ascii")
    return tuple(token for token in re.findall(r"[a-z0-9]+", ascii_text.lower()) if len(token) > 1)
