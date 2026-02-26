from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.core.errors import OpenAIErrorEnvelope as UpstreamOpenAIErrorEnvelope
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.message_coercion import coerce_messages
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping
from app.core.utils.sse import parse_sse_data_json
from app.modules.anthropic_compat.schemas import (
    AnthropicErrorData,
    AnthropicErrorEnvelope,
    AnthropicMessage,
    AnthropicMessageBlock,
    AnthropicMessageResponse,
    AnthropicMessagesRequest,
    AnthropicResponseTextBlock,
    AnthropicResponseToolUseBlock,
    AnthropicTextBlock,
    AnthropicToolChoice,
    AnthropicToolDefinition,
    AnthropicToolResultBlock,
    AnthropicToolResultTextBlock,
    AnthropicToolUseBlock,
    AnthropicUsage,
)


class AnthropicTranslationError(ValueError):
    def __init__(self, message: str, *, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


type AnthropicStopReason = Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"]


@dataclass(slots=True)
class _ToolCallDelta:
    index: int
    call_id: str | None
    name: str | None
    arguments: str | None


@dataclass(slots=True)
class _ToolCallState:
    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: str = ""

    def apply_delta(self, delta: _ToolCallDelta) -> None:
        if delta.call_id:
            self.call_id = delta.call_id
        if delta.name:
            self.name = delta.name
        if delta.arguments:
            self.arguments += delta.arguments


@dataclass(slots=True)
class _ToolCallIndex:
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


@dataclass(slots=True)
class _CollectedResponseState:
    model: str
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: AnthropicStopReason | None = None
    stop_sequence: str | None = None
    text_parts: list[str] = field(default_factory=list)
    tool_calls: list[_ToolCallState] = field(default_factory=list)
    completed_response: Mapping[str, JsonValue] | None = None
    error: AnthropicErrorEnvelope | None = None


@dataclass(slots=True)
class _StreamState:
    model: str
    message_id: str
    started: bool = False
    text_block_index: int | None = None
    next_block_index: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: AnthropicStopReason | None = None
    stop_sequence: str | None = None
    tool_calls: list[_ToolCallState] = field(default_factory=list)


PromptCacheKeySource = Literal["explicit", "metadata", "cache_control", "anchor", "none", "claude_shared"]

_PASSTHROUGH_CACHE_EXTRA_KEYS = frozenset(
    {
        "prompt_cache_key",
        "promptCacheKey",
        "prompt_cache_retention",
        "promptCacheRetention",
    }
)

_CAMEL_TO_SNAKE_CACHE_KEY: dict[str, str] = {
    "promptCacheKey": "prompt_cache_key",
    "promptCacheRetention": "prompt_cache_retention",
}


@dataclass(frozen=True, slots=True)
class PromptCacheKeyResolution:
    key: str | None
    source: PromptCacheKeySource


def to_responses_request(payload: AnthropicMessagesRequest) -> ResponsesRequest:
    translated, _ = to_responses_request_with_cache_resolution(payload)
    return translated


def to_responses_request_with_cache_resolution(
    payload: AnthropicMessagesRequest,
) -> tuple[ResponsesRequest, PromptCacheKeyResolution]:
    instructions = _extract_system_text(payload)
    messages = _to_openai_messages(payload.messages)

    try:
        merged_instructions, input_items = coerce_messages(instructions, messages)
    except ClientPayloadError as exc:
        raise AnthropicTranslationError(str(exc), param=exc.param) from exc

    translated_payload: dict[str, JsonValue] = {
        "model": payload.model,
        "instructions": merged_instructions,
        "input": input_items,
        "tools": _translate_tools(payload.tools),
        "stream": bool(payload.stream),
    }
    tool_choice = _translate_tool_choice(payload.tool_choice)
    if tool_choice is not None:
        translated_payload["tool_choice"] = tool_choice

    if payload.tool_choice and payload.tool_choice.disable_parallel_tool_use is not None:
        translated_payload["parallel_tool_calls"] = not payload.tool_choice.disable_parallel_tool_use

    if payload.temperature is not None:
        translated_payload["temperature"] = payload.temperature
    if payload.top_p is not None:
        translated_payload["top_p"] = payload.top_p
    if payload.top_k is not None:
        translated_payload["top_k"] = payload.top_k
    if payload.stop_sequences is not None:
        translated_payload["stop"] = _json_array_from_strings(payload.stop_sequences)
    if payload.max_tokens is not None:
        translated_payload["max_output_tokens"] = payload.max_tokens
    prompt_cache_resolution = resolve_prompt_cache_key(payload)
    if prompt_cache_resolution.key is not None:
        translated_payload["prompt_cache_key"] = prompt_cache_resolution.key
    prompt_cache_retention = _extract_prompt_cache_retention(payload)
    if prompt_cache_retention is not None:
        translated_payload["prompt_cache_retention"] = prompt_cache_retention
    _merge_passthrough_cache_extras(payload, translated_payload)

    try:
        translated = ResponsesRequest.model_validate(translated_payload)
    except ValueError as exc:
        raise AnthropicTranslationError(str(exc), param="messages") from exc
    return translated, prompt_cache_resolution


def anthropic_error(error_type: str, message: str) -> AnthropicErrorEnvelope:
    return AnthropicErrorEnvelope(type="error", error=AnthropicErrorData(type=error_type, message=message))


def anthropic_error_from_openai_payload(
    payload: Mapping[str, JsonValue] | UpstreamOpenAIErrorEnvelope | None,
    *,
    fallback_message: str,
    status_code: int | None = None,
) -> AnthropicErrorEnvelope:
    if payload is None:
        return anthropic_error(_anthropic_error_type(None, None, status_code), fallback_message)

    error_payload = payload.get("error")
    if not is_json_mapping(error_payload):
        return anthropic_error(_anthropic_error_type(None, None, status_code), fallback_message)

    code = error_payload.get("code")
    error_type = error_payload.get("type")
    message = error_payload.get("message")
    code_str = code if isinstance(code, str) else None
    type_str = error_type if isinstance(error_type, str) else None
    message_str = message if isinstance(message, str) else fallback_message
    mapped_type = _anthropic_error_type(code_str, type_str, status_code)
    return anthropic_error(mapped_type, message_str)


async def collect_anthropic_response_from_openai_stream(
    stream: AsyncIterator[str],
    *,
    model: str,
) -> AnthropicMessageResponse | AnthropicErrorEnvelope:
    state = _CollectedResponseState(model=model)
    tool_index = _ToolCallIndex()

    async for line in stream:
        payload = parse_sse_data_json(line)
        if payload is None:
            continue

        _apply_payload_to_collected_state(state, payload, tool_index)
        if state.error is not None:
            return state.error

    return _build_anthropic_response(state)


async def extract_input_tokens_from_openai_stream(
    stream: AsyncIterator[str],
) -> tuple[int | None, AnthropicErrorEnvelope | None]:
    async for line in stream:
        payload = parse_sse_data_json(line)
        if payload is None:
            continue

        event_type = payload.get("type")
        if event_type in ("error", "response.failed"):
            error = _error_from_stream_payload(payload)
            return None, error

        if event_type in ("response.completed", "response.incomplete"):
            response_payload = payload.get("response")
            if not is_json_mapping(response_payload):
                return None, None
            input_tokens, _ = _extract_usage(response_payload.get("usage"))
            return input_tokens, None

    return None, None


async def stream_anthropic_events_from_openai_stream(
    stream: AsyncIterator[str],
    *,
    model: str,
) -> AsyncIterator[str]:
    state = _StreamState(model=model, message_id=_next_message_id())
    tool_index = _ToolCallIndex()

    async for line in stream:
        payload = parse_sse_data_json(line)
        if payload is None:
            continue

        event_type = payload.get("type")
        if event_type in ("error", "response.failed"):
            error = _error_from_stream_payload(payload)
            yield _format_anthropic_sse("error", error.model_dump(mode="json", exclude_none=True))
            return

        response_payload = payload.get("response")
        response_map = response_payload if is_json_mapping(response_payload) else None

        if response_map is not None:
            response_id = response_map.get("id")
            if isinstance(response_id, str) and response_id:
                state.message_id = response_id
            input_tokens, output_tokens = _extract_usage(response_map.get("usage"))
            if input_tokens is not None:
                state.input_tokens = input_tokens
            if output_tokens is not None:
                state.output_tokens = output_tokens

        if not state.started:
            yield _format_anthropic_sse(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": state.message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": state.model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {
                            "input_tokens": state.input_tokens or 0,
                            "output_tokens": 0,
                        },
                    },
                },
            )
            state.started = True

        if event_type in ("response.output_text.delta", "response.refusal.delta"):
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                if state.text_block_index is None:
                    block_index = state.next_block_index
                    state.next_block_index += 1
                    state.text_block_index = block_index
                    yield _format_anthropic_sse(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                yield _format_anthropic_sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": state.text_block_index,
                        "delta": {"type": "text_delta", "text": delta},
                    },
                )

        tool_delta = _tool_call_delta_from_payload(payload, tool_index)
        if tool_delta is not None:
            _merge_tool_call_delta(state.tool_calls, tool_delta)

        if event_type in ("response.completed", "response.incomplete"):
            if state.text_block_index is not None:
                yield _format_anthropic_sse(
                    "content_block_stop",
                    {
                        "type": "content_block_stop",
                        "index": state.text_block_index,
                    },
                )
                state.text_block_index = None

            completed_tool_blocks = _content_blocks_from_response_output(response_map)
            tool_use_blocks = [
                block for block in completed_tool_blocks if isinstance(block, AnthropicResponseToolUseBlock)
            ]
            if not tool_use_blocks:
                tool_use_blocks = _tool_use_blocks_from_call_states(state.tool_calls)

            for block in tool_use_blocks:
                block_index = state.next_block_index
                state.next_block_index += 1
                yield _format_anthropic_sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": {},
                        },
                    },
                )
                yield _format_anthropic_sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(block.input, ensure_ascii=True, separators=(",", ":")),
                        },
                    },
                )
                yield _format_anthropic_sse(
                    "content_block_stop",
                    {
                        "type": "content_block_stop",
                        "index": block_index,
                    },
                )

            stop_reason, stop_sequence = _extract_stop_reason(
                response_map,
                event_type=event_type,
                has_tool_use=bool(tool_use_blocks),
            )
            state.stop_reason = stop_reason
            state.stop_sequence = stop_sequence

            yield _format_anthropic_sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": stop_reason,
                        "stop_sequence": stop_sequence,
                    },
                    "usage": {
                        "output_tokens": state.output_tokens or 0,
                    },
                },
            )
            yield _format_anthropic_sse(
                "message_stop",
                {
                    "type": "message_stop",
                    "usage": {
                        "input_tokens": state.input_tokens or 0,
                        "output_tokens": state.output_tokens or 0,
                    },
                },
            )
            return


