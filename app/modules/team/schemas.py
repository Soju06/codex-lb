from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel

TEAM_STATUS_PATTERN = r"^(active|suspended)$"
TEAM_WINDOW_PATTERN = r"^(day|week|month)$"


class TeamUsageWindowResponse(DashboardModel):
    cost_usd: float = 0.0
    tokens: int = 0


class TeamUsageResponse(DashboardModel):
    day: TeamUsageWindowResponse
    week: TeamUsageWindowResponse
    month: TeamUsageWindowResponse


class TeamMemberKeyResponse(DashboardModel):
    id: str
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: datetime | None = None


class TeamMemberResponse(DashboardModel):
    id: str
    name: str
    email: str | None = None
    status: str
    cost_cap_day_usd: float | None = None
    cost_cap_week_usd: float | None = None
    cost_cap_month_usd: float | None = None
    token_cap_day: int | None = None
    token_cap_week: int | None = None
    token_cap_month: int | None = None
    allowed_models: list[str] | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
    usage: TeamUsageResponse
    gate: str
    keys: list[TeamMemberKeyResponse] = Field(default_factory=list)


class TeamMemberCreateRequest(DashboardModel):
    name: str = Field(min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=320)
    status: str | None = Field(default=None, pattern=TEAM_STATUS_PATTERN)
    cost_cap_day_usd: float | None = Field(default=None, gt=0)
    cost_cap_week_usd: float | None = Field(default=None, gt=0)
    cost_cap_month_usd: float | None = Field(default=None, gt=0)
    token_cap_day: int | None = Field(default=None, gt=0)
    token_cap_week: int | None = Field(default=None, gt=0)
    token_cap_month: int | None = Field(default=None, gt=0)
    allowed_models: list[str] | None = None
    notes: str | None = Field(default=None, max_length=4000)


class TeamMemberUpdateRequest(DashboardModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=320)
    status: str | None = Field(default=None, pattern=TEAM_STATUS_PATTERN)
    cost_cap_day_usd: float | None = Field(default=None, gt=0)
    cost_cap_week_usd: float | None = Field(default=None, gt=0)
    cost_cap_month_usd: float | None = Field(default=None, gt=0)
    token_cap_day: int | None = Field(default=None, gt=0)
    token_cap_week: int | None = Field(default=None, gt=0)
    token_cap_month: int | None = Field(default=None, gt=0)
    allowed_models: list[str] | None = None
    notes: str | None = Field(default=None, max_length=4000)


class TeamMemberKeyCreateRequest(DashboardModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    expires_at: datetime | None = None


class TeamUsageModelResponse(DashboardModel):
    model: str
    cost_usd: float = 0.0
    tokens: int = 0
    requests: int = 0


class TeamUsageDayResponse(DashboardModel):
    day: date
    cost_usd: float = 0.0
    tokens: int = 0


class TeamMemberUsageResponse(DashboardModel):
    member_id: str
    window: str
    window_start: datetime
    window_end: datetime
    totals: TeamUsageWindowResponse
    models: list[TeamUsageModelResponse] = Field(default_factory=list)
    series: list[TeamUsageDayResponse] = Field(default_factory=list)


class TeamOnboardingSnippets(DashboardModel):
    windows_powershell: str
    macos_zsh: str


class TeamOnboardingResponse(DashboardModel):
    base_url: str
    snippets: TeamOnboardingSnippets
