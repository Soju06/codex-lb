from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from app.core.types import JsonValue


class AnthropicTextBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text"]
    text: str


class AnthropicToolUseBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["tool_use"]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    input: dict[str, JsonValue] = Field(default_factory=dict)


class AnthropicToolResultTextBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text"]
    text: str


class AnthropicToolResultBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["tool_result"]
    tool_use_id: str = Field(min_length=1)
    content: str | list[AnthropicToolResultTextBlock] = ""
    is_error: bool | None = None


AnthropicMessageBlock: TypeAlias = (
    AnthropicTextBlock | AnthropicToolUseBlock | AnthropicToolResultBlock | dict[str, JsonValue]
)


class AnthropicMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["user", "assistant", "system"]
    content: str | list[AnthropicMessageBlock]


class AnthropicSystemTextBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text"]
    text: str


class AnthropicToolDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    description: str | None = None
    input_schema: dict[str, JsonValue] | None = None


class AnthropicToolChoice(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["auto", "any", "tool", "none"]
    name: str | None = None
    disable_parallel_tool_use: bool | None = None


class AnthropicMessagesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[AnthropicMessage]
    system: str | list[AnthropicSystemTextBlock] | None = None
    tools: list[AnthropicToolDefinition] = Field(default_factory=list)
    tool_choice: AnthropicToolChoice | None = None
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None


class AnthropicCountTokensRequest(AnthropicMessagesRequest):
    stream: bool | None = None


class AnthropicResponseTextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str


class AnthropicResponseToolUseBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, JsonValue]


AnthropicResponseContentBlock: TypeAlias = AnthropicResponseTextBlock | AnthropicResponseToolUseBlock


class AnthropicUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int
    output_tokens: int


class AnthropicMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: list[AnthropicResponseContentBlock]
    model: str
    stop_reason: Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"] | None = None
    stop_sequence: str | None = None
    usage: AnthropicUsage


class AnthropicErrorData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    message: str


class AnthropicErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    error: AnthropicErrorData


class AnthropicCountTokensResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int


class AnthropicEventLoggingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
