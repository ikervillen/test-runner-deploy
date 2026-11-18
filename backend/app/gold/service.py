"""Application use cases for the opt-in Gold backend and bounded custom selections."""

from __future__ import annotations

from collections import defaultdict
from time import perf_counter

from app.domain.errors import ApplicationError
from app.domain.score_config import (
    ScoreConfig,
    apply_score_config_override,
    effective_score_config,
)
from app.infrastructure.cache import LruCache
from app.schemas.customer_quality import CustomerQualityResponse
from app.schemas.customer_score import CustomerScoreResponse

from .catalog import build_catalog
from .common import digest
from .presentation import shift_context
from .query import Query
from .scenarios import DETAILS, recalculate
from .score_view import project
from .scoring import validate_config

__all__ = ["DETAILS", "GoldService", "recalculate"]


def _group(rows, key):
    result = defaultdict(list)
    for row in rows:
        result[row[key]].append(row)
    return result


class GoldService:
    def __init__(self, settings):
        self.settings = settings
        self.cache = LruCache(settings.score_cache_size)
        self.catalog_cache = LruCache(1)

    def query(self):
        if self.settings.gold_history_root is not None:
            from .combined_query import CombinedQuery

            return CombinedQuery(
                self.settings.resolved_gold_root,
                self.settings.gold_history_root.resolve(),
                max_rows=self.settings.gold_query_max_rows,
                total_rows=self.settings.gold_query_total_rows,
            )
        return Query(
            self.settings.resolved_gold_root,
            max_rows=self.settings.gold_query_max_rows,
            total_rows=self.settings.gold_query_total_rows,
        )

    def catalog(self):
        q = self.query()
        return self.catalog_cache.get_or_compute(
            q.snapshot.publication_id,
            lambda: build_catalog(
                q, max_productions_per_query=self.settings.max_productions_per_query
            ),
        )[0]

    def quality(self, payload):
        q = self.query()
        productions = q.select(payload.selection, self.settings.max_productions_per_query)
        return CustomerQualityResponse(
            status="unavailable",
            rows=[
                dict(
                    production_id=p["id_produccion"],
                    label="unknown",
                    relevant_count=0,
                    observed_count=0,
                    excluded_count=0,
                    worst_reason=None,
                    explanation="No hay una fuente actualizada de reclamaciones "
                    "para esta producción.",
                    complaints=[],
                )
                for p in productions
            ],
            validation_rows=[],
            warnings=["La ausencia de reclamaciones disponibles no acredita buena calidad."],
            meta={"publication_id": q.snapshot.publication_id},
        )

    def score(self, payload):
        q = self.query()  # Pin once, before cache identity and all dependent reads.
        key = digest([q.snapshot.publication_id, payload.model_dump(mode="json")])
        started = perf_counter()
        response, hit = self.cache.get_or_compute(key, lambda: self._score(q, payload))
        return response.model_copy(
            update={
                "meta": response.meta.model_copy(
                    update={"cache_hit": hit, "query_time_ms": (perf_counter() - started) * 1000}
                )
            }
        )

    def _resolve_config(self, q, payload, own, state):
        """Effective configuration for one production, with the user's override applied."""
        config_id = next(
            (r["config_id"] for r in own if r["tipo_ventana"] == "PRODUCTION"),
            state["config_id"],
        )
        stored = q.config(config_id)
        override = (
            payload.score_config_override.model_dump(exclude_none=True)
            if payload.score_config_override
            else None
        )
        config = dict(
            apply_score_config_override(
                ScoreConfig(stored["score"]["version"], stored["score"]), override
            ).raw
        )
        try:
            validate_config(config)
        except (ValueError, ApplicationError) as exc:
            if payload.score_config_override:
                raise ApplicationError(
                    "INVALID_SCORE_PARAMETERS", "Los parámetros del score no son válidos."
                ) from exc
            raise
        return config, stored

    def _score(self, q, payload):
        productions = q.select(payload.selection, self.settings.max_productions_per_query)
        ids = [p["id_produccion"] for p in productions]
        selected = _group(q.rows("quality_scores", ids), "id_produccion")
        states = {r["id_produccion"]: r for r in q.rows("production_calculation_state", ids)}
        mappings = {r["codigo_modelo_botella"]: r for r in q.rows("model_segment_catalog")}
        scopes = q.rows("publication_scope_status", ids)
        scopes_by_production = _group(scopes, "id_produccion")
        assignments = _group(q.rows("production_assignments", ids), "id_produccion")
        result_ids = [r["id_resultado"] for rows in selected.values() for r in rows]
        tables = {name: q.rows(name, result_ids, "id_resultado") for name in DETAILS}
        tables.update(
            {name: q.rows(name, ids) for name in ("ar_window_bases", "ar_window_exposures")}
        )
        physical = payload.selection.filters.model_dump(exclude_defaults=True)
        output, pending = [], []
        effective, version = {}, 0
        for p in productions:
            identity = p["id_produccion"]
            own = selected.get(identity, [])
            config, stored = self._resolve_config(q, payload, own, states[identity])
            effective = effective_score_config(ScoreConfig(config["version"], config))
            version = config["version"]
            valid = {
                f"{p['linea']}-{a['ramal']}"
                for a in assignments.get(identity, [])
                if a["zona"] == "ZF" and a["ramal"] is not None
            }
            branches = set(payload.selection.branches) & valid
            custom = bool(
                payload.score_config_override
                or any(physical.values())
                or (branches and branches != valid)
            )
            if custom:
                own, local, reasons, scenario = self._scenario(
                    q, p, own, payload, config, stored, mappings, scopes, tables, branches, valid
                )
            else:
                scenario, local, reasons = q, tables, []
            full = next((r for r in own if r["tipo_ventana"] == "PRODUCTION"), None)
            if full is None:
                reasons += [
                    reason
                    for s in scopes_by_production.get(identity, [])
                    for reason in (s["reason_code"] or "").split(";")
                    if reason
                ]
                pending.append(
                    dict(production_id=identity, reasons=sorted(set(reasons or ["SCORE_PENDING"])))
                )
                continue
            view_config = config if custom else None
            row = project(scenario, full, p, local, config=view_config)
            shifts = [
                {**shift_context(shift), **project(scenario, shift, p, local, config=view_config)}
                for shift in own
                if shift["tipo_ventana"] == "SHIFT"
            ]
            output.append(dict(production_id=identity, line=p["linea"], **row, shift_scores=shifts))
        output.sort(key=lambda r: (-r["total_penalty"], r["production_id"]))
        for child in q.forks.values():
            q.files.update(child.files)
        period_ends = [s["period_end"] for s in scopes if s["period_end"]]
        return CustomerScoreResponse(
            status="pending" if not output else "partial" if pending else "completed",
            productions=output,
            pending_productions=pending,
            warnings=[
                "Los filtros físicos afectan a MIA; A/R y ZC conservan el ámbito de producción."
            ]
            if any(payload.selection.filters.model_dump().values())
            else [],
            meta=dict(
                query_time_ms=0,
                config_version=version,
                files_read=len(q.files),
                rows_scanned=q.scanned,
                effective_config=effective,
                publication_id=q.snapshot.publication_id,
                source="gold",
                cutoff=max(period_ends).isoformat() if period_ends else None,
            ),
        )

    @staticmethod
    def _scenario(q, p, own, payload, config, stored, mappings, scopes, tables, branches, valid):
        """Recalculate against the publication that produced the stored score."""
        basis = next(
            (r.get("basis_publication_id") for r in own if r["tipo_ventana"] == "PRODUCTION"),
            None,
        )
        scenario = q.fork(basis)
        production, scenario_mappings, scenario_scopes = p, mappings, scopes
        if scenario is not q:
            production = scenario.rows("productions", [p["id_produccion"]])[0]
            scenario_mappings = {
                r["codigo_modelo_botella"]: r for r in scenario.rows("model_segment_catalog")
            }
            scenario_scopes = scenario.rows("publication_scope_status", [p["id_produccion"]])
        own, extra, reasons = recalculate(
            scenario,
            production,
            payload,
            config,
            stored["catalogs"],
            scenario_mappings,
            scenario_scopes,
            branches,
            valid,
        )
        return own, {**tables, **extra}, reasons, scenario
