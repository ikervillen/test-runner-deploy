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


def slot_stamp(stamp: str | None) -> str | None:
    """Ten-minute bucket of a filename clock. A date without a clock is not a live slot."""
    if not stamp or len(stamp) != 19 or stamp[10] != "T":
        return None
    minute = int(stamp[14:16]) // 10 * 10
    return f"{stamp[:14]}{minute:02d}:00"


def newest_slot(snapshots) -> list:
    """Files of the newest ten-minute bucket per origin. Older files stay on the backlog."""
    newest: dict[str, str] = {}
    for snap in snapshots:
        slot = slot_stamp(getattr(snap, "producer_time", None))
        if slot is not None and (snap.origin not in newest or slot > newest[snap.origin]):
            newest[snap.origin] = slot
    return [
        snap
        for snap in snapshots
        if slot_stamp(getattr(snap, "producer_time", None)) == newest.get(snap.origin)
    ]


def live_slot_marks(snapshots) -> dict[str, str]:
    """Filename clock of that newest bucket. This is the live signal, not the backlog fecha."""
    marks: dict[str, str] = {}
    for snap in newest_slot(snapshots):
        note_observed_time(marks, snap.origin, snap.producer_time)
    return marks


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
