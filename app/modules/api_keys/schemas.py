from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class LimitRuleCreate(DashboardModel):
    limit_type: str = Field(pattern=r"^(total_tokens|input_tokens|output_tokens|cost_usd)$")
    limit_window: str = Field(pattern=r"^(daily|weekly|monthly)$")
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
    tags: list[str] | None = None
    allowed_models: list[str] | None = None
    enforced_model: str | None = Field(default=None, min_length=1)
    enforced_reasoning_effort: str | None = Field(default=None, pattern=r"(?i)^(none|minimal|low|medium|high|xhigh)$")
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    limits: list[LimitRuleCreate] | None = None


class ApiKeyUpdateRequest(DashboardModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    tags: list[str] | None = None
    allowed_models: list[str] | None = None
    enforced_model: str | None = Field(default=None, min_length=1)
    enforced_reasoning_effort: str | None = Field(default=None, pattern=r"(?i)^(none|minimal|low|medium|high|xhigh)$")
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    is_active: bool | None = None
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
    tags: list[str] = Field(default_factory=list)
    allowed_models: list[str] | None
    enforced_model: str | None
    enforced_reasoning_effort: str | None
    expires_at: datetime | None
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    limits: list[LimitRuleResponse] = Field(default_factory=list)
    usage_summary: ApiKeyUsageSummaryResponse | None = None


class ApiKeyCreateResponse(ApiKeyResponse):
    key: str
