from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


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
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    limits: list[LimitRuleCreate] | None = None


class ApiKeyUpdateRequest(DashboardModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    allowed_models: list[str] | None = None
    apply_to_codex_model: bool | None = None
    enforced_model: str | None = Field(default=None, min_length=1)
    enforced_reasoning_effort: str | None = Field(default=None, pattern=r"(?i)^(none|minimal|low|medium|high|xhigh)$")
    enforced_service_tier: str | None = Field(default=None, pattern=r"(?i)^(auto|default|priority|flex|fast)$")
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    is_active: bool | None = None
    assigned_account_ids: list[str] | None = None
    limits: list[LimitRuleCreate] | None = None
    reset_usage: bool | None = None


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
    expires_at: datetime | None
    is_active: bool
    account_assignment_scope_enabled: bool = False
    assigned_account_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    last_used_at: datetime | None
    limits: list[LimitRuleResponse] = Field(default_factory=list)
    usage_summary: ApiKeyUsageSummaryResponse | None = None


class ApiKeyCreateResponse(ApiKeyResponse):
    key: str


class ApiKeyTrendPoint(DashboardModel):
    t: datetime
    v: float


class ApiKeyTrendsResponse(DashboardModel):
    key_id: str
    cost: list[ApiKeyTrendPoint] = Field(default_factory=list)
    tokens: list[ApiKeyTrendPoint] = Field(default_factory=list)


class ApiKeyUsage7DayResponse(DashboardModel):
    key_id: str
    total_tokens: int = 0
    total_cost_usd: float = 0
    total_requests: int = 0
    cached_input_tokens: int = 0
