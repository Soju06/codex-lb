from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.openai.models import OpenAIError, OpenAIErrorEnvelope, ResponseUsage
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.sse import format_sse_data, parse_sse_data_json


class ChatToolCallFunction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    arguments: str | None = None


class ChatToolCallDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    id: str | None = None
    type: str = "function"
    function: ChatToolCallFunction | None = None


class ChatChunkDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    content: str | None = None
    refusal: str | None = None
    tool_calls: list[ChatToolCallDelta] | None = None


class ChatChunkChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    delta: ChatChunkDelta
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatChunkChoice]
    usage: "ChatCompletionUsage | None" = None


class ChatMessageToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    type: str = "function"
    function: ChatToolCallFunction | None = None


class ChatCompletionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str | None = None
    refusal: str | None = None
    tool_calls: list[ChatMessageToolCall] | None = None


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    message: ChatCompletionMessage
    finish_reason: str | None = None


class ChatCompletionUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage | None = None


ChatCompletionResult = ChatCompletion | OpenAIErrorEnvelope


@dataclass
class ToolCallIndex:
    indexes: dict[str, int] = field(default_factory=dict)
    next_index: int = 0

    def index_for(self, call_id: str | None, name: str | None) -> int:
        key = _tool_call_key(call_id, name)
        if key is None:
            return 0
        if key not in self.indexes:
            self.indexes[key] = self.next_index
            self.next_index += 1
        return self.indexes[key]


@dataclass
class _ChatChunkState:
    tool_index: ToolCallIndex = field(default_factory=ToolCallIndex)
    saw_tool_call: bool = False
    sent_role: bool = False


@dataclass
class ToolCallDelta:
    index: int
    call_id: str | None
    name: str | None
    arguments: str | None
    tool_type: str | None

    def to_chunk_call(self) -> ChatToolCallDelta:
        function = _build_tool_call_function(self.name, self.arguments)
        return ChatToolCallDelta(
            index=self.index,
            id=self.call_id,
            type=self.tool_type or "function",
            function=function,
        )


@dataclass
class ToolCallState:
    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: str = ""
    tool_type: str = "function"

    def apply_delta(self, delta: ToolCallDelta) -> None:
        if delta.call_id:
            self.call_id = delta.call_id
        if delta.name:
            self.name = delta.name
        if delta.arguments:
            self.arguments += delta.arguments
        if delta.tool_type:
            self.tool_type = delta.tool_type

    def to_message_tool_call(self) -> ChatMessageToolCall | None:
        function = _build_tool_call_function(self.name, self.arguments or None)
        if self.call_id is None and function is None:
            return None
        return ChatMessageToolCall(
            id=self.call_id,
            type=self.tool_type or "function",
            function=function,
        )


def _build_tool_call_function(name: str | None, arguments: str | None) -> ChatToolCallFunction | None:
    if name is None and arguments is None:
        return None
    return ChatToolCallFunction(name=name, arguments=arguments)


def _parse_data(line: str) -> dict[str, JsonValue] | None:
    return parse_sse_data_json(line)


