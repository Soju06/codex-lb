from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class RequestLogEntry(DashboardModel):
    requested_at: datetime
    account_id: str | None = None
    plan_type: str | None = None
    api_key_id: str | None = None
    api_key_name: str | None = None
    request_id: str
    request_kind: str = "normal"
    model: str
    transport: str | None = None
    service_tier: str | None = None
    requested_service_tier: str | None = None
    actual_service_tier: str | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_effort: str | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    latency_first_token_ms: int | None = None


class RequestLogsResponse(DashboardModel):
    requests: list[RequestLogEntry] = Field(default_factory=list)
    total: int
    has_more: bool


class RequestLogModelOption(DashboardModel):
    model: str
    reasoning_effort: str | None = None


class RequestLogApiKeyOption(DashboardModel):
    id: str
    name: str
    key_prefix: str | None = None


class RequestLogFilterOptionsResponse(DashboardModel):
    account_ids: list[str] = Field(default_factory=list)
    model_options: list[RequestLogModelOption] = Field(default_factory=list)
    api_keys: list[RequestLogApiKeyOption] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
