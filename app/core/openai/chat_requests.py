from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.openai.message_coercion import coerce_messages
from app.core.openai.requests import ResponsesRequest
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
        tools = _normalize_chat_tools(data.pop("tools", []))
        tool_choice = _normalize_tool_choice(data.pop("tool_choice", None))
        reasoning_effort = data.pop("reasoning_effort", None)
        if reasoning_effort is not None and "reasoning" not in data:
            data["reasoning"] = {"effort": reasoning_effort}
        instructions, input_items = coerce_messages("", messages)
        data["instructions"] = instructions
        data["input"] = input_items
        data["tools"] = tools
        if tool_choice is not None:
            data["tool_choice"] = tool_choice
        return ResponsesRequest.model_validate(data)


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
