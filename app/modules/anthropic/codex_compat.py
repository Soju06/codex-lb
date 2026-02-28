from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.chat_responses import ChatCompletion
from app.core.openai.models import OpenAIErrorEnvelope
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.sse import format_sse_data

DEFAULT_CODEX_MODEL = "gpt-5.3-codex"


class AnthropicCodexCompatError(ValueError):
    """Raised when an Anthropic payload cannot be translated to Codex/OpenAI."""


def resolve_target_model(requested_model: str | None) -> str:
    # We intentionally route all Claude-model requests to Codex 5.3.
    _ = requested_model
    return DEFAULT_CODEX_MODEL


def payload_to_responses_request(
    payload: Mapping[str, JsonValue],
    *,
    target_model: str,
) -> tuple[ResponsesRequest, bool, str | None]:
    requested_model = _as_non_empty_string(payload.get("model"))
    stream_requested = bool(payload.get("stream"))

    openai_messages = _translate_messages(payload)
    if not openai_messages:
        raise AnthropicCodexCompatError("messages must contain at least one supported content block")

    openai_tools = _translate_tools(payload.get("tools"))
    openai_tool_choice = _translate_tool_choice(payload.get("tool_choice"))

    chat_payload: dict[str, JsonValue] = {
        "model": target_model,
        "messages": openai_messages,
        "tools": openai_tools,
    }
    if openai_tool_choice is not None:
        chat_payload["tool_choice"] = openai_tool_choice
    try:
        chat_request = ChatCompletionsRequest.model_validate(chat_payload)
        responses_payload = chat_request.to_responses_request()
    except (ValidationError, ValueError) as exc:
        raise AnthropicCodexCompatError(str(exc)) from exc

    responses_payload.model = target_model
    responses_payload.stream = True
    return responses_payload, stream_requested, requested_model


def chat_completion_to_anthropic_message(
    completion: ChatCompletion,
    *,
    requested_model: str | None,
    target_model: str,
) -> dict[str, JsonValue]:
    choice = completion.choices[0] if completion.choices else None
    message = choice.message if choice else None

    content_blocks: list[dict[str, JsonValue]] = []
    if message and isinstance(message.content, str) and message.content:
        content_blocks.append({"type": "text", "text": message.content})

    if message and message.tool_calls:
        for idx, call in enumerate(message.tool_calls):
            call_id = call.id or f"toolu_{uuid4().hex[:20]}_{idx}"
            name = "tool"
            raw_arguments = "{}"
            if call.function:
                if call.function.name:
                    name = call.function.name
                if call.function.arguments:
                    raw_arguments = call.function.arguments
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": name,
                    "input": _parse_tool_arguments(raw_arguments),
                }
            )

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    finish_reason = choice.finish_reason if choice else None
    stop_reason = _map_finish_reason_to_anthropic_stop_reason(
        finish_reason,
        has_tool_use=bool(message and message.tool_calls),
    )

    prompt_tokens = completion.usage.prompt_tokens if completion.usage else None
    completion_tokens = completion.usage.completion_tokens if completion.usage else None

    raw_id = completion.id or f"chatcmpl_{uuid4().hex[:12]}"
    msg_id = raw_id if raw_id.startswith("msg_") else f"msg_{raw_id}"
    model_name = requested_model or target_model

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(prompt_tokens or 0),
            "output_tokens": int(completion_tokens or 0),
        },
    }


def openai_error_to_anthropic_error(error_envelope: OpenAIErrorEnvelope) -> tuple[int, dict[str, JsonValue]]:
    error = error_envelope.error
    message = error.message or "Upstream error"
    code = (error.code or error.type or "").strip().lower()

    if code in {"invalid_api_key", "authentication_error", "unauthorized"}:
        return 401, _anthropic_error("authentication_error", message)
    if code in {"rate_limit_exceeded", "insufficient_quota", "usage_not_included", "quota_exceeded"}:
        return 429, _anthropic_error("rate_limit_error", message)
    if code in {"permission_error", "forbidden"}:
        return 403, _anthropic_error("permission_error", message)
    if code in {"invalid_request_error", "validation_error", "bad_request"}:
        return 400, _anthropic_error("invalid_request_error", message)
    if code == "no_accounts":
        return 503, _anthropic_error("api_error", message)
    return 502, _anthropic_error("api_error", message)


def proxy_payload_to_anthropic_error(payload: JsonValue, status_code: int) -> dict[str, JsonValue]:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                error_type = _map_http_status_to_anthropic_error_type(status_code)
                return _anthropic_error(error_type, message)
    return _anthropic_error(_map_http_status_to_anthropic_error_type(status_code), "Upstream error")


async def stream_message_as_anthropic_events(message_payload: Mapping[str, JsonValue]) -> AsyncIterator[str]:
    content = message_payload.get("content")
    content_blocks = content if isinstance(content, list) else []
    usage = message_payload.get("usage")
    usage_map = usage if isinstance(usage, dict) else {}

    message_start = {
        "id": message_payload.get("id"),
        "type": "message",
        "role": "assistant",
        "model": message_payload.get("model"),
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(_coerce_int(usage_map.get("input_tokens")) or 0),
            "output_tokens": 0,
        },
    }
    yield format_sse_data({"type": "message_start", "message": message_start})

    for index, block in enumerate(content_blocks):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "tool_use":
            start_block = {
                "type": "tool_use",
                "id": block.get("id"),
                "name": block.get("name"),
                "input": block.get("input") if isinstance(block.get("input"), dict) else {},
            }
            yield format_sse_data(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": start_block,
                }
            )
            yield format_sse_data({"type": "content_block_stop", "index": index})
            continue

        text = block.get("text")
        text_value = text if isinstance(text, str) else ""
        yield format_sse_data(
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            }
        )
        if text_value:
            yield format_sse_data(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": text_value},
                }
            )
        yield format_sse_data({"type": "content_block_stop", "index": index})

    yield format_sse_data(
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": message_payload.get("stop_reason"),
                "stop_sequence": None,
            },
            "usage": {
                "output_tokens": int(_coerce_int(usage_map.get("output_tokens")) or 0),
            },
        }
    )
    yield format_sse_data({"type": "message_stop"})


