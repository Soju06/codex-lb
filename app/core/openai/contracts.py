from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from app.core.types import JsonValue

type MessageRole = Literal["system", "developer", "user", "assistant", "tool"]


class TextContentPart(TypedDict, total=False):
    type: str
    text: str


class RefusalContentPart(TypedDict):
    type: Literal["refusal"]
    refusal: str


class ImageURLDescriptor(TypedDict, total=False):
    url: str
    detail: str


class ImageURLContentPart(TypedDict):
    type: Literal["image_url"]
    image_url: str | ImageURLDescriptor


class InputAudioDescriptor(TypedDict, total=False):
    data: str
    format: str


class InputAudioContentPart(TypedDict):
    type: Literal["input_audio"]
    input_audio: InputAudioDescriptor | JsonValue


class FileDescriptor(TypedDict, total=False):
    file_id: str
    file_url: str
    file_data: str
    data: str
    mime_type: str
    content_type: str


class FileContentPart(TypedDict):
    type: Literal["file"]
    file: FileDescriptor | JsonValue


class AssistantToolCallFunction(TypedDict, total=False):
    name: str
    arguments: str


class AssistantToolCall(TypedDict):
    id: str
    function: AssistantToolCallFunction | JsonValue
    type: NotRequired[str]


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