def iter_chat_chunks(
    lines: Iterable[str],
    model: str,
    *,
    created: int | None = None,
    state: _ChatChunkState | None = None,
    include_usage: bool = False,
) -> Iterable[str]:
    created = created or int(time.time())
    state = state or _ChatChunkState()
    for line in lines:
        payload = _parse_data(line)
        if not payload:
            continue
        event_type = payload.get("type")
        if event_type in ("response.output_text.delta", "response.refusal.delta"):
            delta_text = payload.get("delta")
            role = None
            if not state.sent_role:
                role = "assistant"
            if event_type == "response.refusal.delta":
                delta_obj = ChatChunkDelta(
                    role=role,
                    refusal=delta_text if isinstance(delta_text, str) else None,
                )
            else:
                delta_obj = ChatChunkDelta(
                    role=role,
                    content=delta_text if isinstance(delta_text, str) else None,
                )
            chunk = ChatCompletionChunk(
                id="chatcmpl_temp",
                created=created,
                model=model,
                choices=[
                    ChatChunkChoice(
                        index=0,
                        delta=delta_obj,
                        finish_reason=None,
                    )
                ],
            )
            yield _dump_chunk(chunk, include_usage=include_usage)
            if role is not None:
                state.sent_role = True
        tool_delta = _tool_call_delta_from_payload(payload, state.tool_index)
        if tool_delta is not None:
            state.saw_tool_call = True
            role = None
            if not state.sent_role:
                role = "assistant"
            chunk = ChatCompletionChunk(
                id="chatcmpl_temp",
                created=created,
                model=model,
                choices=[
                    ChatChunkChoice(
                        index=0,
                        delta=ChatChunkDelta(
                            role=role,
                            tool_calls=[tool_delta.to_chunk_call()],
                        ),
                        finish_reason=None,
                    )
                ],
            )
            yield _dump_chunk(chunk, include_usage=include_usage)
            if role is not None:
                state.sent_role = True
        if event_type in ("response.failed", "error"):
            error = None
            if event_type == "response.failed":
                response = payload.get("response")
                if isinstance(response, dict):
                    maybe_error = response.get("error")
                    if isinstance(maybe_error, dict):
                        error = maybe_error
            else:
                maybe_error = payload.get("error")
                if isinstance(maybe_error, dict):
                    error = maybe_error
            if error is not None:
                error_payload = {"error": error}
                yield _dump_sse(error_payload)
                yield "data: [DONE]\n\n"
                return
        if event_type in ("response.completed", "response.incomplete"):
            usage = None
            if include_usage:
                response = payload.get("response")
                if isinstance(response, dict):
                    usage = _map_usage(_parse_usage(response.get("usage")))
            finish_reason = "tool_calls" if state.saw_tool_call else "stop"
            if event_type == "response.incomplete" and not state.saw_tool_call:
                finish_reason = _finish_reason_from_incomplete(payload.get("response"))
            done = ChatCompletionChunk(
                id="chatcmpl_temp",
                created=created,
                model=model,
                choices=[
                    ChatChunkChoice(
                        index=0,
                        delta=ChatChunkDelta(),
                        finish_reason=finish_reason,
                    )
                ],
            )
            yield _dump_chunk(done, include_usage=include_usage)
            if include_usage:
                usage_chunk = ChatCompletionChunk(
                    id="chatcmpl_temp",
                    created=created,
                    model=model,
                    choices=[],
                    usage=usage,
                )
                yield _dump_chunk(usage_chunk, include_usage=include_usage)
            yield "data: [DONE]\n\n"
            return


async def stream_chat_chunks(
    stream: AsyncIterator[str],
    model: str,
    *,
    include_usage: bool = False,
) -> AsyncIterator[str]:
    created = int(time.time())
    state = _ChatChunkState()
    async for line in stream:
        for chunk in iter_chat_chunks(
            [line],
            model=model,
            created=created,
            state=state,
            include_usage=include_usage,
        ):
            yield chunk
            if chunk.strip() == "data: [DONE]":
                return