def _extract_system_text(payload: AnthropicMessagesRequest) -> str:
    system = payload.system
    if system is None:
        return ""
    if isinstance(system, str):
        return system

    parts: list[str] = []
    for block in system:
        parts.append(block.text)
    return "\n".join(part for part in parts if part)


def resolve_prompt_cache_key(payload: AnthropicMessagesRequest) -> PromptCacheKeyResolution:
    explicit_key = _extract_explicit_prompt_cache_key(payload)
    if explicit_key is not None:
        return PromptCacheKeyResolution(key=explicit_key, source="explicit")

    metadata_key = _extract_prompt_cache_key_from_metadata(payload)
    if metadata_key is not None:
        return PromptCacheKeyResolution(key=metadata_key, source="metadata")

    cache_control_key = _derive_prompt_cache_key_from_cache_control(payload)
    if cache_control_key is not None:
        return PromptCacheKeyResolution(key=cache_control_key, source="cache_control")

    anchor_key = _derive_prompt_cache_key_from_conversation_anchor(payload)
    if anchor_key is not None:
        return PromptCacheKeyResolution(key=anchor_key, source="anchor")

    return PromptCacheKeyResolution(key=None, source="none")


def _extract_explicit_prompt_cache_key(payload: AnthropicMessagesRequest) -> str | None:
    if not payload.model_extra:
        return None

    for key in ("prompt_cache_key", "promptCacheKey"):
        value = payload.model_extra.get(key)
        normalized = _normalize_prompt_cache_key_value(value)
        if normalized is not None:
            return normalized
    return None


