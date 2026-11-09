from __future__ import annotations

import structlog
from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.lifespan import lifespan
from app.api.middleware import AuthenticationMiddleware, RequestContextMiddleware
from app.api.routes import (
    catalog,
    customer_quality,
    customer_score,
    health,
)
from app.application.catalog import GetCatalog
from app.application.customer_quality import CalculateCustomerQuality
from app.application.customer_score import CalculateCustomerScore
from app.config import Settings, get_settings
from app.infrastructure.cache import LruCache
from app.infrastructure.metrics import OperationalMetrics
from app.infrastructure.operations import ScoreQueryExecutor
from app.infrastructure.repositories.alert_defect_normalizer import ExcelAlertDefectNormalizer
from app.infrastructure.repositories.catalog import CsvCatalogRepository
from app.infrastructure.repositories.customer_quality import StaticCustomerQualityRepository
from app.infrastructure.repositories.defect_catalog import ExcelDefectCatalog
from app.infrastructure.repositories.metadata import ParquetMetadataRepository
from app.infrastructure.repositories.segment_dar_profiles import JsonSegmentDarProfileRepository
from app.infrastructure.scoring import PolarsCustomerScoreRepository
from app.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    if resolved_settings.data_backend == "gold":
        from app.gold.api import create_gold_app

        return create_gold_app(resolved_settings)

    catalog_repository = CsvCatalogRepository(
        resolved_settings.data_root,
        resolved_settings.manifest_path,
    )
    metadata_repository = ParquetMetadataRepository()
    alert_defect_normalizer = ExcelAlertDefectNormalizer(
        resolved_settings.resolved_alert_defect_normalization_path
    )
    defect_catalog = ExcelDefectCatalog(
        resolved_settings.resolved_defect_catalog_path,
        severity_path=resolved_settings.resolved_defect_severity_path,
        alerts_root=resolved_settings.resolved_alerts_rejections_root,
        alert_defect_normalizer=alert_defect_normalizer,
    )
    mia_defect_labels = defect_catalog.mia_labels()
    alert_defect_labels = defect_catalog.alert_rejection_labels()
    customer_quality_repository = StaticCustomerQualityRepository()
    segment_dar_profiles = JsonSegmentDarProfileRepository(
        resolved_settings.resolved_segment_dar_profiles_path
    )
    customer_score_repository = PolarsCustomerScoreRepository(
        resolved_settings.resolved_customer_score_config_path,
        resolved_settings.resolved_defect_severity_path,
        resolved_settings.resolved_alerts_rejections_root,
        resolved_settings.resolved_inspected_totals_root,
        resolved_settings.resolved_hot_end_yield_path,
        mia_defect_labels,
        alert_defect_labels=alert_defect_labels,
        alert_defect_normalizer=alert_defect_normalizer,
        segment_dar_profiles=segment_dar_profiles,
        quality_aggregate_root=resolved_settings.resolved_quality_aggregate_root,
        data_root=resolved_settings.data_root,
    )

    app = FastAPI(
        title="Calidad de producción API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.logger = structlog.get_logger()
    app.state.operational_metrics = OperationalMetrics()
    app.state.score_query_executor = ScoreQueryExecutor(
        max_concurrency=resolved_settings.score_query_max_concurrency,
        queue_timeout_seconds=resolved_settings.score_query_queue_timeout_seconds,
        execution_timeout_seconds=resolved_settings.score_query_timeout_seconds,
        metrics=app.state.operational_metrics,
    )
    app.state.get_catalog = GetCatalog(
        catalog_repository,
        metadata_repository,
        resolved_settings.max_productions_per_query,
    )
    app.state.calculate_customer_quality = CalculateCustomerQuality(
        catalog_repository,
        customer_quality_repository,
        resolved_settings.max_productions_per_query,
    )
    app.state.calculate_customer_score = CalculateCustomerScore(
        catalog_repository,
        customer_score_repository,
        resolved_settings.max_productions_per_query,
        LruCache(resolved_settings.score_cache_size),
        resolved_settings.data_version_token,
    )

    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router, prefix="/api")
    app.include_router(catalog.router, prefix="/api")
    app.include_router(customer_quality.router, prefix="/api")
    app.include_router(customer_score.router, prefix="/api")
    return app
