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

_REQUEST_KIND_ORDER = ("responses", "compact", "transcription")
_TRANSPORT_ORDER = ("http", "websocket")


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
    request_kinds: list[str]
    transports: list[str]
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
        request_kinds: list[str] | None = None,
        transports: list[str] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        status: list[str] | None = None,
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
            request_kinds=request_kinds,
            transports=transports,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=status_filter.include_success,
            include_error_other=status_filter.include_error_other,
            error_codes_in=status_filter.error_codes_in,
            error_codes_excluding=status_filter.error_codes_excluding,
        )
        api_key_ids = [log.api_key_id for log in logs if log.api_key_id]
        api_key_name_by_id = await self._repo.get_api_key_names_by_ids(api_key_ids)
        requests = [
            to_request_log_entry(
                log,
                api_key_name=api_key_name_by_id.get(log.api_key_id or ""),
            )
            for log in logs
        ]
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
        request_kinds: list[str] | None = None,
        transports: list[str] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
    ) -> RequestLogFilterOptions:
        normalized_model_options = (
            [(option.model, option.reasoning_effort) for option in model_options] if model_options else None
        )
        (
            option_account_ids,
            option_model_options,
            option_request_kinds,
            option_transports,
            status_values,
        ) = await self._repo.list_filter_options(
            since=since,
            until=until,
            account_ids=account_ids,
            model_options=normalized_model_options,
            request_kinds=request_kinds,
            transports=transports,
            models=models,
            reasoning_efforts=reasoning_efforts,
        )
        return RequestLogFilterOptions(
            account_ids=option_account_ids,
            model_options=[
                RequestLogModelOption(model=model, reasoning_effort=reasoning_effort)
                for model, reasoning_effort in option_model_options
            ],
            request_kinds=_normalize_ordered_values(option_request_kinds, _REQUEST_KIND_ORDER),
            transports=_normalize_ordered_values(option_transports, _TRANSPORT_ORDER),
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


def _normalize_ordered_values(values: list[str], preferred_order: tuple[str, ...]) -> list[str]:
    normalized = {value for value in values if value}
    ordered = [value for value in preferred_order if value in normalized]
    remaining = sorted(normalized - set(preferred_order))
    return ordered + remaining
