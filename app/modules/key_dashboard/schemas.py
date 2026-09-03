from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


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