def _extract_prompt_cache_key_from_metadata(payload: AnthropicMessagesRequest) -> str | None:
    if not payload.model_extra:
        return None

    metadata = payload.model_extra.get("metadata")
    if not is_json_mapping(metadata):
        return None

    for key in (
        "prompt_cache_key",
        "promptCacheKey",
        "conversation_id",
        "conversationId",
        "thread_id",
        "threadId",
    ):
        normalized = _normalize_prompt_cache_key_value(metadata.get(key))
        if normalized is not None:
            return normalized
    return None


def _extract_prompt_cache_retention(payload: AnthropicMessagesRequest) -> str | None:
    if not payload.model_extra:
        return None

    value = payload.model_extra.get("prompt_cache_retention")
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnthropicTranslationError("prompt_cache_retention must be a string", param="prompt_cache_retention")

    normalized = value.strip()
    if not normalized:
        raise AnthropicTranslationError(
            "prompt_cache_retention must be a non-empty string",
            param="prompt_cache_retention",
        )
    return normalized


def _merge_passthrough_cache_extras(
    payload: AnthropicMessagesRequest,
    translated_payload: dict[str, JsonValue],
) -> None:
    if not payload.model_extra:
        return
    for key in _PASSTHROUGH_CACHE_EXTRA_KEYS:
        if key in translated_payload:
            continue
        canonical = _CAMEL_TO_SNAKE_CACHE_KEY.get(key, key)
        if canonical != key and canonical in translated_payload:
            continue
        value = payload.model_extra.get(key)
        if value is None:
            continue
        translated_payload[canonical] = value


