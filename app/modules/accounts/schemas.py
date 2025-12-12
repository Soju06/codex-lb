from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class AccountUsage(DashboardModel):
    primary_remaining_percent: float | None = None
    secondary_remaining_percent: float | None = None


class AccountTokenStatus(DashboardModel):
    expires_at: datetime | None = None
    state: str | None = None


class AccountAuthStatus(DashboardModel):
    access: AccountTokenStatus | None = None
    refresh: AccountTokenStatus | None = None
    id_token: AccountTokenStatus | None = None


class AccountSummary(DashboardModel):
    account_id: str
    email: str
    display_name: str
    plan_type: str
    status: str
    usage: AccountUsage | None = None
    reset_at_primary: datetime | None = None
    reset_at_secondary: datetime | None = None
    last_refresh_at: datetime | None = None
    capacity_credits_primary: float | None = None
    remaining_credits_primary: float | None = None
    capacity_credits_secondary: float | None = None
    remaining_credits_secondary: float | None = None
    cost_usd_24h: float | None = None
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


class AccountDeleteResponse(DashboardModel):
    status: str
