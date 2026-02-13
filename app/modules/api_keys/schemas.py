from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class ApiKeyCreateRequest(DashboardModel):
    name: str = Field(min_length=1, max_length=128)
    allowed_models: list[str] | None = None
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None


class ApiKeyUpdateRequest(DashboardModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    allowed_models: list[str] | None = None
    weekly_token_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    is_active: bool | None = None


class ApiKeyResponse(DashboardModel):
    id: str
    name: str
    key_prefix: str
    allowed_models: list[str] | None
    weekly_token_limit: int | None
    weekly_tokens_used: int
    weekly_reset_at: datetime
    expires_at: datetime | None
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreateResponse(ApiKeyResponse):
    key: str


class ApiKeyListResponse(DashboardModel):
    keys: list[ApiKeyResponse]
