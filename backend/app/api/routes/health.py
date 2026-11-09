from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.auth import require_role
from app.schemas.api import HealthResponse, ReadinessResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        data_root_available=settings.data_root.is_dir() and settings.manifest_path.is_file(),
    )


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(request: Request) -> ReadinessResponse | JSONResponse:
    settings = request.app.state.settings
    missing = [label for label, path in settings.required_paths().items() if not path.exists()]
    data_version = _data_version(settings.manifest_path)
    payload = ReadinessResponse(
        status="ready" if not missing else "degraded",
        data_version=data_version,
        missing_resources=missing,
    )
    if not missing:
        return payload
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(mode="json"),
    )


def _data_version(manifest_path: Path) -> str | None:
    try:
        modified = manifest_path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(modified, tz=UTC).isoformat()


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> PlainTextResponse:
    require_role(request, request.app.state.settings.auth_metrics_role)
    rendered = request.app.state.operational_metrics.render_prometheus()
    if request.app.state.settings.data_backend == "gold":
        from app.gold.operations import render

        rendered += render(request.app.state.settings.resolved_gold_root)
    return PlainTextResponse(
        rendered,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
