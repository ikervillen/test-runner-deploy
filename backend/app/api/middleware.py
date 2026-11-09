from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette import status
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.auth import AuthenticatedPrincipal


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        logger = structlog.get_logger()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http_request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        request.app.state.operational_metrics.record_http_request(
            method=request.method,
            path=getattr(request.scope.get("route"), "path", "/unmatched"),
            status_code=response.status_code,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return response


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Acepta identidad solo desde un proxy corporativo de confianza."""

    _public_paths = frozenset({"/api/health", "/api/ready"})

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = request.app.state.settings
        if settings.auth_mode == "disabled" or request.url.path in self._public_paths:
            return await call_next(request)

        user = request.headers.get(settings.auth_trusted_user_header, "").strip()
        roles = frozenset(
            role.strip()
            for role in request.headers.get(settings.auth_trusted_roles_header, "").split(",")
            if role.strip()
        )
        if not user:
            return self._error_response(
                request,
                status.HTTP_401_UNAUTHORIZED,
                "AUTHENTICATION_REQUIRED",
                "El proxy corporativo no ha proporcionado una identidad valida.",
            )
        if settings.auth_viewer_role not in roles:
            return self._error_response(
                request,
                status.HTTP_403_FORBIDDEN,
                "FORBIDDEN",
                "La identidad no tiene el rol minimo de consulta.",
            )
        request.state.principal = AuthenticatedPrincipal(user=user, roles=roles)
        return await call_next(request)

    @staticmethod
    def _error_response(
        request: Request,
        status_code: int,
        code: str,
        message: str,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "details": {},
                    "request_id": getattr(request.state, "request_id", "unknown"),
                }
            },
        )
