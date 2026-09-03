from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.message_coercion import coerce_messages
from app.core.openai.requests import (
    PassthroughJsonList,
    PassthroughJsonValue,
    ResponsesCompactRequest,
    ResponsesReasoning,
    ResponsesRequest,
    ResponsesTextControls,
    validate_passthrough_depth,
    validate_tool_types,
)
from app.core.types import JsonValue


def _validate_optional_messages_array(value: list[JsonValue] | None) -> list[JsonValue] | None:
    # ``messages`` skips pydantic's ``list`` validation; keep the array check
    # at field level so the error still names ``param="messages"``.
    if value is None:
        return value
    if not isinstance(value, list):
        raise ValueError("messages must be an array")
    validate_passthrough_depth(value)
    return value


class V1ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: PassthroughJsonList | None = None
    input: PassthroughJsonValue = None
    instructions: str | None = None
    tools: PassthroughJsonList = Field(default_factory=list)
    tool_choice: str | dict[str, JsonValue] | None = None
    parallel_tool_calls: bool | None = None
    reasoning: ResponsesReasoning | None = None
    store: bool | None = None
    stream: bool | None = None
    include: list[str] = Field(default_factory=list)
    service_tier: str | None = None
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
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            validate_passthrough_depth(value)
            return value
        raise ValueError("input must be a string or array")

    @field_validator("messages")
    @classmethod
    def _validate_messages_type(cls, value: list[JsonValue] | None) -> list[JsonValue] | None:
        return _validate_optional_messages_array(value)

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool | None) -> bool | None:
        return False

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, value: list[JsonValue]) -> list[JsonValue]:
        validated = validate_tool_types(value, allow_builtin_tools=True)
        validate_passthrough_depth(validated)
        return validated

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
        # The passthrough fields are already plain JSON (validated for shape
        # by the field validators above), so attach them directly instead of
        # deep-copying them through ``model_dump``.
        data = self.model_dump(mode="json", exclude_none=True, exclude={"input", "messages", "tools"})
        if "tools" in self.model_fields_set:
            # ``tools`` defaults to ``[]`` via ``default_factory``; handing
            # ``ResponsesRequest`` an explicit ``"tools": []`` the client
            # never sent would mark the field as set and forward a
            # synthesized empty array upstream. Only attach it when the
            # client sent it (issue #1184); an explicit ``[]`` stays intact.
            data["tools"] = self.tools
        messages = self.messages
        instructions = self.instructions
        instruction_text = instructions if isinstance(instructions, str) else ""
        input_value = self.input
        input_items: list[JsonValue] = input_value if isinstance(input_value, list) else []
        input_text: str | None = input_value if isinstance(input_value, str) else None

        if messages is not None:
            try:
                instruction_text, input_items = coerce_messages(instruction_text, messages)
            except ClientPayloadError:
                raise
            except ValueError as exc:
                raise ClientPayloadError(str(exc), param="messages") from exc

        data["instructions"] = instruction_text
        if messages is None and input_text is not None:
            data["input"] = input_text
        else:
            data["input"] = input_items
        return ResponsesRequest.model_validate(data)


class V1ResponsesCompactRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: PassthroughJsonList | None = None
    input: PassthroughJsonValue = None
    instructions: str | None = None
    reasoning: ResponsesReasoning | None = None

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
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            validate_passthrough_depth(value)
            return value
        raise ValueError("input must be a string or array")

    @field_validator("messages")
    @classmethod
    def _validate_messages_type(cls, value: list[JsonValue] | None) -> list[JsonValue] | None:
        return _validate_optional_messages_array(value)

    def to_compact_request(self) -> ResponsesCompactRequest:
        data = self.model_dump(mode="json", exclude_none=True, exclude={"input", "messages"})
        messages = self.messages
        instructions = self.instructions
        instruction_text = instructions if isinstance(instructions, str) else ""
        input_value = self.input
        input_items: list[JsonValue] = input_value if isinstance(input_value, list) else []
        input_text: str | None = input_value if isinstance(input_value, str) else None

        if messages is not None:
            try:
                instruction_text, input_items = coerce_messages(instruction_text, messages)
            except ClientPayloadError:
                raise
            except ValueError as exc:
                raise ClientPayloadError(str(exc), param="messages") from exc

        data["instructions"] = instruction_text
        if messages is None and input_text is not None:
            data["input"] = input_text
        else:
            data["input"] = input_items
        return ResponsesCompactRequest.model_validate(data)
