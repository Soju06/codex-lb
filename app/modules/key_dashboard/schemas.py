from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class KeyDashboardProfile(DashboardModel):
    """Explicitly allowlisted API key metadata safe for its owner."""

    name: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    allowed_models: list[str] | None = None
    enforced_model: str | None = None
    allowed_reasoning_efforts: list[str] | None = None
    enforced_reasoning_effort: str | None = None
    enforced_service_tier: str | None = None
    traffic_class: str
    transport_policy_override: str | None = None


class KeyDashboardCostBreakdown(DashboardModel):
    input_usd: float | None = None
    cached_input_usd: float | None = None
    output_usd: float | None = None
    total_usd: float | None = None


class KeyDashboardRequestLog(DashboardModel):
    """Explicitly allowlisted request metadata safe for an API-key owner."""

    requested_at: datetime
    request_id: str
    request_kind: str
    model: str
    transport: str | None = None
    upstream_transport: str | None = None
    service_tier: str | None = None
    requested_service_tier: str | None = None
    actual_service_tier: str | None = None
    reasoning_effort: str | None = None
    status: str
    error_code: str | None = None
    tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    output_tokens_raw: int | None = None
    reasoning_tokens: int | None = None
    cached_input_tokens: int | None = None
    cost_usd: float | None = None
    cost_breakdown: KeyDashboardCostBreakdown = Field(default_factory=KeyDashboardCostBreakdown)
    latency_ms: int | None = None
    latency_first_token_ms: int | None = None
    latency_queue_ms: int | None = None


class KeyDashboardRequestLogsResponse(DashboardModel):
    requests: list[KeyDashboardRequestLog] = Field(default_factory=list)
    total: int
    has_more: bool


class KeyDashboardGroupDay(DashboardModel):
    date: date
    total_tokens: int
    total_cost_usd: float


class KeyDashboardGroupMember(DashboardModel):
    name: str
    key_prefix: str
    is_current_key: bool
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float
    daily_usage: list[KeyDashboardGroupDay]


class KeyDashboardGroupResponse(DashboardModel):
    group_name: str | None
    from_: datetime = Field(alias="from")
    until: datetime
    members: list[KeyDashboardGroupMember]
