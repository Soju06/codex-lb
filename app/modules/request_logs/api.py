from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth.dashboard_access import DashboardPrincipal, DashboardRole
from app.core.auth.dependencies import (
    ensure_dashboard_admin_access,
    require_dashboard_admin_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.utils.time import to_utc_naive, utcnow
from app.dependencies import RequestLogsContext, get_request_logs_context
from app.modules.dashboard.timeframes import (
    resolve_conversation_timeframe,
    resolve_request_log_timeframe,
)
from app.modules.request_logs.schemas import (
    ConversationDetailsResponse,
    ConversationsResponse,
    RequestLogApiKeyOption,
    RequestLogFilterOptionsResponse,
    RequestLogModelOption,
    RequestLogsResponse,
)
from app.modules.request_logs.service import RequestLogModelOption as ServiceRequestLogModelOption

router = APIRouter(
    prefix="/api/request-logs",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)

conversations_router = APIRouter(
    prefix="/api/conversations",
    tags=["dashboard"],
    dependencies=[Depends(require_dashboard_admin_access), Depends(set_dashboard_error_format)],
)

_MODEL_OPTION_DELIMITER = ":::"
_CONVERSATION_MAX_LOOKBACK = timedelta(days=30)


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


def _resolve_request_log_since(
    timeframe: str | None,
    since: datetime | None,
) -> tuple[datetime | None, str | None]:
    if timeframe is not None and since is not None:
        raise HTTPException(status_code=422, detail="timeframe and since cannot be supplied together")
    if timeframe is None:
        return since, None
    return resolve_request_log_timeframe(timeframe), timeframe


@router.get("", response_model=RequestLogsResponse)
async def list_request_logs(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    search: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    account_id: list[str] | None = Query(default=None, alias="accountId"),
    api_key_id: list[str] | None = Query(default=None, alias="apiKeyId"),
    status: list[str] | None = Query(default=None),
    model: list[str] | None = Query(default=None),
    reasoning_effort: list[str] | None = Query(default=None, alias="reasoningEffort"),
    model_option: list[str] | None = Query(default=None, alias="modelOption"),
    timeframe: str | None = Query(default=None, pattern="^(1h|24h|7d)$"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    principal: DashboardPrincipal = Depends(validate_dashboard_session),
    context: RequestLogsContext = Depends(get_request_logs_context),
) -> RequestLogsResponse:
    if conversation_id is not None:
        ensure_dashboard_admin_access(principal)

    parsed_options: list[ServiceRequestLogModelOption] | None = None
    if model_option:
        parsed = [_parse_model_option(value) for value in model_option]
        parsed_options = [value for value in parsed if value is not None] or None
    effective_since, cache_timeframe = _resolve_request_log_since(timeframe, since)
    page = await context.service.list_recent(
        limit=limit,
        offset=offset,
        search=search,
        conversation_id=conversation_id,
        since=effective_since,
        until=until,
        account_ids=account_id,
        api_key_ids=api_key_id,
        model_options=parsed_options,
        models=model,
        reasoning_efforts=reasoning_effort,
        status=status,
        cache_mode="timeframe" if cache_timeframe is not None else "since",
        timeframe=cache_timeframe,
        include_sensitive_metadata=principal.role == DashboardRole.ADMIN,
    )
    return RequestLogsResponse(
        requests=page.requests,
        total=page.total,
        has_more=page.has_more,
        conversation=page.conversation,
    )


@router.get("/options", response_model=RequestLogFilterOptionsResponse)
async def list_request_log_filter_options(
    status: list[str] | None = Query(default=None),
    account_id: list[str] | None = Query(default=None, alias="accountId"),
    api_key_id: list[str] | None = Query(default=None, alias="apiKeyId"),
    model: list[str] | None = Query(default=None),
    reasoning_effort: list[str] | None = Query(default=None, alias="reasoningEffort"),
    model_option: list[str] | None = Query(default=None, alias="modelOption"),
    timeframe: str | None = Query(default=None, pattern="^(1h|24h|7d)$"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    context: RequestLogsContext = Depends(get_request_logs_context),
) -> RequestLogFilterOptionsResponse:
    _ = status  # Keep input backward compatible but do not self-filter status facet.
    parsed_options: list[ServiceRequestLogModelOption] | None = None
    if model_option:
        parsed = [_parse_model_option(value) for value in model_option]
        parsed_options = [value for value in parsed if value is not None] or None
    effective_since, _ = _resolve_request_log_since(timeframe, since)
    options = await context.service.list_filter_options(
        since=effective_since,
        until=until,
        account_ids=account_id,
        api_key_ids=api_key_id,
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
        api_keys=[
            RequestLogApiKeyOption(id=option.id, name=option.name, key_prefix=option.key_prefix)
            for option in options.api_keys
        ],
        statuses=options.statuses,
    )


@conversations_router.get("/", response_model=ConversationsResponse, include_in_schema=False)
@conversations_router.get("", response_model=ConversationsResponse)
async def list_conversations(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    search: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    timeframe: str | None = Query(default=None, pattern="^(1d|7d|30d)$"),
    context: RequestLogsContext = Depends(get_request_logs_context),
) -> ConversationsResponse:
    if timeframe is not None and since is not None:
        raise HTTPException(status_code=422, detail="timeframe and since cannot be supplied together")

    if timeframe is not None:
        _, effective_since = resolve_conversation_timeframe(timeframe)
    else:
        cutoff = utcnow() - _CONVERSATION_MAX_LOOKBACK
        effective_since = to_utc_naive(since) if since is not None else cutoff
        if effective_since < cutoff:
            effective_since = cutoff
    page = await context.service.list_conversations(
        limit=limit,
        offset=offset,
        search=search,
        since=effective_since,
        cache_mode="timeframe" if timeframe else "since",
        timeframe=timeframe,
    )
    return ConversationsResponse(
        conversations=page.conversations,
        total=page.total,
        has_more=page.has_more,
    )


@conversations_router.get("/{conversation_id:path}", response_model=ConversationDetailsResponse)
async def get_conversation_details(
    conversation_id: str,
    context: RequestLogsContext = Depends(get_request_logs_context),
) -> ConversationDetailsResponse:
    details = await context.service.get_conversation_details(conversation_id)
    if details is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationDetailsResponse(
        conversation_id=details.conversation_id,
        start=details.start,
        latest=details.latest,
        account_count=details.account_count,
        total_elapsed_time=details.total_elapsed_time,
        dominant_useragent_group=details.dominant_useragent_group,
        model_stats=details.model_stats,
    )
