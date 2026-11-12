"""Identidades deterministas y errores operativos del proceso."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any


class IngestaError(Exception):
    """El proceso debe detenerse sin inventar un resultado satisfactorio."""


class ConfigurationError(IngestaError):
    pass


class IntegrityError(IngestaError):
    pass


class CaptureError(IngestaError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


def blank_input(data: bytes) -> bool:
    """Empty or NUL-only file. It carries no row and must not open a review."""
    return not data.strip(b"\x00\t\r\n ")


def note_observed_time(marks: dict[str, str], origin: str, stamp: str | None) -> None:
    """Keep the latest local filename clock. A date without a clock is not a cutoff."""
    if not origin or not stamp or len(stamp) != 19 or stamp[10] != "T":
        return
    current = marks.get(origin)
    if current is None or stamp > current:
        marks[origin] = stamp


def horizon_marks(observed: dict, inserts, *, wrote: bool) -> dict[str, str]:
    """Blank files and rows actually written. A rejected row does not move the horizon."""
    marks = dict(observed or {})
    if not wrote:
        return marks
    for row in inserts:
        fecha = row.get("FECHA")
        origin = row.get("origen_fenix")
        if origin and hasattr(fecha, "strftime"):
            note_observed_time(marks, origin, fecha.strftime("%Y-%m-%dT%H:%M:%S"))
    return marks


def parse_int64(token: str) -> int:
    """Entero decimal exacto; sin float ni conversión de códigos a categorías."""
    if not re.fullmatch(r"[+-]?[0-9]+", token.strip()):
        raise ValueError("Se esperaba un entero decimal")
    # Limitar antes de int() también evita números de miles de dígitos.
    digits = token.strip().lstrip("+-").lstrip("0") or "0"
    if len(digits) > 19:
        raise ValueError("Entero fuera del rango int64")
    value = int(("-" if token.strip().startswith("-") else "") + digits)
    if not -(2**63) <= value < 2**63:
        raise ValueError("Entero fuera del rango int64")
    return value
