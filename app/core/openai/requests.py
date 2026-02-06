from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.types import JsonObject, JsonValue

_RESPONSES_INCLUDE_ALLOWLIST = {
    "code_interpreter_call.outputs",
    "computer_call_output.output.image_url",
    "file_search_call.results",
    "message.input_image.image_url",
    "message.output_text.logprobs",
    "reasoning.encrypted_content",
    "web_search_call.action.sources",
}

UNSUPPORTED_TOOL_TYPES = {
    "file_search",
    "code_interpreter",
    "computer_use",
    "computer_use_preview",
    "image_generation",
}

_TOOL_TYPE_ALIASES = {
    "web_search_preview": "web_search",
}


def normalize_tool_type(tool_type: str) -> str:
    return _TOOL_TYPE_ALIASES.get(tool_type, tool_type)


def normalize_tool_choice(choice: JsonValue | None) -> JsonValue | None:
    if not isinstance(choice, Mapping):
        return choice
    choice_map = cast(Mapping[str, JsonValue], choice)
    tool_type = choice_map.get("type")
    if isinstance(tool_type, str):
        normalized_type = normalize_tool_type(tool_type)
        if normalized_type != tool_type:
            updated = dict(choice)
            updated["type"] = normalized_type
            return updated
    return choice


def validate_tool_types(tools: list[JsonValue]) -> list[JsonValue]:
    normalized_tools: list[JsonValue] = []
    for tool in tools:
        if not isinstance(tool, Mapping):
            normalized_tools.append(tool)
            continue
        tool_map = cast(Mapping[str, JsonValue], tool)
        tool_type = tool_map.get("type")
        if isinstance(tool_type, str):
            normalized_type = normalize_tool_type(tool_type)
            if normalized_type != tool_type:
                tool = dict(tool)
                tool["type"] = normalized_type
                tool_type = normalized_type
            if tool_type in UNSUPPORTED_TOOL_TYPES:
                raise ValueError(f"Unsupported tool type: {tool_type}")
        normalized_tools.append(tool)
    return normalized_tools


def _has_input_file_id(input_items: list[JsonValue]) -> bool:
    for item in input_items:
        if not isinstance(item, Mapping):
            continue
        item_map = cast(Mapping[str, JsonValue], item)
        if _is_input_file_with_id(item_map):
            return True
        content = item_map.get("content")
        if isinstance(content, list):
            parts = content
        elif isinstance(content, Mapping):
            parts = [content]
        else:
            parts = []
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            part_map = cast(Mapping[str, JsonValue], part)
            if _is_input_file_with_id(part_map):
                return True
    return False


def _is_input_file_with_id(item: Mapping[str, JsonValue]) -> bool:
    if item.get("type") != "input_file":
        return False
    file_id = item.get("file_id")
    return isinstance(file_id, str) and bool(file_id)


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
    input: JsonValue
    tools: list[JsonValue] = Field(default_factory=list)
    tool_choice: str | dict[str, JsonValue] | None = None
    parallel_tool_calls: bool | None = None
    reasoning: ResponsesReasoning | None = None
    store: bool = False
    stream: bool | None = None
    include: list[str] = Field(default_factory=list)
    conversation: str | None = None
    previous_response_id: str | None = None
    truncation: str | None = None
    prompt_cache_key: str | None = None
    text: ResponsesTextControls | None = None

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            normalized = _normalize_input_text(value)
            if _has_input_file_id(normalized):
                raise ValueError("input_file.file_id is not supported")
            return normalized
        if isinstance(value, list):
            if _has_input_file_id(cast(list[JsonValue], value)):
                raise ValueError("input_file.file_id is not supported")
            return value
        raise ValueError("input must be a string or array")

    @field_validator("include")
    @classmethod
    def _validate_include(cls, value: list[str]) -> list[str]:
        for entry in value:
            if entry not in _RESPONSES_INCLUDE_ALLOWLIST:
                raise ValueError(f"Unsupported include value: {entry}")
        return value

    @field_validator("truncation")
    @classmethod
    def _validate_truncation(cls, value: str | None) -> str | None:
        if value is None:
            return value
        raise ValueError("truncation is not supported")

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool | None) -> bool:
        if value is True:
            raise ValueError("store must be false")
        return False if value is None else value

    @field_validator("previous_response_id")
    @classmethod
    def _reject_previous_response_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        raise ValueError("previous_response_id is not supported")

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, value: list[JsonValue]) -> list[JsonValue]:
        return validate_tool_types(value)

    @field_validator("tool_choice")
    @classmethod
    def _normalize_tool_choice_field(cls, value: JsonValue | None) -> JsonValue | None:
        return normalize_tool_choice(value)

    @field_validator("tool_choice")
    @classmethod
    def _normalize_tool_choice(cls, value: JsonValue | None) -> JsonValue | None:
        return normalize_tool_choice(value)

    @model_validator(mode="after")
    def _validate_conversation(self) -> "ResponsesRequest":
        if self.conversation and self.previous_response_id:
            raise ValueError("Provide either 'conversation' or 'previous_response_id', not both.")
        return self

    def to_payload(self) -> JsonObject:
        payload = self.model_dump(mode="json", exclude_none=True)
        return _strip_unsupported_fields(payload)


class ResponsesCompactRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    instructions: str
    input: JsonValue

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            normalized = _normalize_input_text(value)
            if _has_input_file_id(normalized):
                raise ValueError("input_file.file_id is not supported")
            return normalized
        if isinstance(value, list):
            if _has_input_file_id(cast(list[JsonValue], value)):
                raise ValueError("input_file.file_id is not supported")
            return value
        raise ValueError("input must be a string or array")

    def to_payload(self) -> JsonObject:
        payload = self.model_dump(mode="json", exclude_none=True)
        return _strip_unsupported_fields(payload)


_UNSUPPORTED_UPSTREAM_FIELDS = {"max_output_tokens"}


def _strip_unsupported_fields(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    for key in _UNSUPPORTED_UPSTREAM_FIELDS:
        payload.pop(key, None)
    return payload


def _normalize_input_text(text: str) -> list[JsonValue]:
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]
