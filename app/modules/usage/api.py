from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.dependencies import UsageContext, get_usage_context
from app.modules.usage.schemas import UsageHistoryResponse, UsageSummaryResponse, UsageWindowResponse

router = APIRouter(prefix="/api/usage", tags=["dashboard"])


@router.get("/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    context: UsageContext = Depends(get_usage_context),
) -> UsageSummaryResponse:
    return await context.service.get_usage_summary()


@router.get("/history", response_model=UsageHistoryResponse)
async def get_usage_history(
    hours: int = Query(24, ge=1, le=168),
    context: UsageContext = Depends(get_usage_context),
) -> UsageHistoryResponse:
    return await context.service.get_usage_history(hours)

@router.get("/window", response_model=UsageWindowResponse)
async def get_usage_window(
    window: str = Query("primary", pattern="^(primary|secondary)$"),
    context: UsageContext = Depends(get_usage_context),
) -> UsageWindowResponse:
    return await context.service.get_usage_window(window)