def _derive_prompt_cache_key_from_cache_control(payload: AnthropicMessagesRequest) -> str | None:
    dumped = payload.model_dump(mode="python", exclude_none=True)
    segments: list[JsonValue] = []

    system = dumped.get("system")
    if is_json_list(system):
        for index, block in enumerate(system):
            if not is_json_mapping(block):
                continue
            cache_control = _cache_control_payload(block)
            if cache_control is None:
                continue
            segment: dict[str, JsonValue] = {
                "scope": "system",
                "index": index,
                "cache_control": cache_control,
            }
            block_type = block.get("type")
            if isinstance(block_type, str):
                segment["type"] = block_type
            text = block.get("text")
            if isinstance(text, str):
                segment["text"] = text
            segments.append(segment)

    messages = dumped.get("messages")
    if is_json_list(messages):
        for message_index, message in enumerate(messages):
            if not is_json_mapping(message):
                continue
            role = message.get("role")
            content = message.get("content")
            if not is_json_list(content):
                continue
            for block_index, block in enumerate(content):
                if not is_json_mapping(block):
                    continue
                cache_control = _cache_control_payload(block)
                if cache_control is None:
                    continue
                segment = {
                    "scope": "message",
                    "message_index": message_index,
                    "block_index": block_index,
                    "cache_control": cache_control,
                }
                if isinstance(role, str):
                    segment["role"] = role
                block_type = block.get("type")
                if isinstance(block_type, str):
                    segment["type"] = block_type
                text = block.get("text")
                if isinstance(text, str):
                    segment["text"] = text
                segments.append(segment)

    if not segments:
        return None

    canonical = json.dumps(segments, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"anthropic-cache:{digest}"


def _derive_prompt_cache_key_from_conversation_anchor(payload: AnthropicMessagesRequest) -> str | None:
    dumped = payload.model_dump(mode="python", exclude_none=True)
    system_text = _normalize_system_text_from_dump(dumped.get("system"))
    first_user_text = _first_user_text_from_dump(dumped.get("messages"))
    tool_signature = _tool_signature_from_dump(dumped.get("tools"))

    anchor: dict[str, JsonValue] = {}
    if system_text:
        anchor["system"] = system_text
    if first_user_text:
        anchor["first_user"] = first_user_text
    if tool_signature:
        anchor["tools"] = _json_array_from_objects(tool_signature)

    if not anchor:
        return None

    canonical = json.dumps(anchor, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"anthropic-anchor:{digest}"


def _cache_control_payload(block: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    value = block.get("cache_control")
    if not is_json_mapping(value):
        return None
    return dict(value)


def _normalize_system_text_from_dump(system_value: JsonValue) -> str:
    if isinstance(system_value, str):
        return system_value.strip()
    if not is_json_list(system_value):
        return ""

    parts: list[str] = []
    for block in system_value:
        if not is_json_mapping(block):
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _first_user_text_from_dump(messages_value: JsonValue) -> str:
    if not is_json_list(messages_value):
        return ""
    for message in messages_value:
        if not is_json_mapping(message):
            continue
        role = message.get("role")
        if role != "user":
            continue
        content = message.get("content")
        text = _content_text_from_dump(content)
        if text:
            return text
    return ""


def _content_text_from_dump(content: JsonValue) -> str:
    if isinstance(content, str):
        return content.strip()
    if not is_json_list(content):
        return ""
    parts: list[str] = []
    for block in content:
        if not is_json_mapping(block):
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _tool_signature_from_dump(tools_value: JsonValue) -> list[dict[str, JsonValue]]:
    if not is_json_list(tools_value):
        return []
    signatures: list[dict[str, JsonValue]] = []
    for tool in tools_value:
        if not is_json_mapping(tool):
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        signature: dict[str, JsonValue] = {"name": name.strip()}
        input_schema = tool.get("input_schema")
        if is_json_mapping(input_schema):
            signature["input_schema"] = dict(input_schema)
        signatures.append(signature)
    return signatures


def _json_array_from_strings(values: list[str]) -> list[JsonValue]:
    return [value for value in values]


def _json_array_from_objects(values: list[dict[str, JsonValue]]) -> list[JsonValue]:
    return [value for value in values]


def _normalize_prompt_cache_key_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) <= 256:
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"anthropic-key:{digest}"


def _to_openai_messages(messages: list[AnthropicMessage]) -> list[dict[str, JsonValue]]:
    normalized: list[dict[str, JsonValue]] = []
    for message in messages:
        if message.role == "assistant":
            normalized.extend(_assistant_message_to_openai(message))
            continue
        if message.role == "system":
            normalized.extend(_system_message_to_openai(message))
            continue
        normalized.extend(_user_message_to_openai(message))
    return normalized


def _system_message_to_openai(message: AnthropicMessage) -> list[dict[str, JsonValue]]:
    if isinstance(message.content, str):
        return [{"role": "system", "content": message.content}]

    text_parts: list[JsonValue] = []
    for block in message.content:
        text_part = _text_content_part_from_block(block)
        if text_part is not None:
            text_parts.append(text_part)

    if not text_parts:
        return []
    return [{"role": "system", "content": text_parts}]


def _assistant_message_to_openai(message: AnthropicMessage) -> list[dict[str, JsonValue]]:
    if isinstance(message.content, str):
        return [{"role": "assistant", "content": message.content}]

    text_parts: list[JsonValue] = []
    tool_calls: list[JsonValue] = []
    for block in message.content:
        block_type = _block_type(block)
        if block_type in ("thinking", "redacted_thinking"):
            continue

        text_part = _text_content_part_from_block(block)
        if text_part is not None:
            text_parts.append(text_part)
            continue

        tool_use = _tool_use_from_block(block)
        if tool_use is not None:
            call_id, name, input_payload = tool_use
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")),
                    },
                }
            )
            continue
        if block_type == "tool_result":
            raise AnthropicTranslationError(
                "assistant messages support only text and tool_use blocks",
                param="messages",
            )

    if not text_parts and not tool_calls:
        return []

    assistant_message: dict[str, JsonValue] = {"role": "assistant"}
    if text_parts:
        assistant_message["content"] = text_parts
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls
    return [assistant_message]


