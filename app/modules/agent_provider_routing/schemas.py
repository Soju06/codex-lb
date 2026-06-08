from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.agent_providers.schemas import AgentProviderId
from app.modules.shared.schemas import DashboardModel

ProviderRoutingStrategy = Literal[
    "capacity_weighted",
    "round_robin",
    "sequential_drain",
    "reset_drain",
    "single_account",
    "ordered_fallback",
]


class AgentProviderQuotaWindowResponse(DashboardModel):
    dimension: str
    used: int
    limit: int | None = None
    reset_at: datetime | None = None
    recorded_at: datetime


class AgentProviderQuotaWindowUpsertRequest(DashboardModel):
    dimension: str = Field(min_length=1, max_length=120)
    used: int = Field(ge=0)
    limit: int | None = Field(default=None, ge=0)
    reset_at: datetime | None = None


class AgentProviderRoutingSettingsResponse(DashboardModel):
    provider_id: AgentProviderId
    strategy: ProviderRoutingStrategy
    single_account_id: str | None = None
    ordered_account_ids: list[str] = Field(default_factory=list)
    quota_threshold_pct: float
    round_robin_cursor: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentProviderRoutingSettingsUpdateRequest(DashboardModel):
    strategy: ProviderRoutingStrategy | None = None
    single_account_id: str | None = None
    ordered_account_ids: list[str] | None = None
    quota_threshold_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    round_robin_cursor: str | None = None


class AgentProviderPreflightAccountState(DashboardModel):
    account_id: str
    display_name: str
    status: str
    quota_windows: list[AgentProviderQuotaWindowResponse] = Field(default_factory=list)


class AgentProviderPreflightResponse(DashboardModel):
    provider_id: AgentProviderId
    selected_account_id: str | None = None
    denied_reason: str | None = None
    candidate_account_ids: list[str] = Field(default_factory=list)
    settings: AgentProviderRoutingSettingsResponse
    accounts: list[AgentProviderPreflightAccountState] = Field(default_factory=list)
