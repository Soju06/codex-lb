from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.core.types import JsonValue
from app.modules.proxy.types import (
    CreditStatusDetailsData,
    RateLimitStatusDetailsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)


class RateLimitWindowSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    used_percent: int
    limit_window_seconds: int
    reset_after_seconds: int
    reset_at: int

    @classmethod
    def from_data(cls, data: RateLimitWindowSnapshotData) -> "RateLimitWindowSnapshot":
        return cls(
            used_percent=data.used_percent,
            limit_window_seconds=data.limit_window_seconds,
            reset_after_seconds=data.reset_after_seconds,
            reset_at=data.reset_at,
        )


class RateLimitStatusDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    allowed: bool
    limit_reached: bool
    primary_window: RateLimitWindowSnapshot | None = None
    secondary_window: RateLimitWindowSnapshot | None = None

    @classmethod
    def from_data(cls, data: RateLimitStatusDetailsData) -> "RateLimitStatusDetails":
        return cls(
            allowed=data.allowed,
            limit_reached=data.limit_reached,
            primary_window=RateLimitWindowSnapshot.from_data(data.primary_window) if data.primary_window else None,
            secondary_window=RateLimitWindowSnapshot.from_data(data.secondary_window)
            if data.secondary_window
            else None,
        )


class CreditStatusDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    has_credits: bool
    unlimited: bool
    balance: str | None = None
    approx_local_messages: list[JsonValue] | None = None
    approx_cloud_messages: list[JsonValue] | None = None

    @classmethod
    def from_data(cls, data: CreditStatusDetailsData) -> "CreditStatusDetails":
        return cls(
            has_credits=data.has_credits,
            unlimited=data.unlimited,
            balance=data.balance,
            approx_local_messages=data.approx_local_messages,
            approx_cloud_messages=data.approx_cloud_messages,
        )


class RateLimitStatusPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_type: str
    rate_limit: RateLimitStatusDetails | None = None
    credits: CreditStatusDetails | None = None

    @classmethod
    def from_data(cls, data: RateLimitStatusPayloadData) -> "RateLimitStatusPayload":
        return cls(
            plan_type=data.plan_type,
            rate_limit=RateLimitStatusDetails.from_data(data.rate_limit) if data.rate_limit else None,
            credits=CreditStatusDetails.from_data(data.credits) if data.credits else None,
        )


class ReasoningLevelSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effort: str
    description: str


class ModelMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    description: str
    context_window: int
    input_modalities: list[str]
    supported_reasoning_levels: list[ReasoningLevelSchema]
    default_reasoning_level: str | None = None
    supports_reasoning_summaries: bool = False
    support_verbosity: bool = False
    default_verbosity: str | None = None
    prefer_websockets: bool = False
    supports_parallel_tool_calls: bool = False
    supported_in_api: bool = True
    minimal_client_version: str | None = None
    priority: int = 0


class ModelListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: str = "model"
    created: int
    owned_by: str
    metadata: ModelMetadata | None = None


class ModelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: str = "list"
    data: list[ModelListItem]