async def collect_chat_completion(stream: AsyncIterator[str], model: str) -> ChatCompletionResult:
    created = int(time.time())
    content_parts: list[str] = []
    refusal_parts: list[str] = []
    response_id: str | None = None
    usage: ResponseUsage | None = None
    incomplete_reason: str | None = None
    tool_index = ToolCallIndex()
    tool_calls: list[ToolCallState] = []

    async for line in stream:
        payload = _parse_data(line)
        if not payload:
            continue
        event_type = payload.get("type")
        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                content_parts.append(delta)
        if event_type == "response.refusal.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                refusal_parts.append(delta)
        tool_delta = _tool_call_delta_from_payload(payload, tool_index)
        if tool_delta is not None:
            _merge_tool_call_delta(tool_calls, tool_delta)
        if event_type in ("response.failed", "error"):
            error = None
            if event_type == "response.failed":
                response = payload.get("response")
                if isinstance(response, dict):
                    maybe_error = response.get("error")
                    if isinstance(maybe_error, dict):
                        error = maybe_error
            else:
                maybe_error = payload.get("error")
                if isinstance(maybe_error, dict):
                    error = maybe_error
            if error is not None:
                return _error_envelope_from_payload(error)
            return _default_error_envelope()
        if event_type in ("response.completed", "response.incomplete"):
            response = payload.get("response")
            if isinstance(response, dict):
                response_id_value = response.get("id")
                if isinstance(response_id_value, str):
                    response_id = response_id_value
                usage = _parse_usage(response.get("usage"))
                if event_type == "response.incomplete":
                    incomplete_reason = _finish_reason_from_incomplete(response)

    message_content = "".join(content_parts) or None
    message_refusal = "".join(refusal_parts) or None
    message_tool_calls = _compact_tool_calls(tool_calls)
    has_tool_calls = bool(message_tool_calls)
    finish_reason = "tool_calls" if has_tool_calls else (incomplete_reason or "stop")
    if has_tool_calls and not message_content:
        message_content = None
    message = ChatCompletionMessage(
        role="assistant",
        content=message_content,
        refusal=message_refusal,
        tool_calls=message_tool_calls or None,
    )
    choice = ChatCompletionChoice(
        index=0,
        message=message,
        finish_reason=finish_reason,
    )
    completion = ChatCompletion(
        id=response_id or "chatcmpl_temp",
        created=created,
        model=model,
        choices=[choice],
        usage=_map_usage(usage),
    )
    return completion


def _map_usage(usage: ResponseUsage | None) -> ChatCompletionUsage | None:
    if usage is None:
        return None
    prompt_tokens = usage.input_tokens
    completion_tokens = usage.output_tokens
    total_tokens = usage.total_tokens
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    return ChatCompletionUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _parse_usage(value: JsonValue) -> ResponseUsage | None:
    if not isinstance(value, dict):
        return None
    try:
        return ResponseUsage.model_validate(value)
    except ValidationError:
        return None


def _dump_chunk(chunk: ChatCompletionChunk, *, include_usage: bool = False) -> str:
    payload = chunk.model_dump(mode="json", exclude_none=True)
    if include_usage and "usage" not in payload:
        payload["usage"] = None
    return _dump_sse(payload)


def _dump_sse(payload: dict[str, JsonValue]) -> str:
    return format_sse_data(payload)


def _finish_reason_from_incomplete(response: JsonValue | None) -> str:
    if not is_json_mapping(response):
        return "stop"
    details = response.get("incomplete_details")
    if is_json_mapping(details):
        reason = details.get("reason")
        if reason in ("max_output_tokens", "max_tokens"):
            return "length"
        if reason == "content_filter":
            return "content_filter"
    return "stop"


def _default_error_envelope() -> OpenAIErrorEnvelope:
    return OpenAIErrorEnvelope(
        error=OpenAIError(
            message="Upstream error",
            type="server_error",
            code="upstream_error",
        )
    )


def _error_envelope_from_payload(payload: Mapping[str, JsonValue]) -> OpenAIErrorEnvelope:
    normalized = _normalize_error_payload(payload)
    if not normalized:
        return _default_error_envelope()
    try:
        error = OpenAIError.model_validate(normalized)
    except ValidationError:
        return _default_error_envelope()
    return OpenAIErrorEnvelope(error=error)


