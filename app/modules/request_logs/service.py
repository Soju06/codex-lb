from __future__ import annotations

from datetime import datetime

from app.core.usage.logs import cached_input_tokens_from_log, cost_from_log, total_tokens_from_log
from app.db.models import RequestLog
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.request_logs.schemas import RequestLogEntry

RATE_LIMIT_CODES = {"rate_limit_exceeded", "usage_limit_reached"}
QUOTA_CODES = {"insufficient_quota", "usage_not_included", "quota_exceeded"}


class RequestLogsService:
    def __init__(self, repo: RequestLogsRepository) -> None:
        self._repo = repo

    async def list_recent(
        self,
        limit: int = 50,
        since: datetime | None = None,
        until: datetime | None = None,
        account_id: str | None = None,
        model: str | None = None,
        status: str | None = None,
    ) -> list[RequestLogEntry]:
        status_filter, error_codes = _map_status_filter(status)
        logs = await self._repo.list_recent(
            limit=limit,
            since=since,
            until=until,
            account_id=account_id,
            model=model,
            status=status_filter,
            error_codes=error_codes,
        )
        return [_to_entry(log) for log in logs]


def _map_status_filter(status: str | None) -> tuple[str | None, list[str] | None]:
    if not status:
        return None, None
    normalized = status.lower()
    if normalized == "ok":
        return "success", None
    if normalized == "rate_limit":
        return "error", sorted(RATE_LIMIT_CODES)
    if normalized == "quota":
        return "error", sorted(QUOTA_CODES)
    if normalized == "error":
        return "error", None
    return status, None


def _log_status(log: RequestLog) -> str:
    if log.status == "success":
        return "ok"
    if log.error_code in RATE_LIMIT_CODES:
        return "rate_limit"
    if log.error_code in QUOTA_CODES:
        return "quota"
    return "error"


def _to_entry(log: RequestLog) -> RequestLogEntry:
    return RequestLogEntry(
        requested_at=log.requested_at,
        account_id=log.account_id,
        request_id=log.request_id,
        model=log.model,
        reasoning_effort=log.reasoning_effort,
        status=_log_status(log),
        error_code=log.error_code,
        error_message=log.error_message,
        tokens=total_tokens_from_log(log),
        cached_input_tokens=cached_input_tokens_from_log(log),
        cost_usd=cost_from_log(log, precision=6),
        latency_ms=log.latency_ms,
    )
