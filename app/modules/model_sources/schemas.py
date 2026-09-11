from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class ModelSourceModelInput(DashboardModel):
    model: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    input_per_1m: float | None = Field(default=None, ge=0)
    cached_input_per_1m: float | None = Field(default=None, ge=0)
    output_per_1m: float | None = Field(default=None, ge=0)
    audio_per_minute: float | None = Field(default=None, ge=0)
    raw_metadata_json: str | None = None
    is_enabled: bool = True


CompanyModelHealthState = Literal["unknown", "healthy", "unhealthy", "slow", "stale"]


class CompanyModelHealthCheck(DashboardModel):
    state: CompanyModelHealthState
    checked_at: datetime | None = None
    expires_at: datetime | None = None
    next_due_at: datetime | None = None
    latency_ms: int | None = None
    first_token_ms: int | None = None
    error_code: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    catalog_visible: bool = False


class CompanyHealthCheckSummary(DashboardModel):
    enabled_models: int
    fresh_checks: int
    visible_models: int
    interval_seconds: int
    timeout_seconds: float
    freshness_seconds: int


class ModelSourceModelResponse(ModelSourceModelInput):
    health_check: CompanyModelHealthCheck | None = None
    id: int
    source_id: str
    created_at: datetime
    updated_at: datetime


class ModelSourceCreateRequest(DashboardModel):
    local_token_budget: int | None = Field(default=None, ge=1, le=9_000_000_000_000_000)
    kind: Literal["openai_compatible", "llmbox", "trae", "codebase_llm"] = "openai_compatible"
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1)
    supports_chat_completions: bool = True
    supports_responses: bool = False
    supports_audio_transcriptions: bool = False
    supports_embeddings: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1)
    max_concurrency: int | None = Field(default=None, ge=1)
    models: list[ModelSourceModelInput] = Field(default_factory=list)


class ModelSourceUpdateRequest(DashboardModel):
    local_token_budget: int | None = Field(default=None, ge=1, le=9_000_000_000_000_000)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1)
    is_enabled: bool | None = None
    supports_chat_completions: bool | None = None
    supports_responses: bool | None = None
    supports_audio_transcriptions: bool | None = None
    supports_embeddings: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    max_concurrency: int | None = Field(default=None, ge=1)
    models: list[ModelSourceModelInput] | None = None


class ObservedSourceUsage(DashboardModel):
    since: datetime
    requests: int
    requests_without_usage: int
    input_tokens: int | None
    output_tokens: int | None


class CompanySourceStatus(DashboardModel):
    health_check_summary: CompanyHealthCheckSummary | None = None
    in_flight: int = 0
    health: Literal["unknown", "healthy", "degraded", "unavailable"] = "unknown"
    cooldown_until: datetime | None = None
    successes: int = 0
    failures: int = 0
    rate_limits: int = 0
    server_errors: int = 0
    average_latency_ms: float | None = None
    budget_used: int = 0
    budget_exhausted: bool = False
    credential_cache: str
    quota_status: Literal["unknown"] = "unknown"
    remaining: None = None
    resets_at: None = None
    observed_usage: ObservedSourceUsage | None = None


class ModelSourceResponse(DashboardModel):
    local_token_budget: int | None = None
    company_status: CompanySourceStatus | None = None
    id: str
    name: str
    kind: str
    base_url: str
    is_enabled: bool
    health_status: str
    supports_chat_completions: bool
    supports_responses: bool
    supports_audio_transcriptions: bool
    supports_embeddings: bool
    timeout_seconds: int | None
    max_concurrency: int | None
    created_at: datetime
    updated_at: datetime
    models: list[ModelSourceModelResponse] = Field(default_factory=list)


class ModelSourcesResponse(DashboardModel):
    sources: list[ModelSourceResponse] = Field(default_factory=list)
