"""Explicit Gold application wiring; legacy remains a separate selectable backend."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.errors import register_exception_handlers
from app.api.lifespan import lifespan
from app.api.middleware import AuthenticationMiddleware, RequestContextMiddleware
from app.api.routes import health
from app.api.routes.customer_score import router as score_router
from app.config import Settings
from app.infrastructure.metrics import OperationalMetrics
from app.infrastructure.operations import ScoreQueryExecutor
from app.schemas.api import CatalogResponse
from app.schemas.customer_quality import CustomerQualityRequest, CustomerQualityResponse

from .coverage import coverage_status
from .service import GoldService


class ScoreUseCase:
    def __init__(self, service: GoldService) -> None:
        self.execute = service.score


def create_gold_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="Calidad de producción API",
        version="0.2.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.logger = structlog.get_logger()
    app.state.operational_metrics = OperationalMetrics()
    app.state.score_query_executor = ScoreQueryExecutor(
        max_concurrency=settings.score_query_max_concurrency,
        queue_timeout_seconds=settings.score_query_queue_timeout_seconds,
        execution_timeout_seconds=settings.score_query_timeout_seconds,
        metrics=app.state.operational_metrics,
    )
    service = GoldService(settings)
    app.state.gold_service = service
    app.state.calculate_customer_score = ScoreUseCase(service)
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(score_router, prefix="/api")
    app.add_api_route("/api/metrics", health.metrics, include_in_schema=False)

    @app.get("/api/health")
    async def live() -> dict[str, Any]:
        return {"status": "ok", "data_root_available": settings.resolved_gold_root.is_dir()}

    @app.get("/api/ready")
    async def ready() -> JSONResponse:
        try:
            q = await asyncio.to_thread(service.query)
            return JSONResponse(
                {
                    "status": "ready",
                    "data_version": q.snapshot.publication_id,
                    "missing_resources": [],
                }
            )
        except Exception as exc:
            app.state.logger.warning("gold_readiness_failed", error_type=type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "data_version": None,
                    "missing_resources": ["publicacion Gold compatible"],
                },
            )

    @app.get("/api/catalog", response_model=CatalogResponse)
    async def catalog() -> CatalogResponse:
        result: CatalogResponse = await app.state.score_query_executor.execute(service.catalog)
        return result

    @app.post("/api/customer-quality/query", response_model=CustomerQualityResponse)
    async def quality(payload: CustomerQualityRequest) -> CustomerQualityResponse:
        return await asyncio.to_thread(service.quality, payload)

    @app.get("/api/gold/status")
    async def availability(request: Request) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            q = service.query()
            rows = q.rows("publication_scope_status")
            return {
                "publication_id": q.snapshot.publication_id,
                "created_at": q.snapshot.manifest["created_at"],
                "states": dict(Counter(r["estado"] for r in rows)),
                "pending_reasons": dict(
                    Counter(r["reason_code"] for r in rows if r["reason_code"])
                ),
                "coverage": coverage_status(rows),
            }

        return await asyncio.to_thread(read)

    return app
