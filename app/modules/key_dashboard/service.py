from __future__ import annotations

from typing import cast

from app.core.usage.logs import (
    RequestLogLike,
    cached_input_tokens_from_log,
    cost_breakdown_from_log,
    output_tokens_from_log,
    total_tokens_from_log,
)
from app.db.models import RequestLog
from app.modules.key_dashboard.schemas import (
    KeyDashboardCostBreakdown,
    KeyDashboardRequestLog,
    KeyDashboardRequestLogsResponse,
)
from app.modules.request_logs.mappers import log_status
from app.modules.request_logs.repository import RequestLogsRepository


class KeyDashboardService:
    def __init__(self, repository: RequestLogsRepository) -> None:
        self._repository = repository

    async def list_recent_requests(
        self,
        *,
        api_key_id: str,
        limit: int,
        offset: int,
    ) -> KeyDashboardRequestLogsResponse:
        result = await self._repository.list_recent(
            limit=limit,
            offset=offset,
            api_key_ids=[api_key_id],
            include_sensitive_metadata=False,
        )
        return KeyDashboardRequestLogsResponse(
            requests=[self._to_safe_request(log) for log in result.logs],
            total=result.total,
            has_more=offset + len(result.logs) < result.total,
        )

    @staticmethod
    def _to_safe_request(log: RequestLog) -> KeyDashboardRequestLog:
        log_like = cast(RequestLogLike, log)
        cost = cost_breakdown_from_log(log_like, precision=6)
        return KeyDashboardRequestLog(
            requested_at=log.requested_at,
            request_id=log.request_id,
            request_kind=log.request_kind,
            model=log.model,
            transport=log.transport,
            upstream_transport=log.upstream_transport,
            service_tier=log.service_tier,
            requested_service_tier=log.requested_service_tier,
            actual_service_tier=log.actual_service_tier,
            reasoning_effort=log.reasoning_effort,
            status=log_status(log),
            error_code=log.error_code,
            tokens=total_tokens_from_log(log_like),
            input_tokens=log.input_tokens,
            output_tokens=output_tokens_from_log(log_like),
            output_tokens_raw=log.output_tokens,
            reasoning_tokens=log.reasoning_tokens,
            cached_input_tokens=cached_input_tokens_from_log(log_like),
            cost_usd=cost.total_usd,
            cost_breakdown=KeyDashboardCostBreakdown(**cost.__dict__),
            latency_ms=log.latency_ms,
            latency_first_token_ms=log.latency_first_token_ms,
            latency_queue_ms=log.latency_queue_ms,
        )
