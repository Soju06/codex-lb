from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.request_logs.mappers import (
    QUOTA_CODES,
    RATE_LIMIT_CODES,
    normalize_log_status,
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


@dataclass(frozen=True, slots=True)
class RequestLogFilterOptions:
    account_ids: list[str]
    model_options: list[RequestLogModelOption]
    statuses: list[str]


@dataclass(frozen=True, slots=True)
class RequestLogsPage:
    requests: list[RequestLogEntry]
    total: int
    has_more: bool


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
        owner_user_id: str | None = None,
    ) -> RequestLogsPage:
        status_filter = _map_status_filter(status)
        normalized_model_options = (
            [(option.model, option.reasoning_effort) for option in model_options] if model_options else None
        )
        logs, total = await self._repo.list_recent(
            limit=limit,
            offset=offset,
            search=search,
            since=since,
            until=until,
            account_ids=account_ids,
            model_options=normalized_model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=status_filter.include_success,
            include_error_other=status_filter.include_error_other,
            error_codes_in=status_filter.error_codes_in,
            error_codes_excluding=status_filter.error_codes_excluding,
            owner_user_id=owner_user_id,
        )
        requests = [to_request_log_entry(log) for log in logs]
        return RequestLogsPage(
            requests=requests,
            total=total,
            has_more=offset + limit < total,
        )

    async def list_filter_options(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        model_options: list[RequestLogModelOption] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        owner_user_id: str | None = None,
    ) -> RequestLogFilterOptions:
        normalized_model_options = (
            [(option.model, option.reasoning_effort) for option in model_options] if model_options else None
        )
        option_account_ids, option_model_options, status_values = await self._repo.list_filter_options(
            since=since,
            until=until,
            account_ids=account_ids,
            model_options=normalized_model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            owner_user_id=owner_user_id,
        )
        return RequestLogFilterOptions(
            account_ids=option_account_ids,
            model_options=[
                RequestLogModelOption(model=model, reasoning_effort=reasoning_effort)
                for model, reasoning_effort in option_model_options
            ],
            statuses=_normalize_status_values(status_values),
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


def _normalize_status_values(values: list[tuple[str, str | None]]) -> list[str]:
    normalized = {normalize_log_status(status, error_code) for status, error_code in values}
    ordered = ["ok", "rate_limit", "quota", "error"]
    return [status for status in ordered if status in normalized]
