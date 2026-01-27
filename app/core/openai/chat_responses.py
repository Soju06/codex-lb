from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import cast

from app.core.errors import openai_error
from app.core.types import JsonValue


def _parse_data(line: str) -> dict | None:
    if line.startswith("data:"):
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return None
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
    return None


def iter_chat_chunks(lines: Iterable[str], model: str, *, created: int | None = None) -> Iterator[str]:
    created = created or int(time.time())
    for line in lines:
        payload = _parse_data(line)
        if not payload:
            continue
        event_type = payload.get("type")
        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            chunk = {
                "id": "chatcmpl_temp",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": delta},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        if event_type == "response.failed":
            response = payload.get("response")
            if isinstance(response, dict):
                error = response.get("error")
                if isinstance(error, dict):
                    error_payload = {"error": error}
                    yield f"data: {json.dumps(error_payload)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
        if event_type == "response.completed":
            done = {
                "id": "chatcmpl_temp",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"
            return


async def stream_chat_chunks(stream: AsyncIterator[str], model: str) -> AsyncIterator[str]:
    created = int(time.time())
    async for line in stream:
        for chunk in iter_chat_chunks([line], model=model, created=created):
            yield chunk
            if chunk.strip() == "data: [DONE]":
                return


async def collect_chat_completion(stream: AsyncIterator[str], model: str) -> dict[str, JsonValue]:
    created = int(time.time())
    content_parts: list[str] = []
    response_id: str | None = None
    usage: dict[str, JsonValue] | None = None

    async for line in stream:
        payload = _parse_data(line)
        if not payload:
            continue
        event_type = payload.get("type")
        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                content_parts.append(delta)
        if event_type == "response.failed":
            response = payload.get("response")
            if isinstance(response, dict):
                error = response.get("error")
                if isinstance(error, dict):
                    return {"error": error}
            return cast(dict[str, JsonValue], openai_error("upstream_error", "Upstream error"))
        if event_type == "response.completed":
            response = payload.get("response")
            if isinstance(response, dict):
                response_id_value = response.get("id")
                if isinstance(response_id_value, str):
                    response_id = response_id_value
                usage_value = response.get("usage")
                if isinstance(usage_value, dict):
                    usage = usage_value

    message_content = "".join(content_parts)
    completion = {
        "id": response_id or "chatcmpl_temp",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": message_content},
                "finish_reason": "stop",
            }
        ],
    }
    usage_payload = _map_usage(usage)
    if usage_payload is not None:
        completion["usage"] = usage_payload
    return completion


def _map_usage(usage: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
    if not usage:
        return None
    prompt_tokens = usage.get("input_tokens")
    completion_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")
    if not isinstance(prompt_tokens, int):
        prompt_tokens = None
    if not isinstance(completion_tokens, int):
        completion_tokens = None
    if not isinstance(total_tokens, int):
        total_tokens = None
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
