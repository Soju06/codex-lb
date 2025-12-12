from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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


class UsagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_type: str | None = None
    rate_limit: RateLimitPayload | None = None
    credits: CreditsPayload | None = None
