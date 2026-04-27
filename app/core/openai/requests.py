from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast, overload

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.types import JsonObject, JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping

type MutableJsonObject = dict[str, JsonValue]

_RESPONSES_INCLUDE_ALLOWLIST = {
    "code_interpreter_call.outputs",
    "computer_call_output.output.image_url",
    "file_search_call.results",
    "message.input_image.image_url",
    "message.output_text.logprobs",
    "reasoning.encrypted_content",
    "web_search_call.action.sources",
}

RESPONSES_UNSUPPORTED_TOOL_TYPES: frozenset[str] = frozenset()

CHAT_UNSUPPORTED_TOOL_TYPES = frozenset(
    {
        "file_search",
        "code_interpreter",
        "computer_use",
        "computer_use_preview",
        "image_generation",
    }
)

_TOOL_TYPE_ALIASES = {
    "web_search_preview": "web_search",
}

_INTERLEAVED_REASONING_KEYS = frozenset({"reasoning_content", "reasoning_details", "tool_calls", "function_call"})
_INTERLEAVED_REASONING_PART_TYPES = frozenset({"reasoning", "reasoning_content", "reasoning_details"})
_ASSISTANT_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
_TOOL_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text", "refusal"})


def _json_mapping_or_none(value: JsonValue) -> Mapping[str, JsonValue] | None:
    if not is_json_mapping(value):
        return None
    return value


def _json_parts(value: JsonValue) -> list[JsonValue]:
    if is_json_list(value):
        return value
    return [value]


def normalize_tool_type(tool_type: str) -> str:
    return _TOOL_TYPE_ALIASES.get(tool_type, tool_type)


def normalize_tool_choice(choice: JsonValue | None) -> JsonValue | None:
    if not is_json_mapping(choice):
        return choice
    choice_mapping = choice
    tool_type = choice_mapping.get("type")
    if isinstance(tool_type, str):
        normalized_type = normalize_tool_type(tool_type)
        if normalized_type != tool_type:
            updated = dict(choice_mapping)
            updated["type"] = normalized_type
            return updated
    return choice


def validate_tool_types(
    tools: list[JsonValue],
    *,
    unsupported_tool_types: frozenset[str] = RESPONSES_UNSUPPORTED_TOOL_TYPES,
) -> list[JsonValue]:
    normalized_tools: list[JsonValue] = []
    for tool in tools:
        if not is_json_mapping(tool):
            normalized_tools.append(tool)
            continue
        tool_mapping = tool
        tool_type = tool_mapping.get("type")
        if isinstance(tool_type, str):
            normalized_type = normalize_tool_type(tool_type)
            if normalized_type != tool_type:
                tool = dict(tool_mapping)
                tool["type"] = normalized_type
                tool_type = normalized_type
            if tool_type in unsupported_tool_types:
                raise ValueError(f"Unsupported tool type: {tool_type}")
        normalized_tools.append(tool)
    return normalized_tools


def _has_input_file_id(input_items: list[JsonValue]) -> bool:
    for item in input_items:
        if not is_json_mapping(item):
            continue
        item_mapping = item
        if _is_input_file_with_id(item_mapping):
            return True
        content = item_mapping.get("content")
        if is_json_list(content):
            parts = content
        elif is_json_mapping(content):
            parts = [content]
        else:
            parts = []
        for part in parts:
            if not is_json_mapping(part):
                continue
            if _is_input_file_with_id(part):
                return True
    return False


def _is_input_file_with_id(item: Mapping[str, JsonValue]) -> bool:
    if item.get("type") != "input_file":
        return False
    file_id = item.get("file_id")
    return isinstance(file_id, str) and bool(file_id)


def _sanitize_input_items(input_items: list[JsonValue]) -> list[JsonValue]:
    sanitized_input: list[JsonValue] = []
    for item in input_items:
        sanitized_item = _sanitize_interleaved_reasoning_input_item(item)
        if sanitized_item is None:
            continue
        sanitized_input.append(_normalize_role_input_item(sanitized_item))
    return sanitized_input


def _sanitize_interleaved_reasoning_input_item(item: JsonValue) -> JsonValue | None:
    item_mapping = _json_mapping_or_none(item)
    if item_mapping is None:
        return item

    sanitized_item: MutableJsonObject = {}
    for key, value in item_mapping.items():
        if key in _INTERLEAVED_REASONING_KEYS:
            continue
        if key == "content":
            sanitized_content = _sanitize_interleaved_reasoning_content(value)
            if sanitized_content is None:
                continue
            sanitized_item[key] = sanitized_content
            continue
        sanitized_item[key] = value
    return sanitized_item


