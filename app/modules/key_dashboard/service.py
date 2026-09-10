from __future__ import annotations

from datetime import timedelta
from typing import cast

from app.core.usage.logs import (
    RequestLogLike,
    cached_input_tokens_from_log,
    cost_breakdown_from_log,
    output_tokens_from_log,
    total_tokens_from_log,
)
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.modules.api_keys.service import ApiKeyData
from app.modules.key_dashboard.repository import GroupDailyUsage, KeyDashboardRepository
from app.modules.key_dashboard.schemas import (
    KeyDashboardCostBreakdown,
    KeyDashboardGroupDay,
    KeyDashboardGroupMember,
    KeyDashboardGroupResponse,
    KeyDashboardProfile,
    KeyDashboardRequestLog,
    KeyDashboardRequestLogsResponse,
)
from app.modules.request_logs.mappers import log_status
from app.modules.request_logs.repository import RequestLogsRepository


class KeyDashboardService:
    def __init__(self, repository: RequestLogsRepository, group_repository: KeyDashboardRepository) -> None:
        self._repository = repository
        self._group_repository = group_repository

    async def get_group_usage(self, api_key_id: str) -> KeyDashboardGroupResponse:
        until = utcnow()
        since = until - timedelta(days=30)
        group = await self._group_repository.group_usage(api_key_id, since, until)
        days = [
            since.date() + timedelta(days=offset)
            for offset in range(((until - timedelta(microseconds=1)).date() - since.date()).days + 1)
        ]
        members: list[KeyDashboardGroupMember] = []
        for member in group.members:
            daily_usage = [
                KeyDashboardGroupDay(
                    date=day,
                    total_tokens=(usage := member.daily_usage.get(day, GroupDailyUsage())).total_tokens,
                    total_cost_usd=round(usage.total_cost_usd, 6),
                )
                for day in days
            ]
            members.append(
                KeyDashboardGroupMember(
                    name=member.name,
                    key_prefix=f"{member.key_prefix}…",
                    is_current_key=member.key_id == api_key_id,
                    request_count=sum(usage.request_count for usage in member.daily_usage.values()),
                    total_tokens=sum(day.total_tokens for day in daily_usage),
                    cached_input_tokens=sum(usage.cached_input_tokens for usage in member.daily_usage.values()),
                    total_cost_usd=round(sum(day.total_cost_usd for day in daily_usage), 6),
                    daily_usage=daily_usage,
                )
            )
        return KeyDashboardGroupResponse(
            group_name=group.name,
            from_=since,
            until=until,
            members=members,
        )

    @staticmethod
    def get_profile(api_key: ApiKeyData) -> KeyDashboardProfile:
        return KeyDashboardProfile(
            name=api_key.name,
            key_prefix=f"{api_key.key_prefix}…",
            is_active=api_key.is_active,
            created_at=api_key.created_at,
            expires_at=api_key.expires_at,
            last_used_at=api_key.last_used_at,
            allowed_models=api_key.allowed_models,
            enforced_model=api_key.enforced_model,
            allowed_reasoning_efforts=api_key.allowed_reasoning_efforts,
            enforced_reasoning_effort=api_key.enforced_reasoning_effort,
            enforced_service_tier=api_key.enforced_service_tier,
            traffic_class=api_key.traffic_class,
            transport_policy_override=api_key.transport_policy_override,
        )

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
