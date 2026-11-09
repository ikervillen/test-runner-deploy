from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from starlette import status

from app.api.errors import ApplicationError


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user: str
    roles: frozenset[str]


def require_role(request: Request, role: str) -> None:
    settings = request.app.state.settings
    if settings.auth_mode == "disabled":
        return
    principal = getattr(request.state, "principal", None)
    if principal is None or role not in principal.roles:
        raise ApplicationError(
            "FORBIDDEN",
            "Tu identidad no tiene permisos para esta operacion.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