def _sanitize_interleaved_reasoning_content(content: JsonValue) -> JsonValue | None:
    if is_json_list(content):
        sanitized_parts: list[JsonValue] = []
        for part in _json_parts(content):
            sanitized_part = _sanitize_interleaved_reasoning_content_part(part)
            if sanitized_part is None:
                continue
            sanitized_parts.append(sanitized_part)
        return sanitized_parts
    content_mapping = _json_mapping_or_none(content)
    if content_mapping is not None:
        return _sanitize_interleaved_reasoning_content_part(content_mapping)
    return content


def _sanitize_interleaved_reasoning_content_part(part: JsonValue) -> JsonValue | None:
    part_mapping = _json_mapping_or_none(part)
    if part_mapping is None:
        return part

    part_type = part_mapping.get("type")
    if isinstance(part_type, str) and part_type in _INTERLEAVED_REASONING_PART_TYPES:
        return None

    sanitized_part = dict(part_mapping)
    for key in _INTERLEAVED_REASONING_KEYS:
        sanitized_part.pop(key, None)
    return sanitized_part


def _normalize_role_input_item(value: JsonValue) -> JsonValue:
    value_mapping = _json_mapping_or_none(value)
    if value_mapping is None:
        return value
    role = value_mapping.get("role")
    if role == "assistant":
        return _normalize_assistant_input_item(value_mapping)
    if role == "tool":
        return _normalize_tool_input_item(value_mapping)
    return value


def _normalize_tool_input_item(value: Mapping[str, JsonValue]) -> JsonValue:
    tool_call_id = value.get("tool_call_id")
    tool_call_id_camel = value.get("toolCallId")
    call_id = value.get("call_id")
    resolved_call_id = tool_call_id if isinstance(tool_call_id, str) and tool_call_id else None
    if resolved_call_id is None and isinstance(tool_call_id_camel, str) and tool_call_id_camel:
        resolved_call_id = tool_call_id_camel
    if resolved_call_id is None and isinstance(call_id, str) and call_id:
        resolved_call_id = call_id
    if not isinstance(resolved_call_id, str) or not resolved_call_id:
        raise ValueError("tool input items must include 'tool_call_id'")
    output = value.get("output")
    output_value = output if output is not None else value.get("content")
    return {
        "type": "function_call_output",
        "call_id": resolved_call_id,
        "output": _normalize_tool_output_value(output_value),
    }


def _normalize_tool_output_value(content: JsonValue) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if is_json_list(content):
        parts: list[str] = []
        for part in _json_parts(content):
            if isinstance(part, str):
                parts.append(part)
                continue
            extracted = _extract_text_content_part(part, _TOOL_TEXT_PART_TYPES)
            if extracted is not None:
                parts.append(extracted)
        if parts:
            return "".join(parts)
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    if is_json_mapping(content):
        extracted = _extract_text_content_part(content, _TOOL_TEXT_PART_TYPES)
        if extracted is not None:
            return extracted
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return str(content)


def _normalize_assistant_input_item(value: Mapping[str, JsonValue]) -> JsonValue:
    content = value.get("content")
    normalized_content = _normalize_assistant_content(content)
    if normalized_content == content:
        return value
    updated = dict(value)
    updated["content"] = normalized_content
    return updated


def _normalize_assistant_content(content: JsonValue) -> JsonValue:
    if content is None:
        return None
    if isinstance(content, str):
        return cast(JsonValue, [{"type": "output_text", "text": content}])
    if is_json_list(content):
        return cast(JsonValue, [_normalize_assistant_content_part(part) for part in _json_parts(content)])
    content_mapping = _json_mapping_or_none(content)
    if content_mapping is not None:
        return [_normalize_assistant_content_part(content_mapping)]
    return content


def _normalize_assistant_content_part(part: JsonValue) -> JsonValue:
    if isinstance(part, str):
        return {"type": "output_text", "text": part}
    if not is_json_mapping(part):
        return part
    text = _extract_text_content_part(part, _ASSISTANT_TEXT_PART_TYPES)
    if text is not None:
        return {"type": "output_text", "text": text}
    return part


def _extract_text_content_part(part: JsonValue, allowed_types: frozenset[str]) -> str | None:
    part_mapping = _json_mapping_or_none(part)
    if part_mapping is None:
        return None
    part_type = part_mapping.get("type")
    text = part_mapping.get("text")
    if ((isinstance(part_type, str) and part_type in allowed_types) or part_type is None) and isinstance(text, str):
        return text
    refusal = part_mapping.get("refusal")
    if isinstance(part_type, str) and part_type == "refusal" and isinstance(refusal, str):
        return refusal
    return None


