from __future__ import annotations

from dataclasses import dataclass, field

from app.core.types import JsonValue


@dataclass(frozen=True)
class RateLimitWindowSnapshotData:
    used_percent: int
    limit_window_seconds: int | None = None
    reset_after_seconds: int | None = None
    reset_at: int | None = None


@dataclass(frozen=True)
class RateLimitStatusDetailsData:
    allowed: bool
    limit_reached: bool
    primary_window: RateLimitWindowSnapshotData | None = None
    secondary_window: RateLimitWindowSnapshotData | None = None


@dataclass(frozen=True)
class CreditStatusDetailsData:
    has_credits: bool
    unlimited: bool
    balance: str | None = None
    approx_local_messages: list[JsonValue] | None = None
    approx_cloud_messages: list[JsonValue] | None = None


@dataclass(frozen=True)
class AdditionalRateLimitData:
    limit_name: str
    metered_feature: str
    quota_key: str | None = None
    display_label: str | None = None
    rate_limit: RateLimitStatusDetailsData | None = None


@dataclass(frozen=True)
class RateLimitStatusPayloadData:
    plan_type: str
    rate_limit: RateLimitStatusDetailsData | None = None
    credits: CreditStatusDetailsData | None = None
    additional_rate_limits: list[AdditionalRateLimitData] = field(default_factory=list)