def _user_message_to_openai(message: AnthropicMessage) -> list[dict[str, JsonValue]]:
    if isinstance(message.content, str):
        return [{"role": "user", "content": message.content}]

    mapped: list[dict[str, JsonValue]] = []
    content_parts: list[JsonValue] = []
    for block in message.content:
        block_type = _block_type(block)
        if block_type in ("thinking", "redacted_thinking"):
            continue

        if block_type == "tool_use":
            raise AnthropicTranslationError(
                "user messages support only text and tool_result blocks",
                param="messages",
            )

        tool_result = _tool_result_from_block(block)
        if tool_result is not None:
            tool_use_id, output_text = tool_result
            if content_parts:
                mapped.append({"role": "user", "content": list(content_parts)})
                content_parts = []
            mapped.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_use_id,
                    "content": output_text,
                }
            )
            continue

        text_part = _text_content_part_from_block(block)
        if text_part is not None:
            content_parts.append(text_part)
            continue

        image_part = _input_image_from_block(block)
        if image_part is not None:
            content_parts.append(image_part)
            continue

        block_map = _block_map(block)
        if block_map is not None:
            content_parts.append(dict(block_map))

    if content_parts:
        mapped.append({"role": "user", "content": content_parts})
    return mapped


def _tool_result_output(block: AnthropicToolResultBlock) -> str:
    return _tool_result_output_from_raw(block.content, block.is_error)