def _json_list_or_none(value: JsonValue) -> list[JsonValue] | None:
    if not is_json_list(value):
        return None
    return value


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
    tool_choice: str | JsonObject | None = None
    parallel_tool_calls: bool | None = None
    reasoning: ResponsesReasoning | None = None
    store: bool = False
    stream: bool | None = None
    include: list[str] = Field(default_factory=list)
    service_tier: str | None = None
    service_tier_input: str | None = Field(default=None, exclude=True, repr=False)
    conversation: str | None = None
    previous_response_id: str | None = None
    truncation: str | None = None
    prompt_cache_key: str | None = None
    text: ResponsesTextControls | None = None

    @model_validator(mode="before")
    @classmethod
    def _capture_service_tier_input_before_validation(cls, data: JsonValue) -> JsonValue:
        return _capture_service_tier_input(data)

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            normalized = _normalize_input_text(value)
            if _has_input_file_id(normalized):
                raise ValueError("input_file.file_id is not supported")
            return _sanitize_input_items(normalized)
        if is_json_list(value):
            input_items = value
            if _has_input_file_id(input_items):
                raise ValueError("input_file.file_id is not supported")
            return _sanitize_input_items(input_items)
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
        return False

    @field_validator("previous_response_id")
    @classmethod
    def _normalize_previous_response_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, value: list[JsonValue]) -> list[JsonValue]:
        return validate_tool_types(value)

    @field_validator("tool_choice")
    @classmethod
    def _normalize_tool_choice_field(cls, value: JsonValue | None) -> JsonValue | None:
        return normalize_tool_choice(value)

    @field_validator("service_tier")
    @classmethod
    def _normalize_service_tier_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalize_service_tier_alias_value(value)
        return normalized if isinstance(normalized, str) else value

    @model_validator(mode="after")
    def _validate_conversation(self) -> "ResponsesRequest":
        if self.conversation and self.previous_response_id:
            raise ValueError("Provide either 'conversation' or 'previous_response_id', not both.")
        return self

    def platform_forwarded_service_tier(self) -> str | None:
        if _is_fast_service_tier_input(self.service_tier_input):
            return "default"
        return self.service_tier

    def to_payload(self) -> JsonObject:
        payload: MutableJsonObject = self.model_dump(mode="json", exclude_none=True)
        return _strip_unsupported_fields(payload)


class ResponsesCompactRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    instructions: str
    input: JsonValue
    reasoning: ResponsesReasoning | None = None
    store: bool = False
    service_tier: str | None = None
    service_tier_input: str | None = Field(default=None, exclude=True, repr=False)
    prompt_cache_key: str | None = None

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            normalized = _normalize_input_text(value)
            if _has_input_file_id(normalized):
                raise ValueError("input_file.file_id is not supported")
            return _sanitize_input_items(normalized)
        if is_json_list(value):
            input_items = value
            if _has_input_file_id(input_items):
                raise ValueError("input_file.file_id is not supported")
            return _sanitize_input_items(input_items)
        raise ValueError("input must be a string or array")

    @model_validator(mode="before")
    @classmethod
    def _normalize_service_tier_aliases_before_validation(cls, data: JsonValue) -> JsonValue:
        if not is_json_mapping(data):
            return data
        normalized = _capture_service_tier_input(data)
        service_tier = normalized.get("service_tier")
        normalized_service_tier = _normalize_service_tier_alias_value(service_tier)
        if isinstance(normalized_service_tier, str):
            normalized["service_tier"] = normalized_service_tier
        return normalized

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool) -> bool:
        return False

    def platform_forwarded_service_tier(self) -> str | None:
        if _is_fast_service_tier_input(self.service_tier_input):
            return "default"
        return self.service_tier

    def to_payload(self) -> JsonObject:
        payload: MutableJsonObject = self.model_dump(mode="json", exclude_none=True)
        return _strip_compact_unsupported_fields(payload)


_UNSUPPORTED_UPSTREAM_FIELDS = {
    "max_output_tokens",
    "prompt_cache_retention",
    "safety_identifier",
    "temperature",
}


def _strip_unsupported_fields(payload: MutableJsonObject) -> MutableJsonObject:
    _normalize_openai_compatible_aliases(payload)
    _normalize_service_tier_aliases(payload)
    _sanitize_interleaved_reasoning_input(payload)
    _canonicalize_tools(payload)
    for key in _UNSUPPORTED_UPSTREAM_FIELDS:
        payload.pop(key, None)
    return payload


