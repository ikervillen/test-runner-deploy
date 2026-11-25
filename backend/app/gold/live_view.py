"""Read-only projection of official Gold Live results."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pyarrow.dataset as ds
from pydantic import BaseModel, Field

from app.domain.errors import ApplicationError

from .common import live_epoch
from .metrics import Window
from .query import Query


class Result(BaseModel):
    production: str
    line: str
    machine: int | None
    start: datetime
    cutoff: datetime | None
    state: str
    reason: str | None = None
    result_id: str | None = None
    total_penalty: float | None = None
    normalized_score: float | None = None
    classification: str | None = None
    model: str | None = None
    contributions: dict[str, float] = Field(default_factory=dict)


class Hour(BaseModel):
    start: datetime
    end: datetime
    state: str
    results: list[Result]


class LiveScores(BaseModel):
    publication_id: str
    published_at: datetime
    hours: list[Hour]
    mia_provisional: bool = True
    stale: bool


def query_live(settings, publication_id=None):
    return Query(
        settings.resolved_gold_root,
        publication_id=publication_id,
        max_rows=settings.gold_query_max_rows,
        total_rows=settings.gold_query_total_rows,
    )


def _rows(q, name, expression, pruning):
    return q.snapshot.rows(
        name,
        expression=expression,
        file_pruning_predicate=pruning,
        max_rows=q.max_rows,
        on_scan=q.record_scan,
    )


def live_scores(settings, cache, *, moment=None):
    now = moment or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(settings.gold_live_timezone)).replace(tzinfo=None)
    open_hour = local.replace(minute=0, second=0, microsecond=0)
    q = query_live(settings)
    view = cache.get_or_compute(
        (q.snapshot.publication_id, open_hour), lambda: _project(q, open_hour, now)
    )[0]
    return view.model_copy(update={"stale": (now - view.published_at).total_seconds() > 1200})


def _project(q, open_hour, now, *, include_contributions=True, archive_start=None):
    start = archive_start if archive_start is not None else open_hour - timedelta(hours=7)
    field = ds.field
    horizon = (field("inicio") >= start) & (field("fin") <= open_hour)
    scores = _rows(
        q,
        "quality_scores",
        horizon
        & (field("tipo_ventana").cast("string") == "HOUR")
        & (field("seleccion_id").cast("string") == "all"),
        [("inicio", ">=", start), ("fin", "<=", open_hour), ("tipo_ventana", "=", "HOUR")],
    )
    scopes = _rows(
        q,
        "publication_scope_status",
        (field("period_start") >= start)
        & (field("period_start") < open_hour)
        & (field("source").cast("string") == "quality_scores"),
        [
            ("period_start", ">=", start),
            ("period_start", "<", open_hour),
            ("source", "=", "quality_scores"),
        ],
    )
    productions = _rows(
        q,
        "productions",
        (field("fecha_inicio_zc") < open_hour)
        & (field("fecha_fin_zf").is_null() | (field("fecha_fin_zf") > start)),
        [("fecha_inicio_zc", "<", open_hour)],
    )
    lookup = {r["id_resultado"]: r for r in scores}
    states = {r["scope_id"]: r for r in scopes}
    floor = live_epoch()
    hours = []
    for i in range((open_hour - start) // timedelta(hours=1)):
        left = start + timedelta(hours=i)
        right = left + timedelta(hours=1)
        before = floor is not None and right <= floor
        results = []
        for production in productions if not before else []:
            identity = production["id_produccion"]
            began = production.get("fecha_inicio_operativo_zc") or production["fecha_inicio_zc"]
            finished = production["fecha_fin_zf"]
            cutoff = min(right, finished) if finished else right
            if began is None or began >= right or cutoff <= left:
                continue
            key = Window(identity, "HOUR", left, right, cutoff).identity
            row, scope = lookup.get(key), states.get(key, {})
            reason = scope.get("reason_code")
            score = (
                row
                if row and row["corte"] == cutoff and scope.get("period_end") == cutoff
                else None
            )
            results.append(
                Result(
                    production=identity,
                    line=production["linea"],
                    machine=production.get("id_maquina_fenix"),
                    start=max(left, began, floor or left),
                    cutoff=score["corte"] if score else None,
                    state=("partial" if reason else "complete")
                    if score
                    else "unavailable"
                    if scope.get("estado") == "READY" and archive_start is not None
                    else "pending",
                    model=production.get("codigo_modelo_botella"),
                    reason=reason,
                    result_id=score["id_resultado"] if score else None,
                    total_penalty=score["total_penalty"] if score else None,
                    normalized_score=score["normalized_score"] if score else None,
                    classification=score["estado_interpretacion"] if score else None,
                )
            )
        hours.append(
            Hour(
                start=left,
                end=right,
                state="before_t0" if before else "closed",
                results=results,
            )
        )
    if archive_start is None:
        hours.append(
            Hour(start=open_hour, end=open_hour + timedelta(hours=1), state="open", results=[])
        )
    if include_contributions:
        results = {
            row.result_id: row
            for hour in hours
            for row in hour.results
            if row.result_id is not None
        }
        if results:
            result_ids = sorted(results)
            components = _rows(
                q,
                "quality_score_components",
                field("id_resultado").cast("string").isin(result_ids),
                [("id_resultado", "in", result_ids)],
            )
            for component in components:
                results[component["id_resultado"]].contributions[component["componente"]] = (
                    component["contribucion"]
                )
    published_at = datetime.fromisoformat(q.snapshot.manifest["created_at"])
    return LiveScores(
        publication_id=q.snapshot.publication_id,
        published_at=published_at,
        hours=hours,
        stale=(now - published_at).total_seconds() > 1200,
    )


def detail(settings, publication, result):
    if len(publication) != 64 or any(c not in "0123456789abcdef" for c in publication):
        raise ApplicationError("GOLD_UNAVAILABLE", "Publicación Gold inválida.", status_code=404)
    q = query_live(settings, publication)
    live = _project(
        q,
        datetime.now(ZoneInfo(settings.gold_live_timezone)).replace(
            tzinfo=None, minute=0, second=0, microsecond=0
        ),
        datetime.now(UTC),
        include_contributions=False,
    )
    if not any(row.result_id == result for hour in live.hours for row in hour.results):
        raise ApplicationError(
            "RESULT_NOT_FOUND", "Resultado horario no disponible.", status_code=404
        )
    return result_details(q, result)


def result_details(q, result):
    return {
        name: _rows(
            q,
            name,
            ds.field("id_resultado").cast("string") == result,
            [("id_resultado", "=", result)],
        )
        for name in (
            "quality_score_components",
            "mia_score_details",
            "aql_score_details",
            "dar_score_details",
            "zc_score_details",
        )
    }
