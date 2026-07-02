from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.modules.shared.schemas import DashboardModel

_ALLOWED_PROVIDER_SCOPE = frozenset({"codex", "claude"})
_DEFAULT_PROVIDER_SCOPE: list[str] = ["codex"]


def _scope_to_db(scope: list[str]) -> str:
    return ",".join(sorted(set(scope)))


def _scope_from_db(value: str | None) -> list[str]:
    if not value:
        return list(_DEFAULT_PROVIDER_SCOPE)
    parts = [s.strip() for s in value.split(",") if s.strip()]
    return sorted(set(parts)) or list(_DEFAULT_PROVIDER_SCOPE)


def _validate_provider_scope(value: list[str] | None) -> list[str] | None:
    if value is None:
        return value
    if not value:
        raise ValueError("providerScope must contain at least one provider")
    bad = set(value) - _ALLOWED_PROVIDER_SCOPE
    if bad:
        raise ValueError(f"Unknown providers in providerScope: {sorted(bad)}")
    return sorted(set(value))


class LimitRuleCreate(DashboardModel):
    limit_type: str = Field(pattern=r"^(total_tokens|input_tokens|output_tokens|cost_usd|credits)$")
    limit_window: str = Field(pattern=r"^(daily|weekly|monthly|5h|7d)$")
    max_value: int = Field(ge=1)
    model_filter: str | None = None


class LimitRuleResponse(DashboardModel):
    id: int
    limit_type: str
    limit_window: str
    max_value: int
    current_value: int
    model_filter: str | None
    reset_at: datetime


class ApiKeyCreateRequest(DashboardModel):
    name: str = Field(min_length=1, max_length=128)
    allowed_models: list[str] | None = None
    apply_to_codex_model: bool = False
    enforced_model: str | None = Field(default=None, min_length=1)
    enforced_reasoning_effort: str | None = Field(default=None, pattern=r"(?i)^(none|minimal|low|medium|high|xhigh)$")
    enforced_service_tier: str | None = Field(default=None, pattern=r"(?i)^(auto|default|priority|flex|fast)$")
    traffic_class: str | None = Field(default=None, pattern=r"(?i)^(foreground|opportunistic)$")
    transport_policy_override: str | None = None
    usage_sections: str | None = None
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    assigned_account_ids: list[str] | None = None
    limits: list[LimitRuleCreate] | None = None
    provider_scope: list[str] | None = None

    @field_validator("provider_scope")
    @classmethod
    def _check_provider_scope(cls, value: list[str] | None) -> list[str] | None:
        return _validate_provider_scope(value)


class ApiKeyUpdateRequest(DashboardModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    allowed_models: list[str] | None = None
    apply_to_codex_model: bool | None = None
    enforced_model: str | None = Field(default=None, min_length=1)
    enforced_reasoning_effort: str | None = Field(default=None, pattern=r"(?i)^(none|minimal|low|medium|high|xhigh)$")
    enforced_service_tier: str | None = Field(default=None, pattern=r"(?i)^(auto|default|priority|flex|fast)$")
    traffic_class: str | None = Field(default=None, pattern=r"(?i)^(foreground|opportunistic)$")
    transport_policy_override: str | None = None
    usage_sections: str | None = None
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    is_active: bool | None = None
    assigned_account_ids: list[str] | None = None
    limits: list[LimitRuleCreate] | None = None
    reset_usage: bool | None = None
    provider_scope: list[str] | None = None

    @field_validator("provider_scope")
    @classmethod
    def _check_provider_scope(cls, value: list[str] | None) -> list[str] | None:
        return _validate_provider_scope(value)


class ApiKeyUsageSummaryResponse(DashboardModel):
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float


class ApiKeyResponse(DashboardModel):
    id: str
    name: str
    key_prefix: str
    allowed_models: list[str] | None
    apply_to_codex_model: bool = False
    enforced_model: str | None
    enforced_reasoning_effort: str | None
    enforced_service_tier: str | None
    traffic_class: str
    transport_policy_override: str | None = None
    usage_sections: str = "upstream_limits,account_pool_usage"
    expires_at: datetime | None
    is_active: bool
    account_assignment_scope_enabled: bool = False
    assigned_account_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    last_used_at: datetime | None
    limits: list[LimitRuleResponse] = Field(default_factory=list)
    usage_summary: ApiKeyUsageSummaryResponse | None = None
    pooled_remaining_percent_primary: float | None = None
    pooled_remaining_percent_secondary: float | None = None
    pooled_capacity_credits_primary: float = 0.0
    provider_scope: list[str] = Field(default_factory=lambda: list(_DEFAULT_PROVIDER_SCOPE))


class ApiKeyCreateResponse(ApiKeyResponse):
    key: str


class ApiKeyTrendPoint(DashboardModel):
    t: datetime
    v: float


class ApiKeyTrendsResponse(DashboardModel):
    key_id: str
    cost: list[ApiKeyTrendPoint] = Field(default_factory=list)
    tokens: list[ApiKeyTrendPoint] = Field(default_factory=list)


class ApiKeyAccountCostResponse(DashboardModel):
    account_id: str | None = None
    email: str | None = None
    cost_usd: float = 0
    is_deleted: bool = False


class ApiKeyUsage7DayResponse(DashboardModel):
    key_id: str
    total_tokens: int = 0
    total_cost_usd: float = 0
    total_requests: int = 0
    cached_input_tokens: int = 0
    account_costs: list[ApiKeyAccountCostResponse] = Field(default_factory=list)
