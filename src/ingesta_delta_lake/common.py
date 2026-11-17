"""Identidades deterministas y errores operativos del proceso."""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


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


def plant_now() -> datetime:
    """Filename clocks are plant-local. The live slot is that clock, not UTC."""
    zone = ZoneInfo(os.environ.get("INGESTA_SOURCE_TIMEZONE", "Europe/Madrid"))
    return datetime.now(zone).replace(tzinfo=None)


def live_window(moment: datetime) -> tuple[str, str]:
    """The ten minutes the live phase owns.

    On a tick, 09:30:00, that is the slot that just closed: 09:20–09:30.
    Between ticks, 09:37, it is the slot that contains the clock: 09:30–09:40.
    Every family and every line uses this same pair.
    """
    floored = moment.replace(minute=(moment.minute // 10) * 10, second=0, microsecond=0)
    on_tick = moment.second == 0 and moment.microsecond == 0 and moment.minute % 10 == 0
    start = floored - timedelta(minutes=10) if on_tick else floored
    end = start + timedelta(minutes=10)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def next_live_tick(moment: datetime) -> datetime:
    """The next 10-minute boundary. A moment already on the boundary is that tick."""
    floored = moment.replace(minute=(moment.minute // 10) * 10, second=0, microsecond=0)
    if moment.replace(microsecond=0) == floored and moment.microsecond == 0:
        return floored
    return floored + timedelta(minutes=10)


def history_yields(moment: datetime | None = None) -> bool:
    """History releases the disk 20 seconds before the live tick owns it."""
    moment = moment or plant_now()
    return next_live_tick(moment) - moment <= timedelta(seconds=20)


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


LIVE_SLOT_FAMILIES = ("LISTA_DEFECTOS", "LISTA_RECH", "CONT_ZC")


def _family_captures(control_root, family: str):
    path = control_root / f"control-{family.lower()}.sqlite"
    if not path.exists():
        return
    uri = path.as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return
    with closing(connection) as db:
        db.execute("PRAGMA query_only=ON")
        for table in ("input_versions", "capture_cache"):
            try:
                rows = db.execute(f"SELECT descriptor_json FROM {table}").fetchall()
            except sqlite3.OperationalError:
                continue
            for (raw,) in rows:
                item = json.loads(raw)
                yield item.get("origin"), item.get("producer_time")


def shared_bronze_window(control_root):
    """The last ten-minute slot Bronze has copied, shared by every family and line.

    The slot is the filename clock of that copy. It is never the wall clock and
    never ten minutes past the newest file.
    """
    slot = None
    origins: set[str] = set()
    for family in LIVE_SLOT_FAMILIES:
        for origin, stamp in _family_captures(control_root, family):
            if origin:
                origins.add(origin)
            bucket = slot_stamp(stamp)
            if bucket and (slot is None or bucket > slot):
                slot = bucket
    if slot is None:
        return None, origins
    start = datetime.fromisoformat(slot)
    end = (start + timedelta(minutes=10)).isoformat(timespec="seconds")
    return (slot, end), origins


def split_live_backlog(pending, bronze, window: tuple[str, str], origins=None):
    """Live is that one Bronze slot. Older copies, including October, are backlog."""
    start, end = window
    live = [
        snap
        for snap in pending
        if (slot := slot_stamp(getattr(snap, "producer_time", None))) is not None
        and start <= slot < end
    ]
    live_shas = {(snap.origin, snap.sha256) for snap in live}
    # A repeated file is the same observation, even when its name has no clock.
    live = [snap for snap in pending if (snap.origin, snap.sha256) in live_shas]
    backlog = [snap for snap in pending if (snap.origin, snap.sha256) not in live_shas]
    if origins is None:
        origins = {snap.origin for snap in bronze if getattr(snap, "origin", None)}
    marks = {origin: start for origin in origins}
    return live, backlog, marks


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
