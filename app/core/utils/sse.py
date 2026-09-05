from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping

from app.core.errors import ResponseFailedEvent
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_dict
from app.core.utils.shared_future import wait_on_shared_future

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
                    # Not ``wait_for(shield(pending))``: Python 3.14's shield
                    # leaks a done-callback onto ``pending`` at every keepalive
                    # timeout, so a quiet upstream accumulates callbacks (and
                    # O(n) remove scans) until the next chunk arrives.
                    chunk = await wait_on_shared_future(
                        pending,
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


def _is_single_line_json_object_text(text: str) -> bool:
    return text.startswith("{") and "\n" not in text and "\r" not in text


def format_sse_event_from_text(payload: Mapping[str, JsonValue], text: str) -> str:
    """Frame an already-serialized JSON object without re-dumping it.

    ``text`` MUST be a JSON serialization of ``payload`` (the upstream frame the
    payload was parsed from, or a compact re-dump of it). The result carries the
    same ``event:`` line ``format_sse_event(payload)`` would emit, but its
    ``data:`` line is ``text`` verbatim, so non-ASCII stays UTF-8 instead of
    being ``\\uXXXX``-escaped and the JSON is never re-serialized. Anything
    that would not produce a canonical single-line block (multi-line or
    whitespace-prefixed text) falls back to ``format_sse_event``.
    """
    if not _is_single_line_json_object_text(text):
        return format_sse_event(payload)
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type:
        return f"event: {event_type}\ndata: {text}\n\n"
    return f"data: {text}\n\n"


def format_sse_data(payload: Mapping[str, JsonValue]) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"data: {data}\n\n"


class ParsedSseBlock(str):
    """An SSE event block that carries its already-parsed ``data:`` payload.

    Behaves exactly like ``str`` (framing, encoding, comparison, ``startswith``);
    ``payload`` is what ``parse_sse_data_json`` returns for the same text, so a
    chain of stream stages parses each block once. The payload object is shared
    by every stage that receives the block: consumers MUST treat it as read-only
    and copy before mutating. Derived strings (``strip()``, concatenation) are
    plain ``str`` and take the full parse path again.
    """

    # ``__slots__`` is not supported on ``str`` subclasses; the instance dict
    # costs ~100-200 B per block, far less than a redundant JSON parse.
    payload: dict[str, JsonValue] | None

    def __new__(cls, block: str, payload: dict[str, JsonValue] | None) -> ParsedSseBlock:
        instance = super().__new__(cls, block)
        instance.payload = payload
        return instance


def sse_block_with_payload(event_block: str, payload: dict[str, JsonValue] | None) -> ParsedSseBlock:
    """Attach ``payload`` (the result of ``parse_sse_data_json(event_block)``) to the block."""
    if isinstance(event_block, ParsedSseBlock) and event_block.payload is payload:
        return event_block
    return ParsedSseBlock(event_block, payload)


def parse_sse_data_json_text(text: str) -> dict[str, JsonValue] | None:
    """Parse one ``data:`` value as a JSON object.

    Equivalent to ``parse_sse_data_json(f"data: {text}\\n\\n")`` but skips SSE
    line parsing when ``text`` is a single-line JSON object — the shape every
    upstream websocket frame has. Other inputs (blank, ``[DONE]``, multi-line or
    whitespace-prefixed text) take the exact SSE field path so the ``None`` and
    line-joining semantics stay identical.
    """
    if _is_single_line_json_object_text(text):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if is_json_dict(payload):
            return payload
        return None
    return parse_sse_data_json(f"data: {text}\n\n")


def parse_sse_data_json(event_block: str) -> dict[str, JsonValue] | None:
    if isinstance(event_block, ParsedSseBlock):
        return event_block.payload
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
