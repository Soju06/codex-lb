from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from app.core.usage.logs import (
    RequestLogLike,
    cached_input_tokens_from_log,
    cost_from_log,
    total_tokens_from_log,
)
from app.db.models import RequestLog
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.request_logs.schemas import RequestLogEntry

RATE_LIMIT_CODES = {"rate_limit_exceeded", "usage_limit_reached"}
QUOTA_CODES = {"insufficient_quota", "usage_not_included", "quota_exceeded"}


@dataclass(frozen=True, slots=True)
class RequestLogModelOption:
    model: str
    reasoning_effort: str | None


@dataclass(frozen=True, slots=True)
class RequestLogStatusFilter:
    include_success: bool
    include_error_other: bool
    error_codes_in: list[str] | None
    error_codes_excluding: list[str] | None


@dataclass(frozen=True, slots=True)
class RequestLogFilterOptions:
    account_ids: list[str]
    model_options: list[RequestLogModelOption]


class RequestLogsService:
    def __init__(self, repo: RequestLogsRepository) -> None:
        self._repo = repo

    async def list_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        model_options: list[RequestLogModelOption] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        status: list[str] | None = None,
    ) -> list[RequestLogEntry]:
        status_filter = _map_status_filter(status)
        logs = await self._repo.list_recent(
            limit=limit,
            offset=offset,
            search=search,
            since=since,
            until=until,
            account_ids=account_ids,
            model_options=(
                [(option.model, option.reasoning_effort) for option in model_options] if model_options else None
            ),
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=status_filter.include_success,
            include_error_other=status_filter.include_error_other,
            error_codes_in=status_filter.error_codes_in,
            error_codes_excluding=status_filter.error_codes_excluding,
        )
        return [_to_entry(log) for log in logs]

    async def list_filter_options(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        status: list[str] | None = None,
    ) -> RequestLogFilterOptions:
        status_filter = _map_status_filter(status)
        account_ids, model_options = await self._repo.list_filter_options(
            since=since,
            until=until,
            include_success=status_filter.include_success,
            include_error_other=status_filter.include_error_other,
            error_codes_in=status_filter.error_codes_in,
            error_codes_excluding=status_filter.error_codes_excluding,
        )
        return RequestLogFilterOptions(
            account_ids=account_ids,
            model_options=[
                RequestLogModelOption(model=model, reasoning_effort=reasoning_effort)
                for model, reasoning_effort in model_options
            ],
        )


def _map_status_filter(status: list[str] | None) -> RequestLogStatusFilter:
    if not status:
        return RequestLogStatusFilter(
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
        )
    normalized = {value.lower() for value in status if value}
    if not normalized or "all" in normalized:
        return RequestLogStatusFilter(
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
        )

    include_success = "ok" in normalized
    include_rate_limit = "rate_limit" in normalized
    include_quota = "quota" in normalized
    include_error_other = "error" in normalized

    error_codes_in: set[str] = set()
    if include_rate_limit:
        error_codes_in |= RATE_LIMIT_CODES
    if include_quota:
        error_codes_in |= QUOTA_CODES

    return RequestLogStatusFilter(
        include_success=include_success,
        include_error_other=include_error_other,
        error_codes_in=sorted(error_codes_in) if error_codes_in else None,
        error_codes_excluding=sorted(RATE_LIMIT_CODES | QUOTA_CODES) if include_error_other else None,
    )


def _log_status(log: RequestLog) -> str:
    if log.status == "success":
        return "ok"
    if log.error_code in RATE_LIMIT_CODES:
        return "rate_limit"
    if log.error_code in QUOTA_CODES:
        return "quota"
    return "error"


def _to_entry(log: RequestLog) -> RequestLogEntry:
    log_like = cast(RequestLogLike, log)
    return RequestLogEntry(
        requested_at=log.requested_at,
        account_id=log.account_id,
        request_id=log.request_id,
        model=log.model,
        reasoning_effort=log.reasoning_effort,
        status=_log_status(log),
        error_code=log.error_code,
        error_message=log.error_message,
        tokens=total_tokens_from_log(log_like),
        cached_input_tokens=cached_input_tokens_from_log(log_like),
        cost_usd=cost_from_log(log_like, precision=6),
        latency_ms=log.latency_ms,
    )
