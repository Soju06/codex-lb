from __future__ import annotations

from typing import cast as typing_cast

from app.core.usage.logs import (
    RequestLogLike,
    cached_input_tokens_from_log,
    cost_breakdown_from_log,
    output_tokens_from_log,
    total_tokens_from_log,
)
from app.db.models import RequestLog
from app.modules.request_logs.schemas import RequestLogCostBreakdown, RequestLogEntry

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


def to_request_log_entry(log: RequestLog, *, api_key_name: str | None = None) -> RequestLogEntry:
    log_like = typing_cast(RequestLogLike, log)
    cost_breakdown = cost_breakdown_from_log(log_like, precision=6)
    return RequestLogEntry(
        requested_at=log.requested_at,
        account_id=log.account_id,
        plan_type=log.plan_type,
        api_key_id=log.api_key_id,
        api_key_name=api_key_name,
        request_id=log.request_id,
        request_kind=log.request_kind,
        model=log.model,
        source=log.source,
        useragent=log.useragent,
        useragent_group=log.useragent_group,
        transport=log.transport,
        upstream_transport=log.upstream_transport,
        service_tier=log.service_tier,
        requested_service_tier=log.requested_service_tier,
        actual_service_tier=log.actual_service_tier,
        reasoning_effort=log.reasoning_effort,
        status=log_status(log),
        error_code=log.error_code,
        error_message=log.error_message,
        failure_phase=log.failure_phase,
        failure_detail=log.failure_detail,
        failure_exception_type=log.failure_exception_type,
        upstream_status_code=log.upstream_status_code,
        upstream_error_code=log.upstream_error_code,
        bridge_stage=log.bridge_stage,
        tokens=total_tokens_from_log(log_like),
        input_tokens=log.input_tokens,
        output_tokens=output_tokens_from_log(log_like),
        cached_input_tokens=cached_input_tokens_from_log(log_like),
        cost_usd=cost_breakdown.total_usd,
        cost_breakdown=RequestLogCostBreakdown(**cost_breakdown.__dict__),
        latency_ms=log.latency_ms,
        latency_first_token_ms=log.latency_first_token_ms,
    )
