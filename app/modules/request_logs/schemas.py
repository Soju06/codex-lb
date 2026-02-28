from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class RequestLogEntry(DashboardModel):
    requested_at: datetime
    account_id: str
    request_id: str
    model: str
    requested_model: str | None = None
    forced_model: str | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_effort: str | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    client_app: str | None = None
    client_ip: str | None = None
    auth_key_fingerprint: str | None = None
    store_requested: bool = False


class RequestLogsResponse(DashboardModel):
    requests: list[RequestLogEntry] = Field(default_factory=list)
    total: int
    has_more: bool


class RequestLogModelOption(DashboardModel):
    model: str
    requested_model: str | None = None
    forced_model: str | None = None
    reasoning_effort: str | None = None


class RequestLogFilterOptionsResponse(DashboardModel):
    account_ids: list[str] = Field(default_factory=list)
    model_options: list[RequestLogModelOption] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    client_apps: list[str] = Field(default_factory=list)
