from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field, field_validator

from app.modules.shared.schemas import DashboardModel
from app.modules.upstream_identities.types import PHASE1_PLATFORM_ROUTE_FAMILIES


class UsageTrendPoint(DashboardModel):
    t: datetime
    v: float


class AccountUsageTrend(DashboardModel):
    primary: list[UsageTrendPoint] = Field(default_factory=list)
    secondary: list[UsageTrendPoint] = Field(default_factory=list)


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
    display_name: str
    plan_type: str
    status: str
    provider_kind: str | None = None
    routing_subject_id: str | None = None
    label: str | None = None
    eligible_route_families: list[str] = Field(default_factory=list)
    last_validated_at: datetime | None = None
    last_auth_failure_reason: str | None = None
    organization: str | None = None
    project: str | None = None
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


class AccountsResponse(DashboardModel):
    accounts: List[AccountSummary] = Field(default_factory=list)


class AccountImportResponse(DashboardModel):
    account_id: str
    email: str
    plan_type: str
    status: str


class PlatformIdentityCreateRequest(DashboardModel):
    label: str
    api_key: str
    organization: str | None = None
    project: str | None = None
    eligible_route_families: list[str] = Field(default_factory=list)

    @field_validator("label", "api_key", mode="before")
    @classmethod
    def _strip_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("organization", "project", mode="before")
    @classmethod
    def _strip_optional_strings(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("eligible_route_families")
    @classmethod
    def _validate_route_families(cls, value: list[str]) -> list[str]:
        invalid = [item for item in value if item not in PHASE1_PLATFORM_ROUTE_FAMILIES]
        if invalid:
            raise ValueError(f"Unsupported route families: {', '.join(sorted(invalid))}")
        return value


class PlatformIdentityUpdateRequest(DashboardModel):
    label: str | None = None
    api_key: str | None = None
    organization: str | None = None
    project: str | None = None
    eligible_route_families: list[str] | None = None

    @field_validator("label", "api_key", mode="before")
    @classmethod
    def _strip_optional_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("label", "api_key")
    @classmethod
    def _reject_blank_required_strings(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("Field cannot be blank")
        return value

    @field_validator("organization", "project", mode="before")
    @classmethod
    def _strip_optional_strings(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("eligible_route_families")
    @classmethod
    def _validate_optional_route_families(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        invalid = [item for item in value if item not in PHASE1_PLATFORM_ROUTE_FAMILIES]
        if invalid:
            raise ValueError(f"Unsupported route families: {', '.join(sorted(invalid))}")
        return value


class AccountPauseResponse(DashboardModel):
    status: str


class AccountReactivateResponse(DashboardModel):
    status: str


class AccountDeleteResponse(DashboardModel):
    status: str


class AccountTrendsResponse(DashboardModel):
    account_id: str
    primary: list[UsageTrendPoint] = Field(default_factory=list)
    secondary: list[UsageTrendPoint] = Field(default_factory=list)
