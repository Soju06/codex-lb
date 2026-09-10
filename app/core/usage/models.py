from __future__ import annotations

from copy import deepcopy

from pydantic import BaseModel, ConfigDict, PrivateAttr

from app.core.types import JsonObject


class UsageWindow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    used_percent: float | None = None
    reset_at: int | None = None
    limit_window_seconds: int | None = None
    reset_after_seconds: int | None = None


class RateLimitPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary_window: UsageWindow | None = None
    secondary_window: UsageWindow | None = None


class CreditsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    has_credits: bool | None = None
    unlimited: bool | None = None
    balance: str | None = None


class RateLimitResetCreditsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    available_count: int | None = None


class AdditionalRateLimitPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit_name: str
    metered_feature: str
    rate_limit: RateLimitPayload | None = None


class UsagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    _raw_payload: JsonObject | None = PrivateAttr(default=None)

    plan_type: str | None = None
    workspace_id: str | None = None
    workspace_label: str | None = None
    seat_type: str | None = None
    rate_limit: RateLimitPayload | None = None
    credits: CreditsPayload | None = None
    rate_limit_reset_credits: RateLimitResetCreditsPayload | None = None
    additional_rate_limits: list[AdditionalRateLimitPayload] | None = None

    @classmethod
    def from_upstream(cls, data: JsonObject) -> UsagePayload:
        """Keep the caller envelope for Desktop without changing parsed serialization."""
        payload = cls.model_validate(data)
        payload._raw_payload = deepcopy(data)
        return payload

    @property
    def raw_payload(self) -> JsonObject | None:
        return self._raw_payload
