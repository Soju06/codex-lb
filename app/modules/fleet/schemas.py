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


class FleetPressureMetric(DashboardModel):
    request_count: int = 0
    error_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class FleetPressureAccountBreakdown(FleetPressureMetric):
    account_id: str
    email: str | None = None
    label: str
    last_selected_at: datetime | None = None


class FleetPressureKindBreakdown(FleetPressureMetric):
    name: str
    request_kind: str


class FleetPressureClientBreakdown(FleetPressureMetric):
    name: str
    client_group: str


class FleetPressureWindow(FleetPressureMetric):
    key: str
    label: str
    seconds: int
    truncated: bool = False
    top_error_code: str | None = None
    by_account: list[FleetPressureAccountBreakdown] = Field(default_factory=list)
    by_kind: list[FleetPressureKindBreakdown] = Field(default_factory=list)
    by_client: list[FleetPressureClientBreakdown] = Field(default_factory=list)


class FleetPressureObservability(DashboardModel):
    available: bool = True
    windows: list[FleetPressureWindow] = Field(default_factory=list)


class FleetStickyKindBreakdown(DashboardModel):
    name: str
    total: int = 0
    stale_count: int = 0


class FleetStickyAccountBreakdown(DashboardModel):
    account_id: str
    email: str | None = None
    label: str
    total: int = 0
    recent_count: int = 0
    stale_count: int = 0
    last_updated_at: datetime | None = None
    kinds: list[FleetStickyKindBreakdown] = Field(default_factory=list)


class FleetStickyObservability(DashboardModel):
    available: bool = True
    total: int = 0
    recent_count: int = 0
    stale_count: int = 0
    stale_threshold_seconds: int | None = None
    truncated: bool = False
    by_account: list[FleetStickyAccountBreakdown] = Field(default_factory=list)


class FleetObservabilityResponse(DashboardModel):
    available: bool = True
    generated_at: datetime
    source: str = "codex-lb fleet observability"
    pressure: FleetPressureObservability = Field(default_factory=FleetPressureObservability)
    sticky: FleetStickyObservability = Field(default_factory=FleetStickyObservability)
