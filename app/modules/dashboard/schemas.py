from __future__ import annotations

from datetime import datetime
from typing import List, Literal

from pydantic import Field

from app.modules.accounts.schemas import AccountSummary
from app.modules.shared.schemas import DashboardModel
from app.modules.usage.schemas import MetricsTrends, UsageWindow, UsageWindowResponse

DashboardOverviewTimeframeKey = Literal["1d", "7d", "30d"]


class DashboardOverviewTimeframe(DashboardModel):
    key: DashboardOverviewTimeframeKey
    window_minutes: int = Field(alias="windowMinutes")
    bucket_seconds: int = Field(alias="bucketSeconds")
    bucket_count: int = Field(alias="bucketCount")


class DashboardUsageCost(DashboardModel):
    currency: str
    total_usd: float = Field(alias="totalUsd")


class DashboardUsageMetrics(DashboardModel):
    requests: int | None = None
    tokens: int | None = None
    cached_input_tokens: int | None = Field(default=None, alias="cachedInputTokens")
    error_rate: float | None = Field(default=None, alias="errorRate")
    error_count: int | None = Field(default=None, alias="errorCount")
    top_error: str | None = None


class DashboardOverviewSummary(DashboardModel):
    primary_window: UsageWindow
    secondary_window: UsageWindow | None = None
    cost: DashboardUsageCost
    metrics: DashboardUsageMetrics | None = None


class DashboardUsageWindows(DashboardModel):
    primary: UsageWindowResponse
    secondary: UsageWindowResponse | None = None


class DepletionResponse(DashboardModel):
    risk: float
    risk_level: str  # "safe" | "warning" | "danger" | "critical"
    burn_rate: float
    safe_usage_percent: float
    projected_exhaustion_at: datetime | None = None
    seconds_until_exhaustion: float | None = None


WeeklyCreditPaceStatus = Literal["behind", "on_track", "ahead", "danger"]
WeeklyCreditPaceConfidence = Literal["high", "medium", "low"]


class WeeklyCreditPaceResponse(DashboardModel):
    total_full_credits: float
    total_actual_remaining_credits: float
    total_expected_remaining_credits: float
    actual_used_percent: float
    scheduled_used_percent: float
    delta_percent: float
    schedule_gap_credits: float
    # Legacy frontend field name kept as an alias-compatible value for one release.
    over_plan_credits: float
    projected_shortfall_credits: float
    pause_for_break_even_hours: float | None = None
    pace_multiplier: float | None = None
    throttle_to_percent: float | None = None
    reduce_by_percent: float | None = None
    pro_account_equivalent_to_cover_over_plan: float | None = None
    pro_accounts_to_cover_over_plan: int | None = None
    projected_depletion_hours: float | None = None
    projected_minimum_remaining_credits: float | None = None
    forecast_burn_rate_credits_per_hour: float | None = None
    scheduled_burn_rate_credits_per_hour: float
    status: WeeklyCreditPaceStatus
    account_count: int
    stale_account_count: int = 0
    inactive_account_count: int = 0
    confidence: WeeklyCreditPaceConfidence = "low"


class DashboardOverviewResponse(DashboardModel):
    last_sync_at: datetime | None = None
    timeframe: DashboardOverviewTimeframe
    accounts: List[AccountSummary] = Field(default_factory=list)
    summary: DashboardOverviewSummary
    windows: DashboardUsageWindows
    trends: MetricsTrends
    depletion_primary: DepletionResponse | None = None
    depletion_secondary: DepletionResponse | None = None
    weekly_credit_pace: WeeklyCreditPaceResponse | None = None


class DashboardProjectionsResponse(DashboardModel):
    depletion_primary: DepletionResponse | None = None
    depletion_secondary: DepletionResponse | None = None
    weekly_credit_pace: WeeklyCreditPaceResponse | None = None
