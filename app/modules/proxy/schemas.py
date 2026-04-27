from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.core.types import JsonValue
from app.modules.proxy.types import (
    AdditionalRateLimitData,
    CreditStatusDetailsData,
    RateLimitStatusDetailsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)


class RateLimitWindowSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    used_percent: int
    limit_window_seconds: int | None = None
    reset_after_seconds: int | None = None
    reset_at: int | None = None

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


class AdditionalRateLimitStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quota_key: str | None = None
    limit_name: str
    display_label: str | None = None
    metered_feature: str
    rate_limit: RateLimitStatusDetails | None = None

    @classmethod
    def from_data(cls, data: AdditionalRateLimitData) -> "AdditionalRateLimitStatus":
        return cls(
            quota_key=data.quota_key,
            limit_name=data.limit_name,
            display_label=data.display_label,
            metered_feature=data.metered_feature,
            rate_limit=RateLimitStatusDetails.from_data(data.rate_limit) if data.rate_limit else None,
        )


class RateLimitStatusPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_type: str
    rate_limit: RateLimitStatusDetails | None = None
    credits: CreditStatusDetails | None = None
    additional_rate_limits: list[AdditionalRateLimitStatus] = []

    @classmethod
    def from_data(cls, data: RateLimitStatusPayloadData) -> "RateLimitStatusPayload":
        return cls(
            plan_type=data.plan_type,
            rate_limit=RateLimitStatusDetails.from_data(data.rate_limit) if data.rate_limit else None,
            credits=CreditStatusDetails.from_data(data.credits) if data.credits else None,
            additional_rate_limits=[AdditionalRateLimitStatus.from_data(arl) for arl in data.additional_rate_limits],
        )


class ReasoningLevelSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    effort: str
    description: str


class CodexModelEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    display_name: str
    description: str
    base_instructions: str = ""
    default_reasoning_level: str | None = None
    supported_reasoning_levels: list[ReasoningLevelSchema] = []
    supported_in_api: bool = True
    priority: int = 0
    minimal_client_version: str | None = None
    supports_reasoning_summaries: bool = False
    support_verbosity: bool = False
    default_verbosity: str | None = None
    supports_parallel_tool_calls: bool = False
    context_window: int = 0
    input_modalities: list[str] = []
    available_in_plans: list[str] = []
    prefer_websockets: bool = False
    visibility: str = "list"


class CodexModelsResponse(BaseModel):
    models: list[CodexModelEntry]


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


class V1UsageLimitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit_type: str
    limit_window: str
    max_value: int
    current_value: int
    remaining_value: int
    model_filter: str | None = None
    reset_at: str
    source: str = "api_key_limit"


class V1UsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float
    limits: list[V1UsageLimitResponse]
    upstream_limits: list[V1UsageLimitResponse] = []


class WarmupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "default"


class WarmupSubmittedAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    request_id: str
    model: str


class WarmupSkippedAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    reason: str


class WarmupFailedAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    error_code: str
    error_message: str


class WarmupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    total_accounts: int
    submitted: list[WarmupSubmittedAccount]
    skipped: list[WarmupSkippedAccount]
    failed: list[WarmupFailedAccount]
