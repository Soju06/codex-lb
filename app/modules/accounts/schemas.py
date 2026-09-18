from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field, SecretStr

from app.modules.accounts.subscription_status import SUBSCRIPTION_STATUS_PATTERN
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
    monthly_remaining_percent: float | None = None


class AccountRequestUsage(DashboardModel):
    request_count: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
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


class AccountCreditsWindow(DashboardModel):
    balance: float
    cap: float | None = None


class AccountAdditionalQuota(DashboardModel):
    quota_key: str | None = None
    limit_name: str
    metered_feature: str
    display_label: str | None = None
    routing_policy: str = Field(default="inherit", pattern=r"^(inherit|normal|burn_first|preserve)$")
    model_ids: list[str] = Field(default_factory=list)
    primary_window: AccountAdditionalWindow | None = None
    secondary_window: AccountAdditionalWindow | None = None


class AccountSubscriptionLedger(DashboardModel):
    status: str | None = Field(default=None, pattern=SUBSCRIPTION_STATUS_PATTERN)
    next_charge_at: datetime | None = None
    current_period_end_at: datetime | None = None
    amount: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    last_verified_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AccountIdentityMismatch(DashboardModel):
    """Chronic usage-refresh identity mismatch (e.g. a lapsed plan).

    Present only after the escalation threshold of consecutive refresh cycles
    whose payload identity contradicted the stored slot — every one of those
    refreshes was discarded, so the account's usage data is stale.
    """

    count: int
    first_at: datetime
    last_at: datetime
    stored_plan_type: str | None = None
    payload_plan_type: str | None = None


class AccountSummary(DashboardModel):
    account_id: str
    provider: str = "openai"
    email: str
    alias: str | None = None
    display_name: str
    workspace_id: str | None = None
    workspace_label: str | None = None
    seat_type: str | None = None
    plan_type: str
    routing_policy: str = Field(default="normal", pattern=r"^(normal|burn_first|preserve)$")
    status: str
    security_work_authorized: bool = False
    # Anthropic only: weekly usage below the Fable threshold, i.e. the balancer
    # will consider this account for Fable-class requests. Null for other providers.
    fable_eligible: bool | None = None
    usage: AccountUsage | None = None
    reset_at_primary: datetime | None = None
    reset_at_secondary: datetime | None = None
    reset_at_monthly: datetime | None = None
    rate_limit_reset_at: datetime | None = None
    window_minutes_primary: int | None = None
    window_minutes_secondary: int | None = None
    window_minutes_monthly: int | None = None
    last_refresh_at: datetime | None = None
    capacity_credits_primary: float | None = None
    remaining_credits_primary: float | None = None
    capacity_credits_secondary: float | None = None
    remaining_credits_secondary: float | None = None
    capacity_credits_monthly: float | None = None
    remaining_credits_monthly: float | None = None
    request_usage: AccountRequestUsage | None = None
    additional_quotas: list[AccountAdditionalQuota] = Field(default_factory=list)
    credits_has: bool | None = None
    credits_unlimited: bool | None = None
    credits_balance: float | None = None
    credits_cap: float | None = None
    credits_spent: float | None = None
    credits: AccountCreditsWindow | None = None
    reset_credits_available: int | None = None
    subscription: AccountSubscriptionLedger | None = None
    deactivation_reason: str | None = None
    auth: AccountAuthStatus | None = None
    limit_warmup_enabled: bool = False
    limit_warmup: AccountLimitWarmupStatus | None = None
    # True when another account row in the same response shares this real email,
    # ChatGPT account identity, and workspace slot.
    # Operators see this after a token-invalidation cascade where re-adding
    # via OAuth creates a side-by-side row with a fresh refresh token; the
    # older row keeps a revoked token and keeps generating 401s through the
    # load balancer. Flagging the dupes in /accounts lets the dashboard
    # surface a "delete older" action without requiring the operator to
    # group rows by email themselves. See agent-lb #787 (B).
    is_email_duplicate: bool = False
    # Escalated usage-refresh identity mismatch (see AccountIdentityMismatch);
    # null while refreshes are healthy or below the escalation threshold.
    identity_mismatch: AccountIdentityMismatch | None = None
    # Instance federation: which agent-lb instance currently owns OAuth refresh
    # for this account. Null means this instance (the classic, non-federated
    # case). is_locally_owned is the derived convenience flag the dashboard
    # badges off of.
    owner_instance: str | None = None
    is_locally_owned: bool = True


class AccountsResponse(DashboardModel):
    accounts: List[AccountSummary] = Field(default_factory=list)


class AvailabilityUnavailableAccount(DashboardModel):
    id: str
    status: str
    reset_at: datetime | None = None


class ProviderAvailability(DashboardModel):
    total: int
    available: int
    unavailable: List[AvailabilityUnavailableAccount] = Field(default_factory=list)
    earliest_recovery_at: datetime | None = None


class DegradationStatus(DashboardModel):
    level: str
    reason: str | None = None


class AvailabilityResponse(DashboardModel):
    """Fleet-wide account availability, aggregated per provider."""

    degradation: DegradationStatus
    providers: dict[str, ProviderAvailability] = Field(default_factory=dict)