def _normalize_error_payload(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for key in ("message", "type", "code", "param", "plan_type"):
        value = payload.get(key)
        if isinstance(value, str):
            normalized[key] = value
    for key in ("resets_at", "resets_in_seconds"):
        value = payload.get(key)
        number = _coerce_number(value)
        if number is not None:
            normalized[key] = number
    return normalized


def _coerce_number(value: JsonValue) -> int | float | None:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _tool_call_delta_from_payload(payload: Mapping[str, JsonValue], indexer: ToolCallIndex) -> ToolCallDelta | None:
    if not _is_tool_call_event(payload):
        return None
    fields = _extract_tool_call_fields(payload)
    if fields is None:
        return None
    call_id, name, arguments, tool_type = fields
    index = indexer.index_for(call_id, name)
    return ToolCallDelta(
        index=index,
        call_id=call_id,
        name=name,
        arguments=arguments,
        tool_type=tool_type,
    )


def _is_tool_call_event(payload: Mapping[str, JsonValue]) -> bool:
    event_type = payload.get("type")
    if isinstance(event_type, str) and ("tool_call" in event_type or "function_call" in event_type):
        return True
    item = _as_mapping(payload.get("item"))
    if item is not None:
        item_type = item.get("type")
        if isinstance(item_type, str) and ("tool" in item_type or "function" in item_type):
            return True
        if any(key in item for key in ("call_id", "tool_call_id", "arguments", "function", "name")):
            return True
    if any(key in payload for key in ("call_id", "tool_call_id")):
        return True
    if "arguments" in payload and ("name" in payload or "function" in payload):
        return True
    return False


def _extract_tool_call_fields(
    payload: Mapping[str, JsonValue],
) -> tuple[str | None, str | None, str | None, str | None] | None:
    candidate = _select_tool_call_candidate(payload)
    delta = candidate.get("delta")
    delta_map = _as_mapping(delta)
    delta_text = delta if isinstance(delta, str) else None

    call_id = _first_str(
        candidate.get("call_id"),
        candidate.get("tool_call_id"),
        candidate.get("id"),
    )
    if call_id is None and delta_map is not None:
        call_id = _first_str(
            delta_map.get("id"),
            delta_map.get("call_id"),
            delta_map.get("tool_call_id"),
        )

    name = _first_str(candidate.get("name"), candidate.get("tool_name"))
    if name is None and delta_map is not None:
        name = _first_str(delta_map.get("name"))
    if name is None:
        function = _as_mapping(candidate.get("function"))
        if function is not None:
            name = _first_str(function.get("name"))
    if name is None and delta_map is not None:
        function = _as_mapping(delta_map.get("function"))
        if function is not None:
            name = _first_str(function.get("name"))

    arguments = None
    candidate_arguments = candidate.get("arguments")
    if isinstance(candidate_arguments, str):
        arguments = candidate_arguments
    if arguments is None and isinstance(delta_text, str):
        arguments = delta_text
    if arguments is None and delta_map is not None:
        delta_arguments = delta_map.get("arguments")
        if isinstance(delta_arguments, str):
            arguments = delta_arguments
        else:
            function = _as_mapping(delta_map.get("function"))
            if function is not None:
                function_arguments = function.get("arguments")
                if isinstance(function_arguments, str):
                    arguments = function_arguments

    tool_type = _first_str(candidate.get("tool_type"), candidate.get("type"))
    if tool_type and tool_type.startswith("response."):
        tool_type = None
    if tool_type in ("tool_call", "function_call"):
        tool_type = "function"

    if call_id is None and name is None and arguments is None:
        return None
    return call_id, name, arguments, tool_type


def _select_tool_call_candidate(payload: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    item = _as_mapping(payload.get("item"))
    if item is not None:
        item_type = item.get("type")
        if isinstance(item_type, str) and ("tool" in item_type or "function" in item_type):
            return item
        if any(key in item for key in ("call_id", "tool_call_id", "arguments", "function", "name")):
            return item
    return payload


def _tool_call_key(call_id: str | None, name: str | None) -> str | None:
    if call_id:
        return f"id:{call_id}"
    if name:
        return f"name:{name}"
    return None


def _as_mapping(value: JsonValue) -> Mapping[str, JsonValue] | None:
    if is_json_mapping(value):
        return value
    return None


def _first_str(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _merge_tool_call_delta(tool_calls: list[ToolCallState], delta: ToolCallDelta) -> None:
    while len(tool_calls) <= delta.index:
        tool_calls.append(ToolCallState(index=len(tool_calls)))
    tool_calls[delta.index].apply_delta(delta)


def _compact_tool_calls(tool_calls: list[ToolCallState]) -> list[ChatMessageToolCall]:
    cleaned: list[ChatMessageToolCall] = []
    for call in tool_calls:
        tool_call = call.to_message_tool_call()
        if tool_call is not None:
            cleaned.append(tool_call)
    return cleaned
