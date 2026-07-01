from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class FleetWindowSummary(DashboardModel):
    """One minimal capacity window for a single account."""

    remaining_percent: float | None = None
    reset_at: datetime | None = None
    window_minutes: int | None = None


class FleetAccountSummary(DashboardModel):
    """Non-sensitive capacity projection for fleet consumers."""

    account_id: str
    display_name: str
    email: str
    status: str
    plan_type: str
    primary: FleetWindowSummary
    secondary: FleetWindowSummary
    last_refresh_at: datetime | None = None


class FleetSummaryResponse(DashboardModel):
    accounts: list[FleetAccountSummary] = Field(default_factory=list)


class FleetRefreshResponse(DashboardModel):
    ok: bool = True
    usage_written: bool
    account_count: int
    attempted_count: int
    generated_at: datetime
