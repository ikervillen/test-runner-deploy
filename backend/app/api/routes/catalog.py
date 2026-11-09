from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from app.schemas.api import CatalogResponse

router = APIRouter(tags=["catalog"])


@router.get("/catalog", response_model=CatalogResponse)
async def catalog(request: Request) -> CatalogResponse:
    return await asyncio.to_thread(request.app.state.get_catalog.execute)
