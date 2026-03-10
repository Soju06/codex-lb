from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field

from app.modules.accounts.schemas import AccountSummary
from app.modules.shared.schemas import DashboardModel
from app.modules.usage.schemas import MetricsTrends, UsageSummaryResponse, UsageWindowResponse


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
    window: str = "primary"  # which donut the depletion marker applies to


class DashboardOverviewResponse(DashboardModel):
    last_sync_at: datetime | None = None
    accounts: List[AccountSummary] = Field(default_factory=list)
    summary: UsageSummaryResponse
    windows: DashboardUsageWindows
    trends: MetricsTrends
    depletion: DepletionResponse | None = None
