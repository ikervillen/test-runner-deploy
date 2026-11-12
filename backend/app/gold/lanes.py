"""Hour partitions for the live window and the historical backlog.

Live writes the open clock hour of each production whose accredited cutoff just
moved, and only when that hour sits inside the last eight hours. History writes
one closed hour that is already outside that window. A closed hour is not
rewritten when it leaves the window: it simply stops belonging to live. Totals
that span several hours are a later read of these stored hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .context import stamp
from .coverage import production_scope

HORIZON = timedelta(hours=8)


@dataclass(frozen=True)
class HourChunk:
    """One clock hour of one production. `cutoff` is the accredited instant inside it."""

    production_id: str
    start: datetime
    end: datetime
    cutoff: datetime

    def overlaps(self, row_start: datetime, row_end: datetime | None) -> bool:
        """An interval that crosses the hour is kept whole; the score clips it."""
        if row_start is None:
            return False
        end = row_end if row_end is not None else self.cutoff
        return row_start < self.cutoff and end > self.start


def expected_hours(production: dict, until: datetime) -> list[HourChunk]:
    """Every clock hour of a production through `until`, including a partial last hour."""
    return list(_hours(production, until, until))


def plan(
    productions: list[dict],
    stored_hours: dict[str, dict[datetime, datetime]],
    coverage_rows: list[dict],
    ceiling: datetime,
    lane: str,
    *,
    families: frozenset[str] | None = None,
    reopen: set[tuple[str, datetime]] | None = None,
) -> list[HourChunk] | None:
    """Hour chunks this run may write. None means the manual full build."""
    if lane == "all":
        return None
    if lane not in {"live", "history"}:
        raise ValueError(f"Unknown lane: {lane}")
    reopen = reopen or set()
    accredited = {
        production["id_produccion"]: _accredited(production, coverage_rows, ceiling, families)
        for production in productions
    }
    if lane == "live":
        return _live(productions, stored_hours, accredited, ceiling, reopen)
    return _history(productions, stored_hours, accredited, ceiling, reopen)


def _live(productions, stored_hours, accredited, ceiling, reopen) -> list[HourChunk]:
    open_hours = []
    seen = set()
    for production in productions:
        cutoff = accredited.get(production["id_produccion"])
        chunk = _open_chunk(production, cutoff, ceiling)
        if chunk is not None and _due(stored_hours, chunk, reopen):
            open_hours.append(chunk)
            seen.add((chunk.production_id, chunk.start))
    for production in productions:
        cutoff = accredited.get(production["id_produccion"])
        if cutoff is None:
            continue
        identity = production["id_produccion"]
        for start in stored_hours.get(identity, {}):
            key = (identity, start)
            if key not in reopen or key in seen:
                continue
            end = start + timedelta(hours=1)
            chunk = _clip(production, start, end, min(end, cutoff))
            if chunk is not None and _in_horizon(chunk, ceiling):
                open_hours.append(chunk)
                seen.add(key)
    if open_hours:
        return open_hours
    for production in sorted(productions, key=_started, reverse=True):
        cutoff = accredited.get(production["id_produccion"])
        opened = _open_chunk(production, cutoff, ceiling)
        if cutoff is None or opened is None:
            continue
        cursor = opened.start - timedelta(hours=1)
        while cursor + timedelta(hours=1) > ceiling - HORIZON:
            end = cursor + timedelta(hours=1)
            chunk = _clip(production, cursor, end, min(end, cutoff))
            if chunk is not None and _in_horizon(chunk, ceiling) and _stale(stored_hours, chunk):
                return [chunk]
            cursor -= timedelta(hours=1)
    return []


def _history(productions, stored_hours, accredited, ceiling, reopen) -> list[HourChunk]:
    boundary = ceiling - HORIZON
    chosen = None
    for production in productions:
        cutoff = accredited.get(production["id_produccion"])
        if cutoff is None:
            continue
        identity = production["id_produccion"]
        for start in stored_hours.get(identity, {}):
            if (identity, start) not in reopen:
                continue
            end = start + timedelta(hours=1)
            chunk = _clip(production, start, end, min(end, cutoff, boundary))
            if (
                chunk is not None
                and chunk.end <= boundary
                and (chosen is None or chunk.start > chosen.start)
            ):
                chosen = chunk
    if chosen is not None:
        return [chosen]
    for production in sorted(productions, key=_started, reverse=True):
        cutoff = accredited.get(production["id_produccion"])
        if cutoff is None or _started(production) == datetime.min:
            continue
        cursor = _newest_closed_start(production, cutoff, boundary)
        while cursor + timedelta(hours=1) > _started(production):
            end = cursor + timedelta(hours=1)
            chunk = _clip(production, cursor, end, min(end, cutoff, boundary))
            if chunk is not None and chunk.end <= boundary and _stale(stored_hours, chunk):
                return [chunk]
            cursor -= timedelta(hours=1)
    return []


def _open_chunk(production, cutoff, ceiling) -> HourChunk | None:
    if cutoff is None:
        return None
    start, end = _hour_holding(cutoff)
    chunk = _clip(production, start, end, cutoff)
    if chunk is None or not _in_horizon(chunk, ceiling):
        return None
    return chunk


def _newest_closed_start(production, cutoff, boundary) -> datetime:
    """The newest clock hour whose end is already outside the live window."""
    finished = production.get("fecha_fin_zf")
    finished = stamp(finished) if finished is not None else None
    limit = min(cutoff, finished) if finished is not None else cutoff
    if limit > boundary:
        return _floor_hour(boundary) - timedelta(hours=1)
    return _hour_holding(limit)[0]


def _hours(production: dict, accredited: datetime | None, ceiling: datetime):
    if accredited is None:
        return
    began = _started(production)
    if began == datetime.min:
        return
    cursor = _floor_hour(began)
    limit = min(accredited, production.get("fecha_fin_zf") or accredited, ceiling)
    while cursor < limit:
        end = cursor + timedelta(hours=1)
        cutoff = min(end, limit)
        chunk = _clip(production, cursor, end, cutoff)
        if chunk is not None:
            yield chunk
        cursor = end


def _clip(production, start, end, cutoff) -> HourChunk | None:
    began = _started(production)
    finished = production.get("fecha_fin_zf")
    finished = stamp(finished) if finished is not None else None
    if began == datetime.min or began >= end:
        return None
    if finished is not None and finished <= start:
        return None
    score_cutoff = min(cutoff, finished) if finished is not None else cutoff
    if not start < score_cutoff <= end:
        return None
    return HourChunk(production["id_produccion"], start, end, score_cutoff)


def _hour_holding(cutoff: datetime) -> tuple[datetime, datetime]:
    """The clock hour of the accredited instant. An exact hour closes the previous one."""
    floored = _floor_hour(cutoff)
    if cutoff == floored:
        return floored - timedelta(hours=1), floored
    return floored, floored + timedelta(hours=1)


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _in_horizon(chunk: HourChunk, ceiling: datetime) -> bool:
    return chunk.start < ceiling and chunk.end > ceiling - HORIZON


def _due(stored_hours, chunk: HourChunk, reopen) -> bool:
    return (chunk.production_id, chunk.start) in reopen or _stale(stored_hours, chunk)


def _stale(stored_hours, chunk: HourChunk) -> bool:
    have = stored_hours.get(chunk.production_id, {}).get(chunk.start)
    return have is None or have < chunk.cutoff


def _accredited(production, coverage_rows, ceiling, families):
    options = {"families": families} if families is not None else {}
    _row, cutoff = production_scope(
        production,
        coverage_rows,
        ceiling=ceiling,
        previous=None,
        evidence="lane",
        seen=None,
        **options,
    )
    return cutoff


def _started(production: dict) -> datetime:
    value = production.get("fecha_inicio_operativo_zc") or production.get("fecha_inicio_zc")
    return stamp(value) if value is not None else datetime.min
