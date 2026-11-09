"""Human labels for defect codes."""

from __future__ import annotations

from collections.abc import Mapping


def defect_label(code: int | None, labels: Mapping[int, str]) -> str | None:
    if code is None:
        return None
    return labels.get(code, f"Defecto {code}")


def top_defect_label(
    code: int | None, observed_labels: Mapping[int, str], labels: Mapping[int, str]
) -> str | None:
    """Prefer the description seen in the events, then the catalog, then a generic label."""
    if code is None:
        return None
    return observed_labels.get(code) or defect_label(code, labels)


def top_code(minutes_by_code: Mapping[int, float]) -> int | None:
    if not minutes_by_code:
        return None
    return max(minutes_by_code.items(), key=lambda item: item[1])[0]
