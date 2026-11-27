"""Read-only projection of official Gold Live results."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pyarrow.dataset as ds
from pydantic import BaseModel, Field

from app.domain.errors import ApplicationError

from .common import live_epoch
from .lanes import canonical_hour
from .live_hours import AR_COVERAGE_PENDING
from .metrics import Window
from .query import Query
from .scoring import SCORE_COMPONENTS, has_official_total


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
    partial_subtotal: float | None = None
    evaluated_weight: float | None = None
    classification: str | None = None
    model: str | None = None
    contributions: dict[str, float] = Field(default_factory=dict)
    component_states: dict[str, str] = Field(default_factory=dict)


def evaluation_states(contributions, reason):
    codes = set((reason or "").split(";"))
    states = {}
    for component in sorted(SCORE_COMPONENTS):
        family = component if component in {"MIA", "ZC"} else "AR"
        states[component] = (
            "READY"
            if component in contributions
            else "NOT_APPLICABLE"
            if f"{family}_NOT_APPLICABLE" in codes
            else "WAITING_DEPENDENCY"
        )
    return states


class Hour(BaseModel):
    start: datetime
    end: datetime
    state: str
    results: list[Result]


class ActiveProduction(BaseModel):
    line: str
    machine: int
    state: str
    production: str | None = None
    model: str | None = None
    start: datetime | None = None
    zf_production: str | None = None
    zf_model: str | None = None
    zf_ambiguous: bool = False


class LiveScores(BaseModel):
    publication_id: str
    published_at: datetime
    hours: list[Hour]
    mia_provisional: bool = True
    stale: bool
    active_productions: list[ActiveProduction] = Field(default_factory=list)


def query_live(settings, publication_id=None):
    return Query(
        settings.resolved_gold_root,
        publication_id=publication_id,
        max_rows=settings.gold_query_max_rows,
        total_rows=settings.gold_query_total_rows,
    )


def _rows(q, name, expression, pruning, *, pushdown=False):
    return q.snapshot.rows(
        name,
        expression=expression,
        file_pruning_predicate=pruning,
        max_rows=q.max_rows,
        on_scan=q.record_scan,
        pushdown=pushdown,
    )


def live_scores(settings, cache, *, moment=None):
    now = moment or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(settings.gold_live_timezone)).replace(tzinfo=None)
    open_hour = local.replace(minute=0, second=0, microsecond=0)
    q = query_live(settings)
    view = cache.get_or_compute(
        (q.snapshot.publication_id, open_hour),
        lambda: _project(q, open_hour, now, timezone=ZoneInfo(settings.gold_live_timezone)),
    )[0]
    return view.model_copy(update={"stale": (now - view.published_at).total_seconds() > 1200})


def _project(
    q,
    open_hour,
    now,
    *,
    include_contributions=True,
    archive_start=None,
    timezone=None,
    withdrawals=None,
):
    start = archive_start if archive_start is not None else open_hour - timedelta(hours=7)
    field = ds.field
    published_at = datetime.fromisoformat(q.snapshot.manifest["created_at"])
    presence = defaultdict(set)
    at = None
    if timezone is not None and archive_start is None:
        at = published_at.astimezone(timezone).replace(tzinfo=None)
        for row in _rows(
            q,
            "production_assignments",
            (field("vigente_desde") <= at)
            & (field("vigente_hasta").is_null() | (field("vigente_hasta") > at)),
            [("vigente_desde", "<=", at), ("zona", "=", "ZC")],
            pushdown=True,
        ):
            if (
                row["zona"] == "ZC"
                and row["id_asignacion"].startswith("fenix-zc-presence:")
                and row["estado_asignacion"] == "CONFIRMED"
                and row["id_maquina_fenix"] is not None
            ):
                presence[row["linea"], row["id_maquina_fenix"]].add(row["id_produccion"])
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
    active_ids = sorted({identity for values in presence.values() for identity in values})
    window_productions = (field("fecha_inicio_zc") < open_hour) & (
        field("fecha_fin_zf").is_null() | (field("fecha_fin_zf") > start)
    )
    productions = _rows(
        q,
        "productions",
        window_productions | field("id_produccion").cast("string").isin(active_ids)
        if active_ids
        else window_productions,
        [("fecha_inicio_zc", "<", open_hour)]
        if not active_ids
        else [[("fecha_inicio_zc", "<", open_hour)], [("id_produccion", "in", active_ids)]],
    )
    by_id = {p["id_produccion"]: p for p in productions}
    cold = defaultdict(list)
    if at is not None:
        for p in productions:
            began, ended = p.get("fecha_inicio_zf"), p.get("fecha_fin_zf")
            if (
                p.get("estado_dato") == "VALID"
                and p.get("id_maquina_fenix") is not None
                and began is not None
                and began <= at
                and (ended is None or at < ended)
            ):
                cold[p["linea"], p["id_maquina_fenix"]].append(p)
    active = []
    for line, machine in sorted(presence.keys() | cold.keys()):
        identities = presence.get((line, machine), ())
        zf = cold.get((line, machine), ())
        cold_owner = zf[0] if len(zf) == 1 else {}
        identity = next(iter(identities)) if len(identities) == 1 else None
        row = by_id.get(identity)
        active.append(
            ActiveProduction(
                line=line,
                machine=machine,
                state="ambiguous" if len(identities) > 1 else "active" if row else "unavailable",
                production=identity,
                model=row.get("codigo_modelo_botella") if row else None,
                start=row.get("fecha_inicio_operativo_zc") or row.get("fecha_inicio_zc")
                if row
                else None,
                zf_production=cold_owner.get("id_produccion"),
                zf_model=cold_owner.get("codigo_modelo_botella"),
                zf_ambiguous=len(zf) > 1,
            )
        )
    lookup = {r["id_resultado"]: r for r in scores}
    states = {r["scope_id"]: r for r in scopes}
    if withdrawals:
        states.update((r["scope_id"], r) for r in withdrawals)
        for row in withdrawals:
            lookup.pop(row["scope_id"], None)
    floor = live_epoch()
    hours = []
    for i in range((open_hour - start) // timedelta(hours=1)):
        left = start + timedelta(hours=i)
        right = left + timedelta(hours=1)
        before = floor is not None and right <= floor
        results = []
        owners, ambiguous = canonical_hour(productions, left, right) if not before else ({}, {})
        for production in owners.values():
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
            official = score is not None and has_official_total(score.get("formula_version"))
            results.append(
                Result(
                    production=identity,
                    line=production["linea"],
                    machine=production.get("id_maquina_fenix"),
                    start=max(left, began, floor or left),
                    cutoff=cutoff,
                    state=("partial" if reason or not official else "complete")
                    if score
                    else "unavailable"
                    if scope.get("estado") == "READY" and archive_start is not None
                    else "pending",
                    model=production.get("codigo_modelo_botella"),
                    reason=reason,
                    result_id=score["id_resultado"] if score else None,
                    total_penalty=score["total_penalty"] if score and official else None,
                    normalized_score=score["normalized_score"] if score and official else None,
                    partial_subtotal=score["total_penalty"] if score and not official else None,
                    evaluated_weight=score.get("evaluated_weight") if score else None,
                    classification=score["estado_interpretacion"] if score and official else None,
                )
            )
        for (line, machine), candidates in ambiguous.items():
            results.append(
                Result(
                    production="",
                    line=candidates[0].get("linea") or line,
                    machine=machine,
                    start=left,
                    cutoff=None,
                    state="pending",
                    reason="HOURLY_HANDOVER_AMBIGUOUS",
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
        for hour in hours:
            for row in hour.results:
                row.component_states = evaluation_states(row.contributions, row.reason)
    return LiveScores(
        publication_id=q.snapshot.publication_id,
        published_at=published_at,
        hours=hours,
        stale=(now - published_at).total_seconds() > 1200,
        active_productions=active,
    )


def detail(settings, publication, result):
    if len(publication) != 64 or any(c not in "0123456789abcdef" for c in publication):
        raise ApplicationError("GOLD_UNAVAILABLE", "Publicación Gold inválida.", status_code=404)
    q = query_live(settings, publication)
    ensure_hour_not_withdrawn(settings, q, result)
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


def ensure_hour_not_withdrawn(settings, pinned, result):
    latest = query_live(settings)
    if latest.snapshot.publication_id == pinned.snapshot.publication_id:
        return
    scopes = _rows(
        latest,
        "publication_scope_status",
        ds.field("scope_id").cast("string") == result,
        [("scope_id", "=", result)],
    )
    if any(row["reason_code"] == AR_COVERAGE_PENDING for row in scopes):
        raise ApplicationError(
            "RESULT_NOT_FOUND", "Cierre horario pendiente de cobertura AR.", status_code=404
        )


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
