from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class RequestLogEntry(DashboardModel):
    requested_at: datetime
    account_id: str | None = None
    api_key_name: str | None = None
    request_id: str
    model: str
    transport: str | None = None
    service_tier: str | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_effort: str | None = None
    cost_usd: float | None = None
    burn_rate_5h_plus_accounts: float | None = Field(default=None, serialization_alias="burnRate5hPlusAccounts")
    burn_rate_7d_plus_accounts: float | None = Field(default=None, serialization_alias="burnRate7dPlusAccounts")
    latency_ms: int | None = None


class RequestLogsResponse(DashboardModel):
    requests: list[RequestLogEntry] = Field(default_factory=list)
    total: int
    has_more: bool


class RequestLogModelOption(DashboardModel):
    model: str
    reasoning_effort: str | None = None


class RequestLogFilterOptionsResponse(DashboardModel):
    account_ids: list[str] = Field(default_factory=list)
    model_options: list[RequestLogModelOption] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
