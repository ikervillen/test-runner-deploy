from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.auth import require_role
from app.schemas.customer_score import CustomerScoreRequest, CustomerScoreResponse

router = APIRouter(prefix="/customer-score", tags=["customer-score"])


@router.post("/query", response_model=CustomerScoreResponse)
async def query_customer_score(
    payload: CustomerScoreRequest,
    request: Request,
) -> CustomerScoreResponse:
    if payload.score_config_override is not None:
        require_role(request, request.app.state.settings.auth_manager_role)
    response: CustomerScoreResponse = await request.app.state.score_query_executor.execute(
        lambda: request.app.state.calculate_customer_score.execute(payload)
    )
    return response
