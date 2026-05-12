from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class UsageTrendPoint(DashboardModel):
    t: datetime
    v: float


class AccountUsageTrend(DashboardModel):
    primary: list[UsageTrendPoint] = Field(default_factory=list)
    secondary: list[UsageTrendPoint] = Field(default_factory=list)


class AccountUsage(DashboardModel):
    primary_remaining_percent: float | None = None
    secondary_remaining_percent: float | None = None


class AccountRequestUsage(DashboardModel):
    request_count: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    total_cost_usd: float = 0.0


class AccountTokenStatus(DashboardModel):
    expires_at: datetime | None = None
    state: str | None = None


class AccountAuthStatus(DashboardModel):
    access: AccountTokenStatus | None = None
    refresh: AccountTokenStatus | None = None
    id_token: AccountTokenStatus | None = None


class AccountAdditionalWindow(DashboardModel):
    used_percent: float
    reset_at: int | None = None
    window_minutes: int | None = None


class AccountAdditionalQuota(DashboardModel):
    quota_key: str | None = None
    limit_name: str
    metered_feature: str
    display_label: str | None = None
    routing_policy: str = Field(default="inherit", pattern=r"^(inherit|burn_first|normal|preserve)$")
    primary_window: AccountAdditionalWindow | None = None
    secondary_window: AccountAdditionalWindow | None = None


class AccountSummary(DashboardModel):
    account_id: str
    email: str
    display_name: str
    plan_type: str
    routing_policy: str = "normal"
    status: str
    usage: AccountUsage | None = None
    reset_at_primary: datetime | None = None
    reset_at_secondary: datetime | None = None
    window_minutes_primary: int | None = None
    window_minutes_secondary: int | None = None
    last_refresh_at: datetime | None = None
    capacity_credits_primary: float | None = None
    remaining_credits_primary: float | None = None
    capacity_credits_secondary: float | None = None
    remaining_credits_secondary: float | None = None
    request_usage: AccountRequestUsage | None = None
    additional_quotas: list[AccountAdditionalQuota] = Field(default_factory=list)
    deactivation_reason: str | None = None
    auth: AccountAuthStatus | None = None


class AccountsResponse(DashboardModel):
    accounts: List[AccountSummary] = Field(default_factory=list)


class AccountImportResponse(DashboardModel):
    account_id: str
    email: str
    plan_type: str
    status: str


class AccountPauseResponse(DashboardModel):
    status: str


class AccountReactivateResponse(DashboardModel):
    status: str


class AccountRoutingPolicyUpdate(DashboardModel):
    routing_policy: str


class AccountRoutingPolicyResponse(DashboardModel):
    status: str
    routing_policy: str


class AccountDeleteResponse(DashboardModel):
    status: str


class AccountTrendsResponse(DashboardModel):
    account_id: str
    primary: list[UsageTrendPoint] = Field(default_factory=list)
    secondary: list[UsageTrendPoint] = Field(default_factory=list)
