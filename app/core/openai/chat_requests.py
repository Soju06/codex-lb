from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.openai.message_coercion import coerce_messages
from app.core.openai.requests import ResponsesRequest, ResponsesTextControls, ResponsesTextFormat
from app.core.types import JsonValue


class ChatCompletionsRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[JsonValue]
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

    @model_validator(mode="after")
    def _validate_messages(self) -> "ChatCompletionsRequest":
        if not self.messages:
            raise ValueError("'messages' must be a non-empty list.")
        return self

    def to_responses_request(self) -> ResponsesRequest:
        data = self.model_dump(mode="json", exclude_none=True)
        messages = data.pop("messages")
        data.pop("store", None)
        data.pop("max_tokens", None)
        data.pop("max_completion_tokens", None)
        response_format = data.pop("response_format", None)
        tools = _normalize_chat_tools(data.pop("tools", []))
        tool_choice = _normalize_tool_choice(data.pop("tool_choice", None))
        reasoning_effort = data.pop("reasoning_effort", None)
        if reasoning_effort is not None and "reasoning" not in data:
            data["reasoning"] = {"effort": reasoning_effort}
        if response_format is not None:
            _apply_response_format(data, response_format)
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


class ChatResponseFormat(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)
    json_schema: ChatResponseFormatJsonSchema | None = None

    @model_validator(mode="after")
    def _validate_schema(self) -> "ChatResponseFormat":
        if self.type == "json_schema" and self.json_schema is None:
            raise ValueError("'response_format.json_schema' is required when type is 'json_schema'.")
        return self


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
        name = tool.get("name")
        if isinstance(name, str) and name:
            normalized.append(tool)
    return normalized


def _normalize_tool_choice(tool_choice: JsonValue | None) -> JsonValue | None:
    if not isinstance(tool_choice, dict):
        return tool_choice
    tool_type = tool_choice.get("type")
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
    data["text"] = cast(JsonValue, text_controls.model_dump(mode="json", exclude_none=True))


def _parse_text_controls(text: JsonValue | None) -> ResponsesTextControls | None:
    if text is None:
        return None
    if not isinstance(text, Mapping):
        raise ValueError("'text' must be an object when using 'response_format'.")
    return ResponsesTextControls.model_validate(text)


def _response_format_to_text_format(response_format: JsonValue) -> ResponsesTextFormat:
    if isinstance(response_format, str):
        return _text_format_from_type(response_format)
    if isinstance(response_format, Mapping):
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
