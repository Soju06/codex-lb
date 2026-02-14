from __future__ import annotations

from dataclasses import dataclass

from app.core.types import JsonValue


@dataclass(frozen=True)
class RateLimitWindowSnapshotData:
    used_percent: int
    limit_window_seconds: int
    reset_after_seconds: int
    reset_at: int


@dataclass(frozen=True)
class RateLimitStatusDetailsData:
    allowed: bool
    limit_reached: bool
    primary_window: RateLimitWindowSnapshotData | None = None
    secondary_window: RateLimitWindowSnapshotData | None = None
    spark_primary_window: RateLimitWindowSnapshotData | None = None
    spark_secondary_window: RateLimitWindowSnapshotData | None = None
    spark_window_label: str | None = None


@dataclass(frozen=True)
class CreditStatusDetailsData:
    has_credits: bool
    unlimited: bool
    balance: str | None = None
    approx_local_messages: list[JsonValue] | None = None
    approx_cloud_messages: list[JsonValue] | None = None


@dataclass(frozen=True)
class RateLimitStatusPayloadData:
    plan_type: str
    rate_limit: RateLimitStatusDetailsData | None = None
    credits: CreditStatusDetailsData | None = None
