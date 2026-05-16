from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping

from app.core.errors import ResponseFailedEvent
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_dict

type JsonPayload = Mapping[str, JsonValue] | ResponseFailedEvent

SSE_KEEPALIVE_FRAME = ": keepalive\n\n"


async def inject_sse_keepalives(
    source: AsyncIterator[str],
    interval_seconds: float,
) -> AsyncIterator[str]:
    """Wrap an SSE event iterator and emit comment heartbeats on idle gaps.

    Comment frames (lines starting with ``:``) are mandated by the SSE spec to
    be ignored by parsers, so they are safe to inject between event blocks.
    They keep the TCP path warm so half-open sockets surface as write errors
    instead of hanging forever, and let aggressive intermediaries see traffic.

    A non-positive ``interval_seconds`` disables injection entirely.
    """
    if interval_seconds <= 0:
        async for chunk in source:
            yield chunk
        return

    async def _next_chunk(it: AsyncIterator[str]) -> str:
        return await it.__anext__()

    iterator = source.__aiter__()
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(_next_chunk(iterator))
            try:
                chunk = await asyncio.wait_for(
                    asyncio.shield(pending),
                    timeout=interval_seconds,
                )
            except asyncio.TimeoutError:
                yield SSE_KEEPALIVE_FRAME
                continue
            except StopAsyncIteration:
                pending = None
                break
            pending = None
            yield chunk
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except BaseException:
                pass


def format_sse_event(payload: JsonPayload) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type:
        return f"event: {event_type}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def format_sse_data(payload: Mapping[str, JsonValue]) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"data: {data}\n\n"


def parse_sse_data_json(event_block: str) -> dict[str, JsonValue] | None:
    data = extract_sse_data(event_block)
    if data is None:
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    if is_json_dict(payload):
        return payload
    return None


def extract_sse_data(event_block: str) -> str | None:
    data_lines = _extract_sse_data_lines(event_block)
    if data_lines is None:
        return None
    data = "\n".join(data_lines)
    if not data.strip():
        return None
    if data.strip() == "[DONE]":
        return None
    return data


def _extract_sse_data_lines(event_block: str) -> list[str] | None:
    data_lines: list[str] = []
    for raw_line in event_block.splitlines():
        if not raw_line:
            continue
        if raw_line.startswith(":"):
            continue

        field, value = _parse_sse_field(raw_line)
        if field == "data":
            data_lines.append(value)

    if not data_lines:
        return None
    return data_lines


def _parse_sse_field(line: str) -> tuple[str, str]:
    if ":" not in line:
        return line, ""
    field, value = line.split(":", 1)
    if value.startswith(" "):
        value = value[1:]
    return field, value