def _translate_messages(payload: Mapping[str, JsonValue]) -> list[dict[str, JsonValue]]:
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        raise AnthropicCodexCompatError("messages must be a list")

    translated: list[dict[str, JsonValue]] = []
    system_text = _extract_system_text(payload.get("system"))
    if system_text:
        translated.append({"role": "system", "content": system_text})

    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        role = _as_non_empty_string(raw_message.get("role"))
        if role not in {"user", "assistant", "system"}:
            continue
        content = raw_message.get("content")

        text_chunks, assistant_tool_uses, user_tool_results = _split_content_blocks(content)

        if role == "assistant":
            message_payload: dict[str, JsonValue] = {"role": "assistant"}
            if text_chunks:
                message_payload["content"] = "".join(text_chunks)
            if assistant_tool_uses:
                message_payload["tool_calls"] = assistant_tool_uses
            if "content" in message_payload or "tool_calls" in message_payload:
                translated.append(message_payload)
            continue

        if role == "system":
            if text_chunks:
                translated.append({"role": "system", "content": "".join(text_chunks)})
            continue

        if text_chunks:
            translated.append({"role": "user", "content": "".join(text_chunks)})
        for tool_result in user_tool_results:
            translated.append(tool_result)

    return translated


def _extract_system_text(system_value: JsonValue) -> str:
    if isinstance(system_value, str):
        return system_value
    if isinstance(system_value, list):
        parts: list[str] = []
        for item in system_value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    if (
        isinstance(system_value, dict)
        and system_value.get("type") == "text"
        and isinstance(system_value.get("text"), str)
    ):
        return system_value["text"]
    return ""


def _split_content_blocks(
    content: JsonValue,
) -> tuple[list[str], list[dict[str, JsonValue]], list[dict[str, JsonValue]]]:
    if isinstance(content, str):
        return [content], [], []

    blocks = content if isinstance(content, list) else [content]
    texts: list[str] = []
    assistant_tool_uses: list[dict[str, JsonValue]] = []
    user_tool_results: list[dict[str, JsonValue]] = []

    for block in blocks:
        if isinstance(block, str):
            texts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)
            continue
        if block_type == "tool_use":
            call_id = _as_non_empty_string(block.get("id")) or f"toolu_{uuid4().hex[:20]}"
            name = _as_non_empty_string(block.get("name")) or "tool"
            input_payload = block.get("input")
            if not isinstance(input_payload, dict):
                input_payload = {}
            assistant_tool_uses.append(
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
            call_id = _as_non_empty_string(block.get("tool_use_id")) or _as_non_empty_string(block.get("id"))
            if not call_id:
                call_id = f"toolu_{uuid4().hex[:20]}"
            user_tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_result_content_to_text(block.get("content")),
                }
            )

    return texts, assistant_tool_uses, user_tool_results


def _tool_result_content_to_text(content: JsonValue) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
                continue
            parts.append(json.dumps(part, ensure_ascii=False, separators=(",", ":")))
        return "".join(parts)
    if isinstance(content, dict):
        if content.get("type") == "text" and isinstance(content.get("text"), str):
            return content["text"]
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return str(content)


def _translate_tools(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    translated: list[dict[str, JsonValue]] = []
    for tool in value:
        if not isinstance(tool, dict):
            continue
        name = _as_non_empty_string(tool.get("name"))
        if not name:
            continue
        description = _as_non_empty_string(tool.get("description")) or ""
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}, "additionalProperties": True}
        translated.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": schema,
                },
            }
        )
    return translated


def _translate_tool_choice(value: JsonValue) -> JsonValue | None:
    if not isinstance(value, dict):
        return None
    choice_type = _as_non_empty_string(value.get("type"))
    if not choice_type:
        return None
    if choice_type == "auto":
        return "auto"
    if choice_type == "any":
        return "required"
    if choice_type == "tool":
        tool_name = _as_non_empty_string(value.get("name"))
        if tool_name:
            return {
                "type": "function",
                "function": {"name": tool_name},
            }
    return None


def _parse_tool_arguments(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except Exception:
        return {"_raw": value}
    if isinstance(decoded, dict):
        return decoded
    return {"value": decoded}


def _map_finish_reason_to_anthropic_stop_reason(finish_reason: str | None, *, has_tool_use: bool) -> str:
    if has_tool_use:
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"


def _map_http_status_to_anthropic_error_type(status_code: int) -> str:
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code == 400:
        return "invalid_request_error"
    return "api_error"


def _anthropic_error(error_type: str, message: str) -> dict[str, JsonValue]:
    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": message,
        },
    }


def _as_non_empty_string(value: JsonValue) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return None


def _coerce_int(value: JsonValue) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
