from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class RequestLogEntry(DashboardModel):
    requested_at: datetime
    account_id: str
    request_id: str
    model: str
    status: str
    error_code: str | None = None
    error_message: str | None = None
    tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_effort: str | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None


class RequestLogsResponse(DashboardModel):
    requests: List[RequestLogEntry] = Field(default_factory=list)
