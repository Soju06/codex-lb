from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.dependencies import RequestLogsContext, get_request_logs_context
from app.modules.request_logs.schemas import (
    RequestLogFilterOptionsResponse,
    RequestLogModelOption,
    RequestLogsResponse,
)
from app.modules.request_logs.service import RequestLogModelOption as ServiceRequestLogModelOption

router = APIRouter(prefix="/api/request-logs", tags=["dashboard"])

_MODEL_OPTION_DELIMITER = ":::"


def _parse_model_option(value: str) -> ServiceRequestLogModelOption | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if _MODEL_OPTION_DELIMITER not in raw:
        return ServiceRequestLogModelOption(model=raw, reasoning_effort=None)
    model, effort = raw.split(_MODEL_OPTION_DELIMITER, 1)
    model = model.strip()
    effort = effort.strip()
    if not model:
        return None
    return ServiceRequestLogModelOption(model=model, reasoning_effort=effort or None)


@router.get("", response_model=RequestLogsResponse)
async def list_request_logs(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    search: str | None = Query(default=None),
    account_id: list[str] | None = Query(default=None, alias="accountId"),
    status: list[str] | None = Query(default=None),
    model: list[str] | None = Query(default=None),
    reasoning_effort: list[str] | None = Query(default=None, alias="reasoningEffort"),
    model_option: list[str] | None = Query(default=None, alias="modelOption"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    context: RequestLogsContext = Depends(get_request_logs_context),
) -> RequestLogsResponse:
    parsed_options: list[ServiceRequestLogModelOption] | None = None
    if model_option:
        parsed = [_parse_model_option(value) for value in model_option]
        parsed_options = [value for value in parsed if value is not None] or None
    page = await context.service.list_recent(
        limit=limit,
        offset=offset,
        search=search,
        since=since,
        until=until,
        account_ids=account_id,
        model_options=parsed_options,
        models=model,
        reasoning_efforts=reasoning_effort,
        status=status,
    )
    return RequestLogsResponse(
        requests=page.requests,
        total=page.total,
        has_more=page.has_more,
    )


@router.get("/options", response_model=RequestLogFilterOptionsResponse)
async def list_request_log_filter_options(
    status: list[str] | None = Query(default=None),
    account_id: list[str] | None = Query(default=None, alias="accountId"),
    model: list[str] | None = Query(default=None),
    reasoning_effort: list[str] | None = Query(default=None, alias="reasoningEffort"),
    model_option: list[str] | None = Query(default=None, alias="modelOption"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    context: RequestLogsContext = Depends(get_request_logs_context),
) -> RequestLogFilterOptionsResponse:
    _ = status  # Keep input backward compatible but do not self-filter status facet.
    parsed_options: list[ServiceRequestLogModelOption] | None = None
    if model_option:
        parsed = [_parse_model_option(value) for value in model_option]
        parsed_options = [value for value in parsed if value is not None] or None
    options = await context.service.list_filter_options(
        since=since,
        until=until,
        account_ids=account_id,
        model_options=parsed_options,
        models=model,
        reasoning_efforts=reasoning_effort,
    )
    return RequestLogFilterOptionsResponse(
        account_ids=options.account_ids,
        model_options=[
            RequestLogModelOption(model=option.model, reasoning_effort=option.reasoning_effort)
            for option in options.model_options
        ],
        statuses=options.statuses,
    )
