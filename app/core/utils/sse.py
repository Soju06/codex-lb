from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping

from app.core.errors import ResponseFailedEvent
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_dict

type JsonPayload = Mapping[str, JsonValue] | ResponseFailedEvent

# The SSE spec delimits lines only by CR, LF, or CRLF. str.splitlines() also
# breaks on other Unicode boundaries (VT, FF, FS/GS/RS, NEL, U+2028, U+2029),
# and U+2028/U+2029 are valid *unescaped* inside JSON strings, so splitting on
# them would corrupt a data: payload that legitimately contains one.
_SSE_LINE_BOUNDARY = re.compile(r"\r\n|\r|\n")

SSE_KEEPALIVE_FRAME = ": keepalive\n\n"
CODEX_KEEPALIVE_FRAME = 'event: codex.keepalive\ndata: {"type":"codex.keepalive"}\n\n'

# The exact single-event shape ``format_sse_event`` emits (and the upstream
# Codex backend sends): a leading ``event: <type>`` line, one JSON-object
# ``data:`` line, LF-only framing, and a blank-line terminator. Blocks that
# match can expose their event type without a JSON parse and are safe to
# relay downstream byte-for-byte.
_CANONICAL_SSE_BLOCK = re.compile(r"\Aevent: ([^\r\n]+)\ndata: \{[^\r\n]*\n\n\Z")


def sse_event_type_from_block(event_block: str) -> str | None:
    """Cheaply extract the event type from a canonically framed SSE block.

    Returns the ``event:`` line's value only when the block matches the exact
    shape ``format_sse_event`` produces (see ``_CANONICAL_SSE_BLOCK``).
    Anything else — data-only blocks, multi-line data, CR/CRLF framing,
    comment or ``id:`` lines, non-object data payloads, or an ``event:`` field
    that appears after ``data:`` (legal SSE, but not canonical here) — returns
    ``None`` so callers fall back to a full parse.
    """
    match = _CANONICAL_SSE_BLOCK.match(event_block)
    if match is None:
        return None
    return match.group(1)


async def inject_sse_keepalives(
    source: AsyncIterator[str],
    interval_seconds: float,
    *,
    keepalive_frame: str = SSE_KEEPALIVE_FRAME,
    on_keepalive: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    """Wrap an SSE event iterator and emit comment heartbeats on idle gaps.

    Comment frames (lines starting with ``:``) are mandated by the SSE spec to
    be ignored by parsers, so they are safe to inject between event blocks.
    They keep the TCP path warm so half-open sockets surface as write errors
    instead of hanging forever, and let aggressive intermediaries see traffic.

    A non-positive ``interval_seconds`` disables injection entirely.
    """
    iterator = source.__aiter__()
    try:
        if interval_seconds <= 0:
            async for chunk in iterator:
                yield chunk
            return

        async def _next_chunk(it: AsyncIterator[str]) -> str:
            return await it.__anext__()

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
                    if on_keepalive is not None:
                        on_keepalive()
                    yield keepalive_frame
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
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()
        elif iterator is not source:
            source_aclose = getattr(source, "aclose", None)
            if source_aclose is not None:
                await source_aclose()


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
    for raw_line in _SSE_LINE_BOUNDARY.split(event_block):
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
