"""Domain-level application error, independent of the HTTP framework.

The API layer maps it to a JSON error response; domain and infrastructure raise it
without importing FastAPI.
"""

from __future__ import annotations

from typing import Any

HTTP_BAD_REQUEST = 400


class ApplicationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = HTTP_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