def _tool_result_output_from_raw(
    raw_content: JsonValue | list[AnthropicToolResultTextBlock],
    is_error: bool | None,
) -> str:
    text = _tool_result_text_from_raw(raw_content)

    if is_error:
        return json.dumps(
            {"is_error": True, "content": text},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return text


def _tool_result_text_from_raw(raw_content: JsonValue | list[AnthropicToolResultTextBlock]) -> str:
    if isinstance(raw_content, str):
        return raw_content
    if not isinstance(raw_content, list):
        return ""

    parts: list[str] = []
    for part in raw_content:
        if isinstance(part, AnthropicToolResultTextBlock):
            parts.append(part.text)
            continue
        if isinstance(part, str):
            parts.append(part)
            continue
        if is_json_mapping(part):
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _tool_result_from_block(block: AnthropicMessageBlock) -> tuple[str, str] | None:
    if isinstance(block, AnthropicToolResultBlock):
        return block.tool_use_id, _tool_result_output(block)

    block_map = _block_map(block)
    if block_map is None:
        return None
    if _block_type(block) != "tool_result":
        return None

    tool_use_id = block_map.get("tool_use_id")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        raise AnthropicTranslationError("tool_result blocks require tool_use_id", param="messages")

    raw_is_error = block_map.get("is_error")
    is_error = raw_is_error if isinstance(raw_is_error, bool) else None
    raw_content = block_map.get("content", "")
    return tool_use_id, _tool_result_output_from_raw(raw_content, is_error)


def _tool_use_from_block(block: AnthropicMessageBlock) -> tuple[str, str, dict[str, JsonValue]] | None:
    if isinstance(block, AnthropicToolUseBlock):
        return block.id, block.name, dict(block.input)

    block_map = _block_map(block)
    if block_map is None:
        return None
    if _block_type(block) != "tool_use":
        return None

    call_id = block_map.get("id")
    name = block_map.get("name")
    if not isinstance(call_id, str) or not call_id:
        raise AnthropicTranslationError("tool_use blocks require id", param="messages")
    if not isinstance(name, str) or not name:
        raise AnthropicTranslationError("tool_use blocks require name", param="messages")

    raw_input = block_map.get("input")
    if is_json_mapping(raw_input):
        return call_id, name, dict(raw_input)
    return call_id, name, {}


def _input_image_from_block(block: AnthropicMessageBlock) -> dict[str, JsonValue] | None:
    block_map = _block_map(block)
    if block_map is None:
        return None
    if _block_type(block) != "image":
        return None

    source = block_map.get("source")
    if not is_json_mapping(source):
        return None

    source_type = source.get("type")
    if source_type == "url":
        url = source.get("url")
        if isinstance(url, str) and url:
            return {"type": "input_image", "image_url": url}
        return None

    if source_type == "base64":
        data = source.get("data")
        if not isinstance(data, str) or not data:
            return None
        media_type = source.get("media_type")
        mime_type = media_type if isinstance(media_type, str) and media_type else "application/octet-stream"
        return {"type": "input_image", "image_url": f"data:{mime_type};base64,{data}"}

    return None


def _text_content_part_from_block(block: AnthropicMessageBlock) -> dict[str, JsonValue] | None:
    text = _text_from_block(block)
    if not isinstance(text, str):
        return None
    # OpenAI-compatible upstream rejects Anthropic-only part fields
    # such as cache_control/metadata inside message content.
    return {"type": "text", "text": text}


def _text_from_block(block: AnthropicMessageBlock) -> str | None:
    if isinstance(block, AnthropicTextBlock):
        return block.text
    block_map = _block_map(block)
    if block_map is None:
        return None
    text = block_map.get("text")
    if isinstance(text, str):
        return text
    return None


def _block_type(block: AnthropicMessageBlock) -> str | None:
    if isinstance(block, (AnthropicTextBlock, AnthropicToolUseBlock, AnthropicToolResultBlock)):
        return block.type
    block_map = _block_map(block)
    if block_map is None:
        return None
    block_type = block_map.get("type")
    if isinstance(block_type, str):
        return block_type
    return None


def _block_map(block: AnthropicMessageBlock) -> Mapping[str, JsonValue] | None:
    if isinstance(block, (AnthropicTextBlock, AnthropicToolUseBlock, AnthropicToolResultBlock)):
        return block.model_dump(mode="python", exclude_none=True)
    if isinstance(block, dict):
        return block
    return None


def _translate_tools(tools: list[AnthropicToolDefinition]) -> list[JsonValue]:
    translated: list[JsonValue] = []
    for tool in tools:
        translated.append(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            }
        )
    return translated


def _translate_tool_choice(choice: AnthropicToolChoice | None) -> JsonValue | None:
    if choice is None:
        return None
    if choice.type == "auto":
        return "auto"
    if choice.type == "any":
        return "required"
    if choice.type == "none":
        return "none"
    if choice.type == "tool":
        if not choice.name:
            raise AnthropicTranslationError("tool_choice.name is required when type is 'tool'", param="tool_choice")
        return {"type": "function", "name": choice.name}
    raise AnthropicTranslationError("Unsupported tool_choice", param="tool_choice")


def _apply_payload_to_collected_state(
    state: _CollectedResponseState,
    payload: Mapping[str, JsonValue],
    tool_index: _ToolCallIndex,
) -> None:
    event_type = payload.get("type")

    if event_type in ("response.output_text.delta", "response.refusal.delta"):
        delta = payload.get("delta")
        if isinstance(delta, str):
            state.text_parts.append(delta)

    tool_delta = _tool_call_delta_from_payload(payload, tool_index)
    if tool_delta is not None:
        _merge_tool_call_delta(state.tool_calls, tool_delta)

    if event_type in ("error", "response.failed"):
        state.error = _error_from_stream_payload(payload)
        return

    if event_type not in ("response.completed", "response.incomplete"):
        return

    response_payload = payload.get("response")
    if not is_json_mapping(response_payload):
        return

    state.completed_response = response_payload
    response_id = response_payload.get("id")
    if isinstance(response_id, str):
        state.response_id = response_id

    input_tokens, output_tokens = _extract_usage(response_payload.get("usage"))
    state.input_tokens = input_tokens
    state.output_tokens = output_tokens
    stop_reason, stop_sequence = _extract_stop_reason(
        response_payload,
        event_type=event_type,
        has_tool_use=bool(state.tool_calls),
    )
    state.stop_reason = stop_reason
    state.stop_sequence = stop_sequence


