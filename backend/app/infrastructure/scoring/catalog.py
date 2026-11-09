"""Defect severity workbook: severity per code and legacy/new DAR code pairs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.domain.score_config import as_int
from app.domain.text import canonical_severity, normalize_text

# Column positions in the severity workbook (header rows 1-2, data from row 3).
_OLD_CODE, _OLD_DESCRIPTION, _NEW_CODE, _NEW_DESCRIPTION, _SEVERITY = 0, 1, 2, 3, 10

DAR_SEGMENT_GROUPS: dict[str, tuple[str, ...]] = {
    "Ovalizacion": ("ovalizacion", "ovalidad", "ovalada", "ovality", "oval"),
    "Verticalidad": ("verticalidad", "cuello torcido"),
    "Corcho: Rechupe int. 45 mm": ("rechupe",),
    "Rosca: Line over": ("line over", "lineover", "line-over"),
}


@dataclass(frozen=True, slots=True)
class DarCodePair:
    group: str
    old_code: int | None
    new_code: int | None


def dar_group_from_description(description: str) -> str | None:
    """Text fallback used when a production has no segment DAR profile."""
    searchable = normalize_text(description)
    for group, terms in DAR_SEGMENT_GROUPS.items():
        if any(normalize_text(term) in searchable for term in terms):
            return group
    return None


class SeverityWorkbook:
    """Read-only view of the severity workbook; results are cached per instance."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._severity_by_code: dict[int, str] | None = None
        self._dar_pairs: tuple[DarCodePair, ...] | None = None

    def severity_by_code(self) -> dict[int, str]:
        if self._severity_by_code is None:
            by_code: dict[int, str] = {}
            for row in self._rows():
                severity = canonical_severity(_cell(row, _SEVERITY))
                if severity is None:
                    continue
                for value in (_cell(row, _OLD_CODE), _cell(row, _NEW_CODE)):
                    code = as_int(value)
                    if code is not None:
                        by_code[code] = severity
            self._severity_by_code = by_code
        return self._severity_by_code

    def dar_code_groups(self, *, use_new_codes: bool) -> dict[str, tuple[int, ...]]:
        groups: dict[str, set[int]] = defaultdict(set)
        for pair in self.dar_code_pairs():
            code = pair.new_code if use_new_codes else pair.old_code
            if code is not None:
                groups[pair.group].add(code)
        return {group: tuple(sorted(codes)) for group, codes in groups.items() if codes}

    def dar_code_pairs(self) -> tuple[DarCodePair, ...]:
        if self._dar_pairs is None:
            pairs: set[DarCodePair] = set()
            for row in self._rows():
                searchable = normalize_text(
                    f"{_cell(row, _OLD_DESCRIPTION)} {_cell(row, _NEW_DESCRIPTION)}"
                )
                for group, terms in DAR_SEGMENT_GROUPS.items():
                    if any(normalize_text(term) in searchable for term in terms):
                        pairs.add(
                            DarCodePair(
                                group, as_int(_cell(row, _OLD_CODE)), as_int(_cell(row, _NEW_CODE))
                            )
                        )
            self._dar_pairs = tuple(sorted(pairs, key=lambda p: (p.group, p.old_code or -1)))
        return self._dar_pairs

    def _rows(self) -> Iterator[tuple[Any, ...]]:
        if not self._path.exists():
            return
        workbook = load_workbook(self._path, read_only=True, data_only=True)
        try:
            yield from workbook.worksheets[0].iter_rows(min_row=3, values_only=True)
        finally:
            workbook.close()


def _cell(row: tuple[Any, ...], index: int) -> Any:
    return row[index] if len(row) > index else None
