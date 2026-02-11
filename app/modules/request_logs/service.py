from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.request_logs.mappers import (
    QUOTA_CODES,
    RATE_LIMIT_CODES,
    to_request_log_entry,
)
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.request_logs.schemas import RequestLogEntry


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
    force_empty: bool = False


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
        if status_filter.force_empty:
            return []
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
        return [to_request_log_entry(log) for log in logs]

    async def list_filter_options(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        status: list[str] | None = None,
    ) -> RequestLogFilterOptions:
        status_filter = _map_status_filter(status)
        if status_filter.force_empty:
            return RequestLogFilterOptions(account_ids=[], model_options=[])
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
            force_empty=False,
        )
    normalized = {value.lower() for value in status if value}
    if not normalized or "all" in normalized:
        return RequestLogStatusFilter(
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
            force_empty=False,
        )

    include_success = "ok" in normalized
    include_rate_limit = "rate_limit" in normalized
    include_quota = "quota" in normalized
    include_error_other = "error" in normalized
    recognized = {"ok", "rate_limit", "quota", "error"}
    has_recognized = any(value in recognized for value in normalized)
    if not has_recognized:
        return RequestLogStatusFilter(
            include_success=False,
            include_error_other=False,
            error_codes_in=None,
            error_codes_excluding=None,
            force_empty=True,
        )

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
        force_empty=False,
    )
