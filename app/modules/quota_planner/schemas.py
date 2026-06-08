from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class QuotaPlannerSettingsResponse(DashboardModel):
    mode: str = Field(pattern=r"^(off|shadow|suggest|auto)$")
    timezone: str
    working_days: list[int]
    working_hours_start: str = Field(pattern=r"^\d{2}:\d{2}$")
    working_hours_end: str = Field(pattern=r"^\d{2}:\d{2}$")
    prewarm_enabled: bool
    prewarm_lead_minutes: int = Field(ge=0, le=1440)
    max_warmups_per_day: int = Field(ge=0)
    max_warmup_credits_per_day: float = Field(ge=0)
    min_expected_gain: float = Field(ge=0)
    forecast_quantile: str = Field(pattern=r"^(p50|p75|p90)$")
    allow_synthetic_traffic: bool
    warmup_model_preference: str | None
    dry_run: bool


class QuotaPlannerSettingsUpdateRequest(DashboardModel):
    mode: str | None = Field(default=None, pattern=r"^(off|shadow|suggest|auto)$")
    timezone: str | None = None
    working_days: list[int] | None = None
    working_hours_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    working_hours_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    prewarm_enabled: bool | None = None
    prewarm_lead_minutes: int | None = Field(default=None, ge=0, le=1440)
    max_warmups_per_day: int | None = Field(default=None, ge=0)
    max_warmup_credits_per_day: float | None = Field(default=None, ge=0)
    min_expected_gain: float | None = Field(default=None, ge=0)
    forecast_quantile: str | None = Field(default=None, pattern=r"^(p50|p75|p90)$")
    allow_synthetic_traffic: bool | None = None
    warmup_model_preference: str | None = None
    dry_run: bool | None = None


class QuotaPlannerDecisionResponse(DashboardModel):
    id: str
    created_at: datetime
    mode: str
    account_id: str | None
    action: str
    scheduled_at: datetime | None
    executed_at: datetime | None
    score: float
    reason: str | None
    details: dict[str, Any] | None = None
    status: str
    idempotency_key: str


class QuotaPlannerForecastSlotResponse(DashboardModel):
    slot_start: datetime
    demand_units: float
    request_count: float
    source: str


class QuotaPlannerSimulationResponse(DashboardModel):
    loss: float
    unmet_demand: float
    wasted_capacity: float
    cold_start_penalty: float
    synchronization_penalty: float
    forecast_units: float
    served_units: float


class QuotaPlannerForecastResponse(DashboardModel):
    generated_at: datetime
    horizon_hours: int
    slot_seconds: int
    total_demand_units: float
    peak_slot_start: datetime | None
    peak_demand_units: float
    simulation: QuotaPlannerSimulationResponse
    slots: list[QuotaPlannerForecastSlotResponse]


class QuotaPlannerWarmNowRequest(DashboardModel):
    account_id: str = Field(min_length=1)
    model: str | None = None
    api_key_id: str | None = None
    force_probe: bool = False


class QuotaPlannerWarmupActionResponse(DashboardModel):
    decision_id: str
    status: str
    reason: str
    request_id: str | None = None
    executed_at: datetime | None = None
