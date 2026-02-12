from __future__ import annotations

from typing import cast

from app.core.usage.logs import RequestLogLike, cached_input_tokens_from_log, cost_from_log, total_tokens_from_log
from app.db.models import RequestLog
from app.modules.request_logs.schemas import RequestLogEntry

RATE_LIMIT_CODES = {"rate_limit_exceeded", "usage_limit_reached"}
QUOTA_CODES = {"insufficient_quota", "usage_not_included", "quota_exceeded"}


def normalize_log_status(status: str, error_code: str | None) -> str:
    if status == "success":
        return "ok"
    if error_code in RATE_LIMIT_CODES:
        return "rate_limit"
    if error_code in QUOTA_CODES:
        return "quota"
    return "error"


def log_status(log: RequestLog) -> str:
    return normalize_log_status(log.status, log.error_code)


def to_request_log_entry(log: RequestLog) -> RequestLogEntry:
    log_like = cast(RequestLogLike, log)
    return RequestLogEntry(
        requested_at=log.requested_at,
        account_id=log.account_id,
        request_id=log.request_id,
        model=log.model,
        reasoning_effort=log.reasoning_effort,
        status=log_status(log),
        error_code=log.error_code,
        error_message=log.error_message,
        tokens=total_tokens_from_log(log_like),
        cached_input_tokens=cached_input_tokens_from_log(log_like),
        cost_usd=cost_from_log(log_like, precision=6),
        latency_ms=log.latency_ms,
    )
