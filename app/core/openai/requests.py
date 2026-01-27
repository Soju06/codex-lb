from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.types import JsonObject, JsonValue


class ResponsesReasoning(BaseModel):
    model_config = ConfigDict(extra="allow")

    effort: str | None = None
    summary: str | None = None


class ResponsesTextFormat(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)

    type: str | None = None
    strict: bool | None = None
    schema_: JsonValue | None = Field(default=None, alias="schema")
    name: str | None = None


class ResponsesTextControls(BaseModel):
    model_config = ConfigDict(extra="allow")

    verbosity: str | None = None
    format: ResponsesTextFormat | None = None


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    instructions: str
    input: list[JsonValue]
    tools: list[JsonValue] = Field(default_factory=list)
    tool_choice: str | None = None
    parallel_tool_calls: bool | None = None
    reasoning: ResponsesReasoning | None = None
    store: bool = False
    stream: bool | None = None
    include: list[str] = Field(default_factory=list)
    prompt_cache_key: str | None = None
    text: ResponsesTextControls | None = None

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool | None) -> bool:
        if value is True:
            raise ValueError("store must be false")
        return False if value is None else value

    def to_payload(self) -> JsonObject:
        payload = self.model_dump(mode="json", exclude_none=True)
        return _strip_unsupported_fields(payload)


class ResponsesCompactRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    instructions: str
    input: list[JsonValue]

    def to_payload(self) -> JsonObject:
        payload = self.model_dump(mode="json", exclude_none=True)
        return _strip_unsupported_fields(payload)


_UNSUPPORTED_UPSTREAM_FIELDS = {"max_output_tokens"}


def _strip_unsupported_fields(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    for key in _UNSUPPORTED_UPSTREAM_FIELDS:
        payload.pop(key, None)
    return payload
