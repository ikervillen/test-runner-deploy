from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Worker shutdown hook shared by the legacy and Gold applications."""
    try:
        yield
    finally:
        app.state.operational_metrics.close()
