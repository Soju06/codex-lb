from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.openai.message_coercion import coerce_messages
from app.core.openai.requests import (
    ResponsesCompactRequest,
    ResponsesReasoning,
    ResponsesRequest,
    ResponsesTextControls,
    validate_tool_types,
)
from app.core.types import JsonValue


class V1ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[JsonValue] | None = None
    input: JsonValue | None = None
    instructions: str | None = None
    tools: list[JsonValue] = Field(default_factory=list)
    tool_choice: str | dict[str, JsonValue] | None = None
    parallel_tool_calls: bool | None = None
    reasoning: ResponsesReasoning | None = None
    store: bool | None = None
    stream: bool | None = None
    include: list[str] = Field(default_factory=list)
    conversation: str | None = None
    previous_response_id: str | None = None
    truncation: str | None = None
    prompt_cache_key: str | None = None
    text: ResponsesTextControls | None = None

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue | None) -> JsonValue | None:
        if value is None:
            return value
        if isinstance(value, str) or isinstance(value, list):
            return value
        raise ValueError("input must be a string or array")

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool | None) -> bool | None:
        if value is True:
            raise ValueError("store must be false")
        return value

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, value: list[JsonValue]) -> list[JsonValue]:
        return validate_tool_types(value)

    @model_validator(mode="after")
    def _validate_input(self) -> "V1ResponsesRequest":
        if self.messages is None and self.input is None:
            raise ValueError("Provide either 'input' or 'messages'.")
        if self.messages is not None and self.input not in (None, []):
            raise ValueError("Provide either 'input' or 'messages', not both.")
        if self.conversation and self.previous_response_id:
            raise ValueError("Provide either 'conversation' or 'previous_response_id', not both.")
        return self

    def to_responses_request(self) -> ResponsesRequest:
        data = self.model_dump(mode="json", exclude_none=True)
        messages = data.pop("messages", None)
        instructions = data.get("instructions")
        instruction_text = instructions if isinstance(instructions, str) else ""
        input_value = data.get("input")
        input_items: list[JsonValue] = input_value if isinstance(input_value, list) else []
        input_text: str | None = input_value if isinstance(input_value, str) else None

        if messages is not None:
            instruction_text, input_items = coerce_messages(instruction_text, messages)

        data["instructions"] = instruction_text
        if messages is None and input_text is not None:
            data["input"] = input_text
        else:
            data["input"] = input_items
        return ResponsesRequest.model_validate(data)


class V1ResponsesCompactRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[JsonValue] | None = None
    input: JsonValue | None = None
    instructions: str | None = None

    @model_validator(mode="after")
    def _validate_input(self) -> "V1ResponsesCompactRequest":
        if self.messages is None and self.input is None:
            raise ValueError("Provide either 'input' or 'messages'.")
        if self.messages is not None and self.input not in (None, []):
            raise ValueError("Provide either 'input' or 'messages', not both.")
        return self

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue | None) -> JsonValue | None:
        if value is None:
            return value
        if isinstance(value, str) or isinstance(value, list):
            return value
        raise ValueError("input must be a string or array")

    def to_compact_request(self) -> ResponsesCompactRequest:
        data = self.model_dump(mode="json", exclude_none=True)
        messages = data.pop("messages", None)
        instructions = data.get("instructions")
        instruction_text = instructions if isinstance(instructions, str) else ""
        input_value = data.get("input")
        input_items: list[JsonValue] = input_value if isinstance(input_value, list) else []
        input_text: str | None = input_value if isinstance(input_value, str) else None

        if messages is not None:
            instruction_text, input_items = coerce_messages(instruction_text, messages)

        data["instructions"] = instruction_text
        if messages is None and input_text is not None:
            data["input"] = input_text
        else:
            data["input"] = input_items
        return ResponsesCompactRequest.model_validate(data)
