from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from app.schemas.customer_quality import CustomerQualityRequest, CustomerQualityResponse

router = APIRouter(prefix="/customer-quality", tags=["customer-quality"])


@router.post("/query", response_model=CustomerQualityResponse)
async def query_customer_quality(
    payload: CustomerQualityRequest,
    request: Request,
) -> CustomerQualityResponse:
    return await asyncio.to_thread(
        request.app.state.calculate_customer_quality.execute,
        payload,
    )