def _build_anthropic_response(state: _CollectedResponseState) -> AnthropicMessageResponse:
    content_blocks = _content_blocks_from_response_output(state.completed_response)
    if not content_blocks:
        content_blocks = _content_blocks_from_state(state)

    stop_reason = state.stop_reason
    if stop_reason is None:
        has_tool_use = any(isinstance(block, AnthropicResponseToolUseBlock) for block in content_blocks)
        stop_reason = "tool_use" if has_tool_use else "end_turn"

    return AnthropicMessageResponse(
        id=state.response_id or _next_message_id(),
        model=state.model,
        content=content_blocks,
        stop_reason=stop_reason,
        stop_sequence=state.stop_sequence,
        usage=AnthropicUsage(
            input_tokens=state.input_tokens or 0,
            output_tokens=state.output_tokens or 0,
        ),
    )


def _content_blocks_from_state(
    state: _CollectedResponseState,
) -> list[AnthropicResponseTextBlock | AnthropicResponseToolUseBlock]:
    content: list[AnthropicResponseTextBlock | AnthropicResponseToolUseBlock] = []

    if state.text_parts:
        content.append(AnthropicResponseTextBlock(type="text", text="".join(state.text_parts)))

    content.extend(_tool_use_blocks_from_call_states(state.tool_calls))
    return content


def _content_blocks_from_response_output(
    response_payload: Mapping[str, JsonValue] | None,
) -> list[AnthropicResponseTextBlock | AnthropicResponseToolUseBlock]:
    if response_payload is None:
        return []

    output = response_payload.get("output")
    if not is_json_list(output):
        return []

    content: list[AnthropicResponseTextBlock | AnthropicResponseToolUseBlock] = []
    for item in output:
        if not is_json_mapping(item):
            continue
        item_type = item.get("type")

        if item_type == "message":
            content.extend(_text_blocks_from_message_item(item))
            continue

        if item_type in ("function_call", "tool_call"):
            content.append(_tool_use_block_from_output_item(item))
            continue

        if item_type in ("output_text", "refusal"):
            text_value = item.get("text")
            if not isinstance(text_value, str):
                text_value = item.get("refusal") if isinstance(item.get("refusal"), str) else None
            if isinstance(text_value, str):
                content.append(AnthropicResponseTextBlock(type="text", text=text_value))

    return content


def _text_blocks_from_message_item(item: Mapping[str, JsonValue]) -> list[AnthropicResponseTextBlock]:
    blocks: list[AnthropicResponseTextBlock] = []
    raw_content = item.get("content")
    if not is_json_list(raw_content):
        return blocks

    for part in raw_content:
        if not is_json_mapping(part):
            continue
        part_type = part.get("type")
        if part_type not in ("output_text", "text", "refusal"):
            continue
        text_value = part.get("text")
        if not isinstance(text_value, str):
            refusal_value = part.get("refusal")
            text_value = refusal_value if isinstance(refusal_value, str) else None
        if isinstance(text_value, str):
            blocks.append(AnthropicResponseTextBlock(type="text", text=text_value))

    return blocks


def _tool_use_blocks_from_call_states(states: list[_ToolCallState]) -> list[AnthropicResponseToolUseBlock]:
    blocks: list[AnthropicResponseToolUseBlock] = []
    for state in states:
        call_id = state.call_id or f"toolu_{state.index}_{uuid.uuid4().hex[:8]}"
        name = state.name or "tool"
        blocks.append(
            AnthropicResponseToolUseBlock(
                type="tool_use",
                id=call_id,
                name=name,
                input=_parse_tool_input(state.arguments),
            )
        )
    return blocks


def _tool_use_block_from_output_item(item: Mapping[str, JsonValue]) -> AnthropicResponseToolUseBlock:
    call_id = _first_str(item.get("call_id"), item.get("id")) or f"toolu_{uuid.uuid4().hex[:8]}"
    name = _first_str(item.get("name")) or "tool"
    arguments = item.get("arguments")
    argument_str = arguments if isinstance(arguments, str) else "{}"
    return AnthropicResponseToolUseBlock(
        type="tool_use",
        id=call_id,
        name=name,
        input=_parse_tool_input(argument_str),
    )


def _parse_tool_input(arguments: str) -> dict[str, JsonValue]:
    if not arguments:
        return {}

    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {"raw": arguments}

    if is_json_mapping(parsed):
        return dict(parsed)
    return {"value": parsed}


def _extract_usage(usage_payload: JsonValue) -> tuple[int | None, int | None]:
    if not is_json_mapping(usage_payload):
        return None, None

    input_tokens_raw = usage_payload.get("input_tokens")
    output_tokens_raw = usage_payload.get("output_tokens")

    input_tokens = input_tokens_raw if isinstance(input_tokens_raw, int) else None
    output_tokens = output_tokens_raw if isinstance(output_tokens_raw, int) else None
    return input_tokens, output_tokens


