from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.dependencies import RequestLogsContext, get_request_logs_context
from app.modules.request_logs.schemas import RequestLogsResponse

router = APIRouter(prefix="/api/request-logs", tags=["dashboard"])


@router.get("", response_model=RequestLogsResponse)
async def list_request_logs(
    limit: int = Query(50, ge=1, le=200),
    account_id: str | None = Query(default=None, alias="accountId"),
    status: str | None = Query(default=None),
    model: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    context: RequestLogsContext = Depends(get_request_logs_context),
) -> RequestLogsResponse:
    logs = await context.service.list_recent(
        limit=limit,
        since=since,
        until=until,
        account_id=account_id,
        model=model,
        status=status,
    )
    return RequestLogsResponse(requests=logs)
