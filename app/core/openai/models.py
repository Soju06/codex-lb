from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)


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
    model_config = ConfigDict(extra="ignore")

    cached_tokens: StrictInt | None = None
    reasoning_tokens: StrictInt | None = None


class ResponseUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    def _normalize_error(cls, value: object) -> OpenAIError | None:
        if value is None:
            return None
        try:
            return OpenAIError.model_validate(value)
        except ValidationError:
            return None

    @field_validator("usage", mode="before")
    @classmethod
    def _normalize_usage(cls, value: object) -> ResponseUsage | None:
        if value is None:
            return None
        try:
            return ResponseUsage.model_validate(value)
        except ValidationError:
            return None


class OpenAIEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: StrictStr
    response: OpenAIResponse | None = None
    error: OpenAIError | None = None

    @field_validator("error", mode="before")
    @classmethod
    def _normalize_error(cls, value: object) -> OpenAIError | None:
        if value is None:
            return None
        try:
            return OpenAIError.model_validate(value)
        except ValidationError:
            return None


class OpenAIResponsePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: StrictStr | None = None
    status: StrictStr | None = None
    error: OpenAIError | None = None
    usage: ResponseUsage | None = None

    @field_validator("error", mode="before")
    @classmethod
    def _normalize_error(cls, value: object) -> OpenAIError | None:
        if value is None:
            return None
        try:
            return OpenAIError.model_validate(value)
        except ValidationError:
            return None

    @field_validator("usage", mode="before")
    @classmethod
    def _normalize_usage(cls, value: object) -> ResponseUsage | None:
        if value is None:
            return None
        try:
            return ResponseUsage.model_validate(value)
        except ValidationError:
            return None
