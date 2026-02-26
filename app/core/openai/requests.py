from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.types import JsonObject, JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping

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

_INTERLEAVED_REASONING_KEYS = frozenset({"reasoning_content", "reasoning_details", "tool_calls", "function_call"})
_INTERLEAVED_REASONING_PART_TYPES = frozenset({"reasoning", "reasoning_content", "reasoning_details"})
_ASSISTANT_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
_TOOL_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text", "refusal"})


def normalize_tool_type(tool_type: str) -> str:
    return _TOOL_TYPE_ALIASES.get(tool_type, tool_type)


def normalize_tool_choice(choice: JsonValue | None) -> JsonValue | None:
    if not is_json_mapping(choice):
        return choice
    tool_type = choice.get("type")
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
        if not is_json_mapping(tool):
            normalized_tools.append(tool)
            continue
        tool_type = tool.get("type")
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
        if not is_json_mapping(item):
            continue
        if _is_input_file_with_id(item):
            return True
        content = item.get("content")
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
    if not is_json_mapping(item):
        return item

    sanitized_item: dict[str, JsonValue] = {}
    for key, value in item.items():
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
        for part in content:
            sanitized_part = _sanitize_interleaved_reasoning_content_part(part)
            if sanitized_part is None:
                continue
            sanitized_parts.append(sanitized_part)
        return sanitized_parts
    if is_json_mapping(content):
        return _sanitize_interleaved_reasoning_content_part(content)
    return content


def _sanitize_interleaved_reasoning_content_part(part: JsonValue) -> JsonValue | None:
    if not is_json_mapping(part):
        return part

    part_type = part.get("type")
    if isinstance(part_type, str) and part_type in _INTERLEAVED_REASONING_PART_TYPES:
        return None

    sanitized_part = dict(part)
    for key in _INTERLEAVED_REASONING_KEYS:
        sanitized_part.pop(key, None)
    return sanitized_part


def _normalize_role_input_item(value: JsonValue) -> JsonValue:
    if not is_json_mapping(value):
        return value
    role = value.get("role")
    if role == "assistant":
        return _normalize_assistant_input_item(value)
    if role == "tool":
        return _normalize_tool_input_item(value)
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
        for part in content:
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
        return content
    if isinstance(content, str):
        return [{"type": "output_text", "text": content}]
    if is_json_list(content):
        return [_normalize_assistant_content_part(part) for part in content]
    if is_json_mapping(content):
        return [_normalize_assistant_content_part(content)]
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
    if not is_json_mapping(part):
        return None
    part_type = part.get("type")
    text = part.get("text")
    if ((isinstance(part_type, str) and part_type in allowed_types) or part_type is None) and isinstance(text, str):
        return text
    refusal = part.get("refusal")
    if isinstance(part_type, str) and part_type == "refusal" and isinstance(refusal, str):
        return refusal
    return None


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
            return _sanitize_input_items(normalized)
        if is_json_list(value):
            if _has_input_file_id(value):
                raise ValueError("input_file.file_id is not supported")
            return _sanitize_input_items(value)
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
            return _sanitize_input_items(normalized)
        if is_json_list(value):
            if _has_input_file_id(value):
                raise ValueError("input_file.file_id is not supported")
            return _sanitize_input_items(value)
        raise ValueError("input must be a string or array")

    def to_payload(self) -> JsonObject:
        payload = self.model_dump(mode="json", exclude_none=True)
        return _strip_unsupported_fields(payload)


_UNSUPPORTED_UPSTREAM_FIELDS = {"max_output_tokens"}


def _strip_unsupported_fields(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    _normalize_openai_compatible_aliases(payload)
    _sanitize_interleaved_reasoning_input(payload)
    for key in _UNSUPPORTED_UPSTREAM_FIELDS:
        payload.pop(key, None)
    return payload


def _sanitize_interleaved_reasoning_input(payload: dict[str, JsonValue]) -> None:
    input_value = payload.get("input")
    if not is_json_list(input_value):
        return
    payload["input"] = _sanitize_input_items(input_value)


def _normalize_openai_compatible_aliases(payload: dict[str, JsonValue]) -> None:
    reasoning_effort = payload.pop("reasoningEffort", None)
    reasoning_summary = payload.pop("reasoningSummary", None)
    text_verbosity = payload.pop("textVerbosity", None)
    top_level_verbosity = payload.pop("verbosity", None)

    reasoning_payload = payload.get("reasoning")
    if is_json_mapping(reasoning_payload):
        reasoning_map: dict[str, JsonValue] = dict(reasoning_payload)
    else:
        reasoning_map = {}

    if isinstance(reasoning_effort, str) and "effort" not in reasoning_map:
        reasoning_map["effort"] = reasoning_effort
    if isinstance(reasoning_summary, str) and "summary" not in reasoning_map:
        reasoning_map["summary"] = reasoning_summary
    if reasoning_map:
        payload["reasoning"] = reasoning_map

    text_payload = payload.get("text")
    if is_json_mapping(text_payload):
        text_map: dict[str, JsonValue] = dict(text_payload)
    else:
        text_map = {}

    if isinstance(text_verbosity, str) and "verbosity" not in text_map:
        text_map["verbosity"] = text_verbosity
    if isinstance(top_level_verbosity, str) and "verbosity" not in text_map:
        text_map["verbosity"] = top_level_verbosity
    if text_map:
        payload["text"] = text_map


def _normalize_input_text(text: str) -> list[JsonValue]:
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]