def _canonicalize_tools(payload: MutableJsonObject) -> None:
    tools = payload.get("tools")
    if not is_json_list(tools):
        return
    tool_list = tools
    if not tool_list:
        return
    sorted_tools = sorted(tool_list, key=_tool_sort_key)
    payload["tools"] = [_sort_keys_recursive(t) for t in sorted_tools]


def _tool_sort_key(tool: JsonValue) -> str:
    if not is_json_mapping(tool):
        return ""
    tool_map = tool
    name = tool_map.get("name")
    if isinstance(name, str):
        return name
    func = tool_map.get("function")
    if is_json_mapping(func):
        func_name = func.get("name")
        if isinstance(func_name, str):
            return func_name
    return ""


def _sort_keys_recursive(value: JsonValue) -> JsonValue:
    if is_json_mapping(value):
        mapping = value
        return {k: _sort_keys_recursive(v) for k, v in sorted(mapping.items())}
    if is_json_list(value):
        return [_sort_keys_recursive(item) for item in value]
    return value


def _strip_compact_unsupported_fields(payload: MutableJsonObject) -> MutableJsonObject:
    payload = _strip_unsupported_fields(payload)
    payload.pop("store", None)
    payload.pop("tools", None)
    payload.pop("tool_choice", None)
    payload.pop("parallel_tool_calls", None)
    return payload


def _sanitize_interleaved_reasoning_input(payload: MutableJsonObject) -> None:
    input_value = payload.get("input")
    input_items = _json_list_or_none(input_value)
    if input_items is None:
        return
    payload["input"] = _sanitize_input_items(input_items)


def _normalize_openai_compatible_aliases(payload: MutableJsonObject) -> None:
    reasoning_effort = payload.pop("reasoningEffort", None)
    reasoning_summary = payload.pop("reasoningSummary", None)
    text_verbosity = payload.pop("textVerbosity", None)
    top_level_verbosity = payload.pop("verbosity", None)
    prompt_cache_key = payload.pop("promptCacheKey", None)
    prompt_cache_retention = payload.pop("promptCacheRetention", None)

    if isinstance(prompt_cache_key, str) and "prompt_cache_key" not in payload:
        payload["prompt_cache_key"] = prompt_cache_key
    if isinstance(prompt_cache_retention, str) and "prompt_cache_retention" not in payload:
        payload["prompt_cache_retention"] = prompt_cache_retention

    reasoning_payload = _json_mapping_or_none(payload.get("reasoning"))
    if reasoning_payload is not None:
        reasoning_map: MutableJsonObject = dict(reasoning_payload.items())
    else:
        reasoning_map = {}

    if isinstance(reasoning_effort, str) and "effort" not in reasoning_map:
        reasoning_map["effort"] = reasoning_effort
    if isinstance(reasoning_summary, str) and "summary" not in reasoning_map:
        reasoning_map["summary"] = reasoning_summary
    if reasoning_map:
        payload["reasoning"] = reasoning_map

    text_payload = _json_mapping_or_none(payload.get("text"))
    if text_payload is not None:
        text_map: MutableJsonObject = dict(text_payload.items())
    else:
        text_map = {}

    if isinstance(text_verbosity, str) and "verbosity" not in text_map:
        text_map["verbosity"] = text_verbosity
    if isinstance(top_level_verbosity, str) and "verbosity" not in text_map:
        text_map["verbosity"] = top_level_verbosity
    if text_map:
        payload["text"] = text_map


def _normalize_service_tier_aliases(payload: MutableJsonObject) -> None:
    service_tier = payload.get("service_tier")
    normalized = _normalize_service_tier_alias_value(service_tier)
    if isinstance(normalized, str):
        payload["service_tier"] = normalized


@overload
def _capture_service_tier_input(data: Mapping[str, JsonValue]) -> MutableJsonObject: ...


@overload
def _capture_service_tier_input(data: JsonValue) -> JsonValue | MutableJsonObject: ...


def _capture_service_tier_input(data: JsonValue) -> JsonValue | MutableJsonObject:
    if not is_json_mapping(data):
        return data
    normalized: MutableJsonObject = dict(data.items())
    service_tier = normalized.get("service_tier")
    if isinstance(service_tier, str):
        stripped = service_tier.strip()
        normalized["service_tier_input"] = stripped or None
    return normalized


def _is_fast_service_tier_input(value: str | None) -> bool:
    return isinstance(value, str) and value.lower() == "fast"


def _normalize_service_tier_alias_value(value: JsonValue) -> JsonValue:
    if not isinstance(value, str):
        return value
    if value.strip().lower() == "fast":
        return "priority"
    return value


def _normalize_input_text(text: str) -> list[JsonValue]:
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]
