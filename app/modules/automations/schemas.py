from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.modules.shared.schemas import DashboardModel

AUTOMATION_SCHEDULE_TYPES = ("daily",)
AUTOMATION_WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
AUTOMATION_RUN_STATUSES = ("running", "success", "failed", "partial")
AUTOMATION_RUN_TRIGGERS = ("scheduled", "manual")
AUTOMATION_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")

AutomationWeekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class AutomationScheduleRequest(DashboardModel):
    type: Literal["daily"] = "daily"
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    timezone: str = Field(min_length=1, max_length=64)
    threshold_minutes: int = Field(default=0, ge=0, le=240)
    days: list[AutomationWeekday] = Field(
        default_factory=lambda: list(AUTOMATION_WEEKDAY_CODES),
        min_length=1,
        max_length=7,
    )

    @field_validator("days")
    @classmethod
    def _validate_days(cls, value: list[AutomationWeekday]) -> list[AutomationWeekday]:
        deduped: list[AutomationWeekday] = []
        seen: set[str] = set()
        for day in value:
            if day in seen:
                continue
            seen.add(day)
            deduped.append(day)
        if not deduped:
            raise ValueError("At least one schedule day is required")
        return deduped


class AutomationScheduleResponse(DashboardModel):
    type: Literal["daily"]
    time: str
    timezone: str
    threshold_minutes: int
    days: list[AutomationWeekday]


class AutomationJobCreateRequest(DashboardModel):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    include_paused_accounts: bool = Field(default=False, alias="includePausedAccounts")
    schedule: AutomationScheduleRequest
    model: str = Field(min_length=1)
    reasoning_effort: str | None = Field(default=None, pattern=r"(?i)^(minimal|low|medium|high|xhigh)$")
    prompt: str | None = Field(default=None, max_length=1000)
    account_ids: list[str] = Field(default_factory=list, max_length=128, alias="accountIds")

    @field_validator("account_ids")
    @classmethod
    def _validate_unique_account_ids(cls, value: list[str]) -> list[str]:
        normalized = [account_id.strip() for account_id in value if account_id.strip()]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Duplicate account IDs are not allowed")
        return normalized


class AutomationJobUpdateRequest(DashboardModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    include_paused_accounts: bool | None = Field(default=None, alias="includePausedAccounts")
    schedule: AutomationScheduleRequest | None = None
    model: str | None = Field(default=None, min_length=1)
    reasoning_effort: str | None = Field(default=None, pattern=r"(?i)^(minimal|low|medium|high|xhigh)$")
    prompt: str | None = Field(default=None, max_length=1000)
    account_ids: list[str] | None = Field(default=None, max_length=128, alias="accountIds")

    @field_validator("account_ids")
    @classmethod
    def _validate_unique_account_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [account_id.strip() for account_id in value if account_id.strip()]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Duplicate account IDs are not allowed")
        return normalized


class AutomationRunResponse(DashboardModel):
    id: str
    job_id: str
    job_name: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    trigger: Literal["scheduled", "manual"]
    status: Literal["running", "success", "failed", "partial"]
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime | None = None
    account_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempt_count: int = Field(ge=0)
    effective_status: Literal["running", "success", "failed", "partial"] | None = None
    total_accounts: int | None = Field(default=None, ge=0)
    completed_accounts: int | None = Field(default=None, ge=0)
    pending_accounts: int | None = Field(default=None, ge=0)
    cycle_key: str | None = None


class AutomationJobResponse(DashboardModel):
    id: str
    name: str
    enabled: bool
    include_paused_accounts: bool = False
    schedule: AutomationScheduleResponse
    model: str
    reasoning_effort: str | None = None
    prompt: str
    account_ids: list[str]
    next_run_at: datetime | None = None
    last_run: AutomationRunResponse | None = None


class AutomationJobsListResponse(DashboardModel):
    items: list[AutomationJobResponse] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


class AutomationRunsListResponse(DashboardModel):
    items: list[AutomationRunResponse] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


class AutomationJobFilterOptionsResponse(DashboardModel):
    account_ids: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    schedule_types: list[str] = Field(default_factory=list)


class AutomationRunFilterOptionsResponse(DashboardModel):
    account_ids: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)


class AutomationRunAccountStateResponse(DashboardModel):
    account_id: str
    status: Literal["pending", "running", "success", "failed", "partial"]
    run_id: str | None = None
    scheduled_for: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


class AutomationRunDetailsResponse(DashboardModel):
    run: AutomationRunResponse
    accounts: list[AutomationRunAccountStateResponse] = Field(default_factory=list)
    total_accounts: int = Field(ge=0)
    completed_accounts: int = Field(ge=0)
    pending_accounts: int = Field(ge=0)


class AutomationDeleteResponse(DashboardModel):
    status: Literal["deleted"]
