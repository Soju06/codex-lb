from __future__ import annotations

from typing import Literal, TypedDict

from app.core.types import JsonValue

type MessageRole = Literal["system", "developer", "user", "assistant", "tool"]


class TextContentPart(TypedDict, total=False):
    type: str
    text: str


class RefusalContentPart(TypedDict):
    type: Literal["refusal"]
    refusal: str


class OpenAIMessage(TypedDict, total=False):
    role: MessageRole | str
    content: JsonValue
    tool_calls: list[JsonValue]
    refusal: str
    tool_call_id: str
    toolCallId: str
    call_id: str


class FunctionCallInputItem(TypedDict):
    type: Literal["function_call"]
    call_id: str
    name: str
    arguments: str


class FunctionCallOutputInputItem(TypedDict):
    type: Literal["function_call_output"]
    call_id: str
    output: str


class InputFileItem(TypedDict, total=False):
    type: Literal["input_file"]
    file_id: str
    file_url: str
