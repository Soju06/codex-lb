from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.agent_providers.schemas import AgentProviderId
from app.modules.shared.schemas import DashboardModel


class AgentProviderAccountResponse(DashboardModel):
    account_id: str
    provider_id: AgentProviderId
    external_account_id: str | None = None
    display_name: str
    status: str
    auth_mode: str
    api_key_set: bool
    credential_fingerprint: str | None = None
    project_id: str | None = None
    location: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentProviderAccountsResponse(DashboardModel):
    accounts: list[AgentProviderAccountResponse] = Field(default_factory=list)


class GeminiProviderAccountCreateRequest(DashboardModel):
    display_name: str = Field(min_length=1, max_length=120)
    api_key: str = Field(min_length=1, max_length=4096)
    external_account_id: str | None = Field(default=None, max_length=255)
    project_id: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)


class AntigravityProviderAccountCreateRequest(DashboardModel):
    display_name: str = Field(min_length=1, max_length=120)
    auth_mode: str | None = Field(default=None, pattern=r"^(api_key|cli_keyring)$")
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    external_account_id: str | None = Field(default=None, max_length=255)
    project_id: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)


class AgentProviderAccountUpdateRequest(DashboardModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = Field(default=None, pattern=r"^(active|paused)$")
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    external_account_id: str | None = Field(default=None, max_length=255)
    project_id: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
