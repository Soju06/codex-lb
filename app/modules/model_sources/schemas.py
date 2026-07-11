from __future__ import annotations

from datetime import datetime

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


class ModelSourceModelResponse(ModelSourceModelInput):
    id: int
    source_id: str
    created_at: datetime
    updated_at: datetime


class ModelSourceCreateRequest(DashboardModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1)
    supports_chat_completions: bool = True
    supports_responses: bool = False
    supports_audio_transcriptions: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1)
    max_concurrency: int | None = Field(default=None, ge=1)
    models: list[ModelSourceModelInput] = Field(default_factory=list)


class ModelSourceUpdateRequest(DashboardModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1)
    is_enabled: bool | None = None
    supports_chat_completions: bool | None = None
    supports_responses: bool | None = None
    supports_audio_transcriptions: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    max_concurrency: int | None = Field(default=None, ge=1)
    models: list[ModelSourceModelInput] | None = None


class ModelSourceResponse(DashboardModel):
    id: str
    name: str
    kind: str
    base_url: str
    is_enabled: bool
    health_status: str
    supports_chat_completions: bool
    supports_responses: bool
    supports_audio_transcriptions: bool
    timeout_seconds: int | None
    max_concurrency: int | None
    created_at: datetime
    updated_at: datetime
    models: list[ModelSourceModelResponse] = Field(default_factory=list)


class ModelSourcesResponse(DashboardModel):
    sources: list[ModelSourceResponse] = Field(default_factory=list)
