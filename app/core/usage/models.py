from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class UsageWindow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    used_percent: float | None = None
    reset_at: int | None = None
    limit_window_seconds: int | None = None
    reset_after_seconds: int | None = None


class RateLimitPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    primary_window: UsageWindow | None = None
    secondary_window: UsageWindow | None = None

    def spark_windows(self) -> list[tuple[str, UsageWindow]]:
        extras = self.model_extra or {}
        windows: list[tuple[str, UsageWindow]] = []
        for key, value in extras.items():
            if "spark" not in key.lower():
                continue
            try:
                window = UsageWindow.model_validate(value)
            except Exception:
                continue
            windows.append((key, window))
        windows.sort(key=lambda item: item[0].lower())
        return windows


class AdditionalRateLimitPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit_name: str | None = None
    rate_limit: RateLimitPayload | None = None


class CreditsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    has_credits: bool | None = None
    unlimited: bool | None = None
    balance: str | None = None


class UsagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_type: str | None = None
    rate_limit: RateLimitPayload | None = None
    additional_rate_limits: list[AdditionalRateLimitPayload] | None = None
    credits: CreditsPayload | None = None
