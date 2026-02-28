from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class UsageWindow(DashboardModel):
    remaining_percent: float
    capacity_credits: float
    remaining_credits: float
    reset_at: datetime | None = None
    window_minutes: int | None = None


class UsageCost(DashboardModel):
    currency: str
    total_usd_7d: float = Field(alias="totalUsd7d")


class UsageMetrics(DashboardModel):
    requests_7d: int | None = Field(default=None, alias="requests7d")
    tokens_secondary_window: int | None = None
    cached_tokens_secondary_window: int | None = None
    error_rate_7d: float | None = Field(default=None, alias="errorRate7d")
    top_error: str | None = None
    store_requested_7d: int | None = Field(default=None, alias="storeRequested7d")
    response_context_responses: int | None = None
    response_context_items: int | None = None


class UsageSummaryResponse(DashboardModel):
    primary_window: UsageWindow
    secondary_window: UsageWindow | None = None
    cost: UsageCost
    metrics: UsageMetrics | None = None


class UsageHistoryItem(DashboardModel):
    account_id: str
    remaining_percent_avg: float | None = None
    capacity_credits: float
    remaining_credits: float


class UsageHistoryResponse(DashboardModel):
    window_hours: int
    accounts: List[UsageHistoryItem] = Field(default_factory=list)


class UsageWindowResponse(DashboardModel):
    window_key: str
    window_minutes: int | None = None
    accounts: List[UsageHistoryItem] = Field(default_factory=list)


class TrendPoint(DashboardModel):
    t: datetime
    v: float


class MetricsTrends(DashboardModel):
    requests: List[TrendPoint] = Field(default_factory=list)
    tokens: List[TrendPoint] = Field(default_factory=list)
    cost: List[TrendPoint] = Field(default_factory=list)
    error_rate: List[TrendPoint] = Field(default_factory=list)
