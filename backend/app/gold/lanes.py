"""Hour partitions for the live window and the historical backlog.

Live copies the new ten-minute slot into the open hour and does not score it.
The score is written once, when that clock hour closes. History writes one
closed hour that is already outside the last eight hours. A late stop reopens
only the hour it overlaps. Totals that span several hours are a later read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .context import stamp
from .coverage import production_scope

HORIZON = timedelta(hours=8)


@dataclass(frozen=True)
class HourChunk:
    """One clock hour of one production. `cutoff` is the accredited instant inside it.

    `facts_only` copies rows and does not score. `read_from` is the slot start;
    rows at or before it were already copied.
    """

    production_id: str
    start: datetime
    end: datetime
    cutoff: datetime
    facts_only: bool = False
    read_from: datetime | None = None

    def overlaps(self, row_start: datetime, row_end: datetime | None) -> bool:
        """An interval that crosses the requested window is kept whole."""
        if row_start is None:
            return False
        end = row_end if row_end is not None else self.cutoff
        return row_start < self.cutoff and end > (self.read_from or self.start)


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
    since: datetime | None = None,
    copied_until: dict[str, datetime] | None = None,
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
        accredited = {
            production["id_produccion"]: _accredited(
                production, coverage_rows, ceiling, families, "live_until"
            )
            for production in productions
        }
        return _live(
            productions, stored_hours, accredited, ceiling, reopen, since, copied_until or {}
        )
    return _history(productions, stored_hours, accredited, ceiling, reopen)


def _live(productions, stored_hours, accredited, ceiling, reopen, since, copied) -> list[HourChunk]:
    """Copy the new slot, or score the hour that just closed. Never walk older hours."""
    chunks = []
    seen = set()
    for production in productions:
        cutoff = accredited.get(production["id_produccion"])
        if cutoff is None or (since is not None and cutoff <= since):
            continue
        identity = production["id_produccion"]
        for start in stored_hours.get(identity, {}):
            key = (identity, start)
            if key not in reopen or key in seen:
                continue
            end = start + timedelta(hours=1)
            chunk = _clip(production, start, end, min(end, cutoff))
            if chunk is not None and _in_horizon(chunk, ceiling) and end > (since or start):
                chunks.append(chunk)
                seen.add(key)
        start, end = _hour_holding(cutoff)
        if (identity, start) in seen:
            continue
        if cutoff == end:
            chunk = _clip(production, start, end, cutoff)
            if chunk is not None and _in_horizon(chunk, ceiling) and _stale(stored_hours, chunk):
                left = max(start, since) if since is not None else start
                chunks.append(
                    HourChunk(
                        identity,
                        start,
                        end,
                        cutoff,
                        read_from=None if left == start else left,
                    )
                )
            continue
        copied_at = copied.get(identity)
        left = max(start, copied_at or start, since or start)
        if left >= cutoff:
            continue
        chunk = _clip(production, start, end, cutoff)
        if chunk is not None and _in_horizon(chunk, ceiling):
            chunks.append(HourChunk(identity, start, end, cutoff, facts_only=True, read_from=left))
    return chunks


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


def _stale(stored_hours, chunk: HourChunk) -> bool:
    have = stored_hours.get(chunk.production_id, {}).get(chunk.start)
    return have is None or have < chunk.cutoff


def _accredited(production, coverage_rows, ceiling, families, until_field="covered_until"):
    options = {"families": families} if families is not None else {}
    options["until_field"] = until_field
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
