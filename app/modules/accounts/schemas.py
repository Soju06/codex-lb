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
    secondary_scheduled: list[UsageTrendPoint] = Field(default_factory=list)


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


class AccountLimitWarmupStatus(DashboardModel):
    window: str
    reset_at: int
    status: str
    model: str
    attempted_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


class AccountAdditionalWindow(DashboardModel):
    used_percent: float
    reset_at: int | None = None
    window_minutes: int | None = None


class AccountAdditionalQuota(DashboardModel):
    quota_key: str | None = None
    limit_name: str
    metered_feature: str
    display_label: str | None = None
    primary_window: AccountAdditionalWindow | None = None
    secondary_window: AccountAdditionalWindow | None = None


class AccountSummary(DashboardModel):
    account_id: str
    email: str
    alias: str | None = None
    display_name: str
    plan_type: str
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
    limit_warmup_enabled: bool = False
    limit_warmup: AccountLimitWarmupStatus | None = None
    # True when another account row in the same response shares this real email
    # and ChatGPT account identity.
    # Operators see this after a token-invalidation cascade where re-adding
    # via OAuth creates a side-by-side row with a fresh refresh token; the
    # older row keeps a revoked token and keeps generating 401s through the
    # load balancer. Flagging the dupes in /accounts lets the dashboard
    # surface a "delete older" action without requiring the operator to
    # group rows by email themselves. See codex-lb #787 (B).
    is_email_duplicate: bool = False


class AccountsResponse(DashboardModel):
    accounts: List[AccountSummary] = Field(default_factory=list)


class AccountImportResponse(DashboardModel):
    account_id: str
    email: str
    plan_type: str
    status: str


class OpenCodeOAuthAuth(DashboardModel):
    type: str = "oauth"
    refresh: str
    access: str
    expires: int = Field(ge=0)
    account_id: str | None = None


class OpenCodeAuthJson(DashboardModel):
    openai: OpenCodeOAuthAuth


class AccountOpenCodeAuthExportAccount(DashboardModel):
    account_id: str
    chatgpt_account_id: str | None = None
    email: str


class AccountOpenCodeAuthExportResponse(DashboardModel):
    filename: str
    account: AccountOpenCodeAuthExportAccount
    auth_json: OpenCodeAuthJson


class AccountPauseResponse(DashboardModel):
    status: str


class AccountReactivateResponse(DashboardModel):
    status: str


class AccountLimitWarmupUpdateRequest(DashboardModel):
    enabled: bool


class AccountLimitWarmupUpdateResponse(DashboardModel):
    status: str
    enabled: bool


class AccountDeleteResponse(DashboardModel):
    status: str


class AccountExportResponse(DashboardModel):
    account_id: str
    email: str
    plan_type: str
    status: str
    auth_json: str


class AccountTrendsResponse(DashboardModel):
    account_id: str
    primary: list[UsageTrendPoint] = Field(default_factory=list)
    secondary: list[UsageTrendPoint] = Field(default_factory=list)
    secondary_scheduled: list[UsageTrendPoint] = Field(default_factory=list)


class AccountAliasRequest(DashboardModel):
    alias: str | None = Field(default=None, max_length=255)


class AccountAliasResponse(DashboardModel):
    account_id: str
    alias: str | None = None
