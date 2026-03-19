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


class BurnRateSnapshotResponse(DashboardModel):
    recorded_at: datetime
    primary_projected_plus_accounts: float | None = None
    secondary_projected_plus_accounts: float | None = None
    primary_used_plus_accounts: float | None = None
    secondary_used_plus_accounts: float | None = None
    primary_window_minutes: int | None = None
    secondary_window_minutes: int | None = None
    primary_account_count: int = 0
    secondary_account_count: int = 0
    primary_max_plus_equivalent_accounts: float = 0.0
    secondary_max_plus_equivalent_accounts: float = 0.0


class DashboardOverviewResponse(DashboardModel):
    last_sync_at: datetime | None = None
    accounts: List[AccountSummary] = Field(default_factory=list)
    summary: UsageSummaryResponse
    windows: DashboardUsageWindows
    trends: MetricsTrends
    depletion_primary: DepletionResponse | None = None
    depletion_secondary: DepletionResponse | None = None
    burn_rate: BurnRateSnapshotResponse | None = None
