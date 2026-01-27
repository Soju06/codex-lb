from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.openai.message_coercion import coerce_messages
from app.core.openai.requests import (
    ResponsesCompactRequest,
    ResponsesReasoning,
    ResponsesRequest,
    ResponsesTextControls,
)
from app.core.types import JsonValue


class V1ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[JsonValue] | None = None
    input: list[JsonValue] | None = None
    instructions: str | None = None
    tools: list[JsonValue] = Field(default_factory=list)
    tool_choice: str | None = None
    parallel_tool_calls: bool | None = None
    reasoning: ResponsesReasoning | None = None
    store: bool | None = None
    stream: bool | None = None
    include: list[str] = Field(default_factory=list)
    prompt_cache_key: str | None = None
    text: ResponsesTextControls | None = None

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool | None) -> bool | None:
        if value is True:
            raise ValueError("store must be false")
        return value

    @model_validator(mode="after")
    def _validate_input(self) -> "V1ResponsesRequest":
        if self.messages is None and self.input is None:
            raise ValueError("Provide either 'input' or 'messages'.")
        if self.messages is not None and self.input not in (None, []):
            raise ValueError("Provide either 'input' or 'messages', not both.")
        return self

    def to_responses_request(self) -> ResponsesRequest:
        data = self.model_dump(mode="json", exclude_none=True)
        messages = data.pop("messages", None)
        instructions = data.get("instructions")
        instruction_text = instructions if isinstance(instructions, str) else ""
        input_value = data.get("input")
        input_items: list[JsonValue] = input_value if isinstance(input_value, list) else []

        if messages is not None:
            instruction_text, input_items = coerce_messages(instruction_text, messages)

        data["instructions"] = instruction_text
        data["input"] = input_items
        return ResponsesRequest.model_validate(data)


class V1ResponsesCompactRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[JsonValue] | None = None
    input: list[JsonValue] | None = None
    instructions: str | None = None

    @model_validator(mode="after")
    def _validate_input(self) -> "V1ResponsesCompactRequest":
        if self.messages is None and self.input is None:
            raise ValueError("Provide either 'input' or 'messages'.")
        if self.messages is not None and self.input not in (None, []):
            raise ValueError("Provide either 'input' or 'messages', not both.")
        return self

    def to_compact_request(self) -> ResponsesCompactRequest:
        data = self.model_dump(mode="json", exclude_none=True)
        messages = data.pop("messages", None)
        instructions = data.get("instructions")
        instruction_text = instructions if isinstance(instructions, str) else ""
        input_value = data.get("input")
        input_items: list[JsonValue] = input_value if isinstance(input_value, list) else []

        if messages is not None:
            instruction_text, input_items = coerce_messages(instruction_text, messages)

        data["instructions"] = instruction_text
        data["input"] = input_items
        return ResponsesCompactRequest.model_validate(data)