def _extract_stop_reason(
    response_payload: Mapping[str, JsonValue] | None,
    *,
    event_type: JsonValue,
    has_tool_use: bool,
) -> tuple[AnthropicStopReason, str | None]:
    stop_sequence: str | None = None
    if response_payload is not None:
        raw_stop_sequence = response_payload.get("stop_sequence")
        if isinstance(raw_stop_sequence, str):
            stop_sequence = raw_stop_sequence

    if has_tool_use:
        return "tool_use", stop_sequence

    if event_type == "response.incomplete" and response_payload is not None:
        incomplete_details = response_payload.get("incomplete_details")
        if is_json_mapping(incomplete_details):
            reason = incomplete_details.get("reason")
            if reason in ("max_output_tokens", "max_tokens"):
                return "max_tokens", stop_sequence
            if reason == "stop_sequence":
                return "stop_sequence", stop_sequence

    if stop_sequence is not None:
        return "stop_sequence", stop_sequence

    return "end_turn", stop_sequence


def _error_from_stream_payload(payload: Mapping[str, JsonValue]) -> AnthropicErrorEnvelope:
    event_type = payload.get("type")
    if event_type == "error":
        error_payload = payload.get("error")
    elif event_type == "response.failed":
        response_payload = payload.get("response")
        error_payload = response_payload.get("error") if is_json_mapping(response_payload) else None
    else:
        error_payload = None

    if not is_json_mapping(error_payload):
        return anthropic_error("api_error", "Upstream error")

    code = error_payload.get("code")
    type_value = error_payload.get("type")
    message = error_payload.get("message")
    return anthropic_error(
        _anthropic_error_type(
            code if isinstance(code, str) else None,
            type_value if isinstance(type_value, str) else None,
            None,
        ),
        message if isinstance(message, str) else "Upstream error",
    )


def _anthropic_error_type(code: str | None, error_type: str | None, status_code: int | None) -> str:
    normalized_code = code.lower() if isinstance(code, str) else ""
    normalized_type = error_type.lower() if isinstance(error_type, str) else ""

    if normalized_code in {"invalid_api_key", "authentication_error"} or normalized_type == "authentication_error":
        return "authentication_error"
    if normalized_code in {"model_not_allowed", "insufficient_permissions"} or normalized_type == "permission_error":
        return "permission_error"
    if normalized_code in {"rate_limit_exceeded", "too_many_requests"} or normalized_type == "rate_limit_error":
        return "rate_limit_error"
    if normalized_code in {"invalid_request_error", "bad_request", "not_found"}:
        return "invalid_request_error"

    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code is not None and status_code < 500:
        return "invalid_request_error"
    return "api_error"


def _format_anthropic_sse(event_type: str, payload: Mapping[str, JsonValue]) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"event: {event_type}\ndata: {data}\n\n"


def _tool_call_delta_from_payload(
    payload: Mapping[str, JsonValue],
    indexer: _ToolCallIndex,
) -> _ToolCallDelta | None:
    if not _is_tool_call_event(payload):
        return None

    fields = _extract_tool_call_fields(payload)
    if fields is None:
        return None

    call_id, name, arguments = fields
    index = indexer.index_for(call_id, name)
    return _ToolCallDelta(index=index, call_id=call_id, name=name, arguments=arguments)


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


def _extract_tool_call_fields(payload: Mapping[str, JsonValue]) -> tuple[str | None, str | None, str | None] | None:
    candidate = _select_tool_call_candidate(payload)
    delta = candidate.get("delta")
    delta_map = _as_mapping(delta)
    delta_text = delta if isinstance(delta, str) else None

    call_id = _first_str(candidate.get("call_id"), candidate.get("tool_call_id"), candidate.get("id"))
    if call_id is None and delta_map is not None:
        call_id = _first_str(delta_map.get("id"), delta_map.get("call_id"), delta_map.get("tool_call_id"))

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

    if call_id is None and name is None and arguments is None:
        return None
    return call_id, name, arguments


def _select_tool_call_candidate(payload: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    item = _as_mapping(payload.get("item"))
    if item is not None:
        item_type = item.get("type")
        if isinstance(item_type, str) and ("tool" in item_type or "function" in item_type):
            return item
        if any(key in item for key in ("call_id", "tool_call_id", "arguments", "function", "name")):
            return item
    return payload


def _merge_tool_call_delta(tool_calls: list[_ToolCallState], delta: _ToolCallDelta) -> None:
    while len(tool_calls) <= delta.index:
        tool_calls.append(_ToolCallState(index=len(tool_calls)))
    tool_calls[delta.index].apply_delta(delta)


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


def _next_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"
