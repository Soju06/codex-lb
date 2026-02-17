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
    allowed_models: list[str] | None = None
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    limits: list[LimitRuleCreate] | None = None


class ApiKeyUpdateRequest(DashboardModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    allowed_models: list[str] | None = None
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    is_active: bool | None = None
    limits: list[LimitRuleCreate] | None = None


class ApiKeyResponse(DashboardModel):
    id: str
    name: str
    key_prefix: str
    allowed_models: list[str] | None
    weekly_token_limit: int | None = None
    weekly_tokens_used: int = 0
    weekly_reset_at: datetime | None = None
    expires_at: datetime | None
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    limits: list[LimitRuleResponse] = Field(default_factory=list)


class ApiKeyCreateResponse(ApiKeyResponse):
    key: str


class ApiKeyListResponse(DashboardModel):
    keys: list[ApiKeyResponse]
