"""Archive view of retained Gold Live Delta rows, with no data copying or score writes."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pyarrow.dataset as ds

from app.domain.errors import ApplicationError
from app.domain.shifts import SHIFT_HOURS, SHIFT_STARTS

from .common import digest, live_epoch, read_json
from .live_view import _project, _rows, query_live, result_details

PAGE_SHIFTS = 6
ARCHIVE_AGE = timedelta(hours=8)
HOUR = timedelta(hours=1)


def shift_start(stamp):
    for _, hour in reversed(SHIFT_STARTS):
        if stamp.hour >= hour:
            return stamp.replace(hour=hour, minute=0, second=0, microsecond=0)
    return (stamp - timedelta(days=1)).replace(
        hour=SHIFT_STARTS[-1][1], minute=0, second=0, microsecond=0
    )


def eligible_cutoff(settings, now):
    return (
        (now - ARCHIVE_AGE)
        .astimezone(ZoneInfo(settings.gold_live_timezone))
        .replace(tzinfo=None, minute=0, second=0, microsecond=0)
    )


def archive(settings, cache, before=None, *, moment=None, publication_id=None):
    now = moment or datetime.now(UTC)
    floor = live_epoch()
    if floor is None:
        raise ApplicationError(
            "LIVE_ARCHIVE_UNAVAILABLE", "T0 de Live no disponible.", status_code=503
        )
    cutoff = eligible_cutoff(settings, now)
    if before is not None and (before.tzinfo is not None or before != shift_start(before)):
        raise ApplicationError(
            "INVALID_ARCHIVE_CURSOR", "Seleccione un inicio de turno.", status_code=422
        )
    end = min(cutoff, before) if before else cutoff
    if publication_id is not None and not valid_id(publication_id):
        raise ApplicationError(
            "INVALID_ARCHIVE_PUBLICATION", "Publicación inválida.", status_code=422
        )
    q = query_live(settings, publication_id)

    def page():
        previous = q.snapshot.manifest.get("previous_publication_id")
        if end <= floor:
            return dict(
                t0=floor,
                eligible_until=cutoff,
                publication=q.snapshot.publication_id,
                previous_publication=previous,
                shifts=[],
                next_before=None,
            )
        span = timedelta(hours=SHIFT_HOURS)
        last = shift_start(end - timedelta(microseconds=1))
        first = max(shift_start(floor), last - (PAGE_SHIFTS - 1) * span)
        projected = _project(q, end, now, archive_start=first)
        by_start = {hour.start: hour for hour in projected.hours}
        shifts = []
        start = first
        while start <= last:
            stop = start + span
            hours = [
                by_start[stamp]
                for i in range(SHIFT_HOURS)
                if (stamp := start + i * HOUR) in by_start
            ]
            shifts.append(
                dict(
                    start=start,
                    end=stop,
                    name=next(name for name, hour in SHIFT_STARTS if hour == start.hour),
                    partial_t0=start < floor < stop,
                    accruing=stop > cutoff,
                    hours=[hour.model_dump() for hour in hours],
                )
            )
            start = stop
        return dict(
            t0=floor,
            eligible_until=cutoff,
            publication=q.snapshot.publication_id,
            previous_publication=previous,
            shifts=shifts,
            next_before=first if first > shift_start(floor) else None,
        )

    return cache.get_or_compute(("archive", q.snapshot.publication_id, end, cutoff, floor), page)[0]


def valid_id(value):
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "abcdef0123456789" for c in value)
    )


def archived_result(settings, publication, result, *, moment=None):
    for value in (publication, result):
        if not valid_id(value):
            raise ApplicationError(
                "RESULT_NOT_FOUND", "Resultado archivado no disponible.", status_code=404
            )
    floor = live_epoch()
    now = moment or datetime.now(UTC)
    q = query_live(settings, publication)
    field = ds.field
    scores = _rows(
        q,
        "quality_scores",
        field("id_resultado").cast("string") == result,
        [("id_resultado", "=", result)],
    )
    if (
        len(scores) != 1
        or floor is None
        or scores[0]["fin"] <= floor
        or scores[0]["fin"] > eligible_cutoff(settings, now)
        or scores[0]["tipo_ventana"] != "HOUR"
        or scores[0]["seleccion_id"] != "all"
    ):
        raise ApplicationError(
            "RESULT_NOT_FOUND", "Resultado archivado no disponible.", status_code=404
        )
    score = scores[0]
    scope = _rows(
        q,
        "publication_scope_status",
        field("scope_id").cast("string") == result,
        [("scope_id", "=", result)],
    )
    if not any(
        row["source"] == "quality_scores" and row["period_end"] == score["corte"] for row in scope
    ):
        raise ApplicationError("RESULT_NOT_FOUND", "Cierre horario sin acreditar.", status_code=404)
    ref = score["reference_id"]
    references = {
        name: _rows(
            q, name, field("reference_id").cast("string") == ref, [("reference_id", "=", ref)]
        )
        if ref
        else []
        for name in ("mia_reference_sets", "mia_reference_values", "mia_reference_members")
    }
    config = score["config_id"]
    # Configs are immutable and addressed by the effective mathematical hash.
    config_path = q.snapshot.root / "configs" / f"{config}.json"
    configuration = read_json(config_path) if valid_id(config) and config_path.is_file() else None
    if (
        configuration is not None
        and digest([configuration.get("score"), configuration.get("catalogs")]) != config
    ):
        configuration = None
    details = result_details(q, result)
    missing = []
    if configuration is None:
        missing.append("configuration")
    if ref and not references["mia_reference_sets"]:
        missing.append("mia_reference_sets")
    if not details["quality_score_components"]:
        missing.append("quality_score_components")
    return {
        "publication": q.snapshot.publication_id,
        "score": score,
        "coverage": scope,
        "configuration": configuration,
        "mia_references": references,
        "details": details,
        "missing": missing,
    }
