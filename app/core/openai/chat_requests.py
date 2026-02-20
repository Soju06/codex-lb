from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.openai.message_coercion import coerce_messages
from app.core.openai.requests import (
    ResponsesRequest,
    ResponsesTextControls,
    ResponsesTextFormat,
    normalize_tool_type,
    validate_tool_types,
)
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping

_SUPPORTED_CHAT_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})


class ChatCompletionsRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[dict[str, JsonValue]]
    tools: list[JsonValue] = Field(default_factory=list)
    tool_choice: str | dict[str, JsonValue] | None = None
    parallel_tool_calls: bool | None = None
    stream: bool | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    n: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = None
    seed: int | None = None
    response_format: JsonValue | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    store: bool | None = None
    stream_options: ChatStreamOptions | None = None

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, value: list[JsonValue]) -> list[JsonValue]:
        return validate_tool_types(value)

    @field_validator("messages")
    @classmethod
    def _reject_file_id(cls, value: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
        for message in value:
            if not is_json_mapping(message):
                continue
            content = message.get("content")
            parts = content if is_json_list(content) else [content]
            for part in parts:
                if not is_json_mapping(part):
                    continue
                part_type = part.get("type") or ("text" if "text" in part else None)
                if part_type != "file" and "file" not in part:
                    continue
                file_info = part.get("file")
                if not is_json_mapping(file_info):
                    continue
                file_id = file_info.get("file_id")
                if isinstance(file_id, str) and file_id:
                    raise ValueError("file_id is not supported")
        return value

    @model_validator(mode="after")
    def _validate_messages(self) -> "ChatCompletionsRequest":
        if not self.messages:
            raise ValueError("'messages' must be a non-empty list.")
        for message in self.messages:
            if not is_json_mapping(message):
                raise ValueError("'messages' must contain objects.")
            role = message.get("role")
            role_name = role if isinstance(role, str) else None
            if role_name is None:
                raise ValueError("Each message must include a string 'role'.")
            if role_name not in _SUPPORTED_CHAT_ROLES:
                raise ValueError(f"Unsupported message role: {role_name}")
            content = message.get("content")
            if role_name in ("system", "developer"):
                _ensure_text_only_content(content, role_name)
            elif role_name == "user":
                _validate_user_content(content)
            elif role_name == "assistant":
                _validate_assistant_tool_calls(message)
            elif role_name == "tool":
                _validate_tool_message(message)
        return self

    def to_responses_request(self) -> ResponsesRequest:
        data = self.model_dump(mode="json", exclude_none=True)
        messages = data.pop("messages")
        messages = _sanitize_user_messages(messages)
        data.pop("store", None)
        data.pop("max_tokens", None)
        data.pop("max_completion_tokens", None)
        response_format = data.pop("response_format", None)
        stream_options = data.pop("stream_options", None)
        tools = _normalize_chat_tools(data.pop("tools", []))
        tool_choice = _normalize_tool_choice(data.pop("tool_choice", None))
        reasoning_effort = data.pop("reasoning_effort", None)
        if reasoning_effort is not None and "reasoning" not in data:
            data["reasoning"] = {"effort": reasoning_effort}
        if response_format is not None:
            _apply_response_format(data, response_format)
        if isinstance(stream_options, Mapping):
            include_obfuscation = stream_options.get("include_obfuscation")
            if include_obfuscation is not None:
                data["stream_options"] = {"include_obfuscation": include_obfuscation}
        instructions, input_items = coerce_messages("", messages)
        data["instructions"] = instructions
        data["input"] = input_items
        data["tools"] = tools
        if tool_choice is not None:
            data["tool_choice"] = tool_choice
        return ResponsesRequest.model_validate(data)


class ChatResponseFormatJsonSchema(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str | None = None
    schema_: JsonValue | None = Field(default=None, alias="schema")
    strict: bool | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ValueError("response_format.json_schema.name must match [A-Za-z0-9_-]{1,64}")
        return value


class ChatResponseFormat(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)
    json_schema: ChatResponseFormatJsonSchema | None = None

    @model_validator(mode="after")
    def _validate_schema(self) -> "ChatResponseFormat":
        if self.type == "json_schema" and self.json_schema is None:
            raise ValueError("'response_format.json_schema' is required when type is 'json_schema'.")
        return self


class ChatStreamOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    include_usage: bool | None = None
    include_obfuscation: bool | None = None


def _normalize_chat_tools(tools: list[JsonValue]) -> list[JsonValue]:
    normalized: list[JsonValue] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        function = tool.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            normalized.append(
                {
                    "type": tool_type or "function",
                    "name": name,
                    "description": function.get("description"),
                    "parameters": function.get("parameters"),
                }
            )
            continue
        if isinstance(tool_type, str):
            normalized_type = normalize_tool_type(tool_type)
            if normalized_type == "web_search":
                if normalized_type != tool_type:
                    tool = dict(tool)
                    tool["type"] = normalized_type
                normalized.append(tool)
                continue
        name = tool.get("name")
        if isinstance(name, str) and name:
            normalized.append(tool)
    return normalized


def _normalize_tool_choice(tool_choice: JsonValue | None) -> JsonValue | None:
    if not isinstance(tool_choice, dict):
        return tool_choice
    tool_type = tool_choice.get("type")
    if isinstance(tool_type, str) and tool_type == "web_search_preview":
        tool_choice = dict(tool_choice)
        tool_choice["type"] = "web_search"
        tool_type = "web_search"
    function = tool_choice.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name:
            return {"type": tool_type or "function", "name": name}
    return tool_choice


def _apply_response_format(data: dict[str, JsonValue], response_format: JsonValue) -> None:
    text_controls = _parse_text_controls(data.get("text"))
    if text_controls is None:
        text_controls = ResponsesTextControls()
    if text_controls.format is not None:
        raise ValueError("Provide either 'response_format' or 'text.format', not both.")
    text_controls.format = _response_format_to_text_format(response_format)
    data["text"] = text_controls.model_dump(mode="json", exclude_none=True)


def _parse_text_controls(text: JsonValue | None) -> ResponsesTextControls | None:
    if text is None:
        return None
    if not is_json_mapping(text):
        raise ValueError("'text' must be an object when using 'response_format'.")
    return ResponsesTextControls.model_validate(text)


def _response_format_to_text_format(response_format: JsonValue) -> ResponsesTextFormat:
    if isinstance(response_format, str):
        return _text_format_from_type(response_format)
    if is_json_mapping(response_format):
        parsed = ChatResponseFormat.model_validate(response_format)
        return _text_format_from_parsed(parsed)
    raise ValueError("'response_format' must be a string or object.")


def _text_format_from_type(format_type: str) -> ResponsesTextFormat:
    if format_type in ("json_object", "text"):
        return ResponsesTextFormat(type=format_type)
    if format_type == "json_schema":
        raise ValueError("'response_format' must include 'json_schema' when type is 'json_schema'.")
    raise ValueError(f"Unsupported response_format.type: {format_type}")


def _text_format_from_parsed(parsed: ChatResponseFormat) -> ResponsesTextFormat:
    if parsed.type == "json_schema":
        json_schema = parsed.json_schema
        if json_schema is None:
            raise ValueError("'response_format.json_schema' is required when type is 'json_schema'.")
        return ResponsesTextFormat(
            type=parsed.type,
            schema_=json_schema.schema_,
            name=json_schema.name,
            strict=json_schema.strict,
        )
    if parsed.type in ("json_object", "text"):
        return ResponsesTextFormat(type=parsed.type)
    raise ValueError(f"Unsupported response_format.type: {parsed.type}")


def _ensure_text_only_content(content: JsonValue, role: str) -> None:
    if content is None:
        return
    if isinstance(content, str):
        return
    if is_json_list(content):
        for part in content:
            if isinstance(part, str):
                continue
            if is_json_mapping(part):
                part_type = part.get("type")
                if part_type not in (None, "text"):
                    raise ValueError(f"{role} messages must be text-only.")
                text = part.get("text")
                if isinstance(text, str):
                    continue
            raise ValueError(f"{role} messages must be text-only.")
        return
    if is_json_mapping(content):
        part_type = content.get("type")
        if part_type not in (None, "text"):
            raise ValueError(f"{role} messages must be text-only.")
        text = content.get("text")
        if isinstance(text, str):
            return
    raise ValueError(f"{role} messages must be text-only.")


def _validate_user_content(content: JsonValue) -> None:
    if content is None or isinstance(content, str):
        return
    parts = content if is_json_list(content) else [content]
    for part in parts:
        if isinstance(part, str):
            continue
        if not is_json_mapping(part):
            raise ValueError("User message content parts must be objects.")
        part_type = part.get("type") or ("text" if "text" in part else None)
        if part_type == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise ValueError("Text content parts must include a string 'text'.")
            continue
        if part_type == "image_url":
            image_url = part.get("image_url")
            if not is_json_mapping(image_url):
                raise ValueError("Image content parts must include image_url.url.")
            if not isinstance(image_url.get("url"), str):
                raise ValueError("Image content parts must include image_url.url.")
            continue
        if part_type == "input_audio":
            raise ValueError("Audio input is not supported.")
        if part_type == "file":
            file_info = part.get("file")
            if not is_json_mapping(file_info):
                raise ValueError("File content parts must include file metadata.")
            continue
        raise ValueError(f"Unsupported user content part type: {part_type}")


def _validate_tool_message(message: Mapping[str, JsonValue]) -> None:
    tool_call_id = message.get("tool_call_id")
    tool_call_id_camel = message.get("toolCallId")
    call_id = message.get("call_id")
    resolved_call_id = tool_call_id if isinstance(tool_call_id, str) and tool_call_id else None
    if resolved_call_id is None and isinstance(tool_call_id_camel, str) and tool_call_id_camel:
        resolved_call_id = tool_call_id_camel
    if resolved_call_id is None and isinstance(call_id, str) and call_id:
        resolved_call_id = call_id
    if not isinstance(resolved_call_id, str) or not resolved_call_id:
        raise ValueError("tool messages must include 'tool_call_id'.")


def _validate_assistant_tool_calls(message: Mapping[str, JsonValue]) -> None:
    tool_calls = message.get("tool_calls")
    if tool_calls is None:
        return
    if not is_json_list(tool_calls):
        raise ValueError("assistant message 'tool_calls' must be an array.")
    for index, tool_call in enumerate(tool_calls):
        if not is_json_mapping(tool_call):
            raise ValueError(f"assistant tool_calls[{index}] must be an object.")
        call_id = tool_call.get("id")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError(f"assistant tool_calls[{index}] must include a non-empty 'id'.")
        function = tool_call.get("function")
        if not is_json_mapping(function):
            raise ValueError(f"assistant tool_calls[{index}] must include a 'function' object.")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"assistant tool_calls[{index}].function must include a non-empty 'name'.")


def _sanitize_user_messages(messages: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    sanitized: list[dict[str, JsonValue]] = []
    for message in messages:
        role = message.get("role")
        if role != "user":
            sanitized.append(message)
            continue
        content = message.get("content")
        sanitized_content = _drop_oversized_images(content)
        new_message = dict(message)
        if sanitized_content is not None:
            new_message["content"] = sanitized_content
        sanitized.append(new_message)
    return sanitized


def _drop_oversized_images(content: JsonValue) -> JsonValue | None:
    if content is None or isinstance(content, str):
        return content
    parts = content if is_json_list(content) else [content]
    sanitized_parts: list[JsonValue] = []
    for part in parts:
        if not is_json_mapping(part):
            sanitized_parts.append(part)
            continue
        part_type = part.get("type") or ("text" if "text" in part else None)
        if part_type == "image_url":
            image_url = part.get("image_url")
            if is_json_mapping(image_url):
                url = image_url.get("url")
            else:
                url = None
            if isinstance(url, str) and _is_oversized_data_url(url):
                continue
        sanitized_parts.append(part)
    if is_json_list(content):
        return sanitized_parts
    return sanitized_parts[0] if sanitized_parts else ""


def _is_oversized_data_url(url: str) -> bool:
    if not url.startswith("data:"):
        return False
    try:
        header, data = url.split(",", 1)
    except ValueError:
        return False
    if ";base64" not in header:
        return False
    padding = data.count("=")
    size = (len(data) * 3) // 4 - padding
    return size > 8 * 1024 * 1024
