from __future__ import annotations

from typing import TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from app.core.types import JsonValue

type ModelLikeInput = JsonValue | BaseModel


def _normalize_model_value[T: BaseModel](model_type: type[T], value: ModelLikeInput | None) -> T | None:
    if value is None:
        return None
    try:
        return model_type.model_validate(value)
    except ValidationError:
        return None


class OpenAIError(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: StrictStr | None = None
    type: StrictStr | None = None
    code: StrictStr | None = None
    param: StrictStr | None = None
    plan_type: StrictStr | None = None
    resets_at: StrictInt | StrictFloat | None = None
    resets_in_seconds: StrictInt | StrictFloat | None = None


class OpenAIErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: OpenAIError | None = None


class ResponseUsageDetails(BaseModel):
    model_config = ConfigDict(extra="allow")

    cached_tokens: StrictInt | None = None
    reasoning_tokens: StrictInt | None = None


class ResponseUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    input_tokens: StrictInt | None = None
    output_tokens: StrictInt | None = None
    total_tokens: StrictInt | None = None
    input_tokens_details: ResponseUsageDetails | None = None
    output_tokens_details: ResponseUsageDetails | None = None


class OpenAIResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: StrictStr | None = None
    status: StrictStr | None = None
    error: OpenAIError | None = None
    usage: ResponseUsage | None = None

    @field_validator("error", mode="before")
    @classmethod
    def _normalize_error(cls, value: ModelLikeInput | None) -> OpenAIError | None:
        return _normalize_model_value(OpenAIError, value)

    @field_validator("usage", mode="before")
    @classmethod
    def _normalize_usage(cls, value: ModelLikeInput | None) -> ResponseUsage | None:
        return _normalize_model_value(ResponseUsage, value)


class OpenAIEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: StrictStr
    response: OpenAIResponse | None = None
    error: OpenAIError | None = None

    @field_validator("error", mode="before")
    @classmethod
    def _normalize_error(cls, value: ModelLikeInput | None) -> OpenAIError | None:
        return _normalize_model_value(OpenAIError, value)


class OpenAIResponsePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: StrictStr | None = None
    status: StrictStr | None = None
    error: OpenAIError | None = None
    usage: ResponseUsage | None = None

    @field_validator("error", mode="before")
    @classmethod
    def _normalize_error(cls, value: ModelLikeInput | None) -> OpenAIError | None:
        return _normalize_model_value(OpenAIError, value)

    @field_validator("usage", mode="before")
    @classmethod
    def _normalize_usage(cls, value: ModelLikeInput | None) -> ResponseUsage | None:
        return _normalize_model_value(ResponseUsage, value)


class CompactResponsePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    object: StrictStr
    id: StrictStr | None = None
    status: StrictStr | None = None
    error: OpenAIError | None = None
    usage: ResponseUsage | None = None

    @field_validator("object")
    @classmethod
    def _validate_object(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Compact response payload requires an object discriminator")
        if not normalized.startswith("response.compact"):
            raise ValueError("Compact response payload requires a compact object discriminator")
        return normalized

    @field_validator("error", mode="before")
    @classmethod
    def _normalize_error(cls, value: ModelLikeInput | None) -> OpenAIError | None:
        return _normalize_model_value(OpenAIError, value)

    @field_validator("usage", mode="before")
    @classmethod
    def _normalize_usage(cls, value: ModelLikeInput | None) -> ResponseUsage | None:
        return _normalize_model_value(ResponseUsage, value)


OpenAIResponseResult: TypeAlias = OpenAIResponsePayload | OpenAIErrorEnvelope
CompactResponseResult: TypeAlias = CompactResponsePayload | OpenAIErrorEnvelope
