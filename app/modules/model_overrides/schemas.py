from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.modules.shared.schemas import DashboardModel

MatchType = Literal["ip", "app", "api_key"]


class ModelOverrideEntry(DashboardModel):
    id: int
    match_type: MatchType
    match_value: str
    forced_model: str
    forced_reasoning_effort: str | None = None
    enabled: bool
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class ModelOverridesResponse(DashboardModel):
    items: list[ModelOverrideEntry] = Field(default_factory=list)


class ModelOverrideCreateRequest(DashboardModel):
    match_type: MatchType
    match_value: str = Field(min_length=1, max_length=256)
    forced_model: str = Field(min_length=1, max_length=256)
    forced_reasoning_effort: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("match_value", "forced_model", mode="before")
    @classmethod
    def _trim_required(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("forced_reasoning_effort", mode="before")
    @classmethod
    def _trim_optional(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ModelOverrideUpdateRequest(DashboardModel):
    match_value: str | None = Field(default=None, min_length=1, max_length=256)
    forced_model: str | None = Field(default=None, min_length=1, max_length=256)
    forced_reasoning_effort: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("match_value", "forced_model", mode="before")
    @classmethod
    def _trim_required(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("forced_reasoning_effort", mode="before")
    @classmethod
    def _trim_optional(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