class AccountImportResponse(DashboardModel):
    account_id: str
    email: str
    workspace_id: str | None = None
    workspace_label: str | None = None
    seat_type: str | None = None
    plan_type: str
    status: str


class AccountApiKeyImportRequest(DashboardModel):
    provider: str = Field(default="glm", pattern=r"^(glm|openrouter)$")
    api_key: SecretStr = Field(min_length=1)
    email: str = Field(default="glm@z.ai", min_length=3, max_length=320)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    alias: str | None = Field(default="GLM Coding Plan", max_length=200)
    plan_type: str = Field(default="glm-coding", min_length=1, max_length=64)
    credits_balance: float | None = None
    credits_cap: float | None = None
    credits_spent: float | None = None


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


class AccountUpdateRequest(DashboardModel):
    security_work_authorized: bool | None = None


class AccountUpdateResponse(DashboardModel):
    status: str


class AccountPauseResponse(DashboardModel):
    status: str


class AccountReactivateResponse(DashboardModel):
    status: str


class AccountLimitWarmupUpdateRequest(DashboardModel):
    enabled: bool


class AccountLimitWarmupUpdateResponse(DashboardModel):
    status: str
    enabled: bool


class AccountRoutingPolicyUpdateRequest(DashboardModel):
    routing_policy: str = Field(pattern=r"^(normal|burn_first|preserve)$")


class AccountRoutingPolicyUpdateResponse(DashboardModel):
    account_id: str
    routing_policy: str


class AccountSubscriptionUpdateRequest(AccountSubscriptionLedger):
    pass


class AccountSubscriptionUpdateResponse(DashboardModel):
    account_id: str
    subscription: AccountSubscriptionLedger | None = None


class AccountSubscriptionCheckResponse(DashboardModel):
    status: str
    account_id: str
    working: bool
    probe_status_code: int
    subscription: AccountSubscriptionLedger | None = None
    message: str | None = None


class AccountDeleteResponse(DashboardModel):
    status: str


class AccountExportResponse(DashboardModel):
    account_id: str
    email: str
    workspace_id: str | None = None
    workspace_label: str | None = None
    seat_type: str | None = None
    plan_type: str
    status: str
    auth_json: str


class AccountProbeRequest(DashboardModel):
    model: str | None = Field(
        default=None,
        description=(
            "Optional model slug for the probe request. Defaults to the service's configured fallback when omitted."
        ),
    )


class AccountProbeResponse(DashboardModel):
    status: str
    account_id: str
    probe_status_code: int
    primary_used_percent_before: float | None = None
    primary_used_percent_after: float | None = None
    secondary_used_percent_before: float | None = None
    secondary_used_percent_after: float | None = None
    account_status_before: str
    account_status_after: str


class AccountResetCredit(DashboardModel):
    id: str
    reset_type: str
    status: str
    granted_at: str
    expires_at: str | None = None
    title: str | None = None
    description: str | None = None


class AccountResetCreditsResponse(DashboardModel):
    account_id: str
    available_count: int
    credits: list[AccountResetCredit] = Field(default_factory=list)


class AccountResetCreditConsumeRequest(DashboardModel):
    credit_id: str | None = Field(
        default=None,
        description="Optional reset-credit id to redeem. Upstream picks the next available credit when omitted.",
    )


class AccountResetCreditConsumeResponse(DashboardModel):
    status: str
    account_id: str
    code: str
    windows_reset: int
    primary_used_percent_before: float | None = None
    primary_used_percent_after: float | None = None
    secondary_used_percent_before: float | None = None
    secondary_used_percent_after: float | None = None


class AccountTrendsResponse(DashboardModel):
    account_id: str
    primary: list[UsageTrendPoint] = Field(default_factory=list)
    secondary: list[UsageTrendPoint] = Field(default_factory=list)
    secondary_scheduled: list[UsageTrendPoint] = Field(default_factory=list)


class CodexAuthTokens(DashboardModel):
    id_token: str = Field(serialization_alias="id_token", validation_alias="id_token")
    access_token: str = Field(serialization_alias="access_token", validation_alias="access_token")
    refresh_token: str = Field(serialization_alias="refresh_token", validation_alias="refresh_token")
    account_id: str | None = Field(
        default=None,
        serialization_alias="account_id",
        validation_alias="account_id",
    )


class CodexAuthJson(DashboardModel):
    auth_mode: str = Field(default="chatgpt", serialization_alias="auth_mode", validation_alias="auth_mode")
    openai_api_key: str | None = Field(
        default=None,
        serialization_alias="OPENAI_API_KEY",
        validation_alias="OPENAI_API_KEY",
    )
    tokens: CodexAuthTokens
    last_refresh: str = Field(serialization_alias="last_refresh", validation_alias="last_refresh")


class AccountAuthExportTokens(DashboardModel):
    id_token: str
    access_token: str
    refresh_token: str
    expires_at_ms: int = Field(ge=0)


class AccountAuthExportResponse(DashboardModel):
    filename: str
    account: AccountOpenCodeAuthExportAccount
    tokens: AccountAuthExportTokens
    codex_auth_json: CodexAuthJson
    opencode_auth_json: OpenCodeAuthJson


class AccountAliasRequest(DashboardModel):
    alias: str | None = Field(default=None, max_length=255)


class AccountAliasResponse(DashboardModel):
    account_id: str
    alias: str | None = None
