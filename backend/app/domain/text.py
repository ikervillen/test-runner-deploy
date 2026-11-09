"""Accent-insensitive text helpers shared by catalog readers in every backend."""

from __future__ import annotations

from typing import Any
from unicodedata import normalize

_SEVERITY_BY_KEY = {
    "critico": "Critico",
    "crtico": "Critico",
    "grave": "Grave",
    "mayor": "Mayor",
    "menor": "Menor",
    "leve": "Leve",
}


def normalize_text(value: Any) -> str:
    """Lower-case ASCII with hyphens treated as spaces and whitespace collapsed."""
    ascii_text = normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_text.lower().replace("-", " ").split())


def canonical_severity(value: Any) -> str | None:
    """Map a free-text severity cell to one of the configured severity names."""
    return _SEVERITY_BY_KEY.get(normalize_text(value))
