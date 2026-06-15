from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from app.core.openai.parsing import parse_sse_event
from app.core.utils.sse import (
    CODEX_KEEPALIVE_FRAME,
    SSE_KEEPALIVE_FRAME,
    extract_sse_data,
    format_sse_event,
    inject_sse_keepalives,
    parse_sse_data_json,
)

pytestmark = pytest.mark.unit


def test_format_sse_event_serializes_payload():
    payload = {"type": "response.completed", "response": {"id": "resp_1"}}
    result = format_sse_event(payload)
    assert result == 'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'


async def _agen(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


async def _slow_agen(items: list[str], delay: float) -> AsyncIterator[str]:
    for item in items:
        await asyncio.sleep(delay)
        yield item


@pytest.mark.asyncio
async def test_inject_sse_keepalives_passes_through_when_disabled():
    out = [chunk async for chunk in inject_sse_keepalives(_agen(["a\n\n", "b\n\n"]), 0)]
    assert out == ["a\n\n", "b\n\n"]


@pytest.mark.asyncio
async def test_inject_sse_keepalives_no_pings_when_source_is_fast():
    out = [chunk async for chunk in inject_sse_keepalives(_agen(["a\n\n", "b\n\n"]), 5.0)]
    assert out == ["a\n\n", "b\n\n"]


@pytest.mark.asyncio
async def test_inject_sse_keepalives_emits_pings_on_idle_gap():
    out = [chunk async for chunk in inject_sse_keepalives(_slow_agen(["a\n\n"], delay=0.25), 0.05)]
    assert out[-1] == "a\n\n"
    assert SSE_KEEPALIVE_FRAME in out
    assert out.count(SSE_KEEPALIVE_FRAME) >= 2


@pytest.mark.asyncio
async def test_inject_sse_keepalives_can_emit_codex_event_frame():
    out = [
        chunk
        async for chunk in inject_sse_keepalives(
            _slow_agen(["a\n\n"], delay=0.25),
            0.05,
            keepalive_frame=CODEX_KEEPALIVE_FRAME,
        )
    ]
    assert out[-1] == "a\n\n"
    assert CODEX_KEEPALIVE_FRAME in out
    assert out.count(CODEX_KEEPALIVE_FRAME) >= 2


@pytest.mark.asyncio
async def test_inject_sse_keepalives_keepalive_frame_is_sse_comment():
    assert SSE_KEEPALIVE_FRAME.startswith(":")
    assert SSE_KEEPALIVE_FRAME.endswith("\n\n")


def test_extract_sse_data_preserves_unicode_line_separators():
    # U+2028 / U+2029 are valid *unescaped* inside JSON strings. The SSE spec
    # delimits lines only by CR/LF/CRLF, so they must not split a data: payload.
    payload = {"type": "response.output_text.delta", "delta": "line1\u2028line2\u2029end"}
    block = "event: response.output_text.delta\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    data = extract_sse_data(block)

    assert data is not None
    assert json.loads(data) == payload


def test_parse_sse_data_json_preserves_unicode_line_separators():
    payload = {"type": "response.completed", "response": {"id": "resp_1", "status": "completed", "note": "a\u2028b"}}
    block = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    assert parse_sse_data_json(block) == payload


def test_parse_sse_event_parses_payload_with_unicode_line_separators():
    # The proxy receive path relies on parse_sse_event for terminal-event
    # detection, dedupe, and usage; an unescaped U+2028 used to drop the event.
    payload = {"type": "response.output_text.delta", "delta": "x\u2028y\u2029z"}
    block = "event: response.output_text.delta\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    event = parse_sse_event(block)

    assert event is not None
    assert event.type == "response.output_text.delta"


def test_extract_sse_data_joins_crlf_multiline_data():
    # CR, LF, and CRLF all remain valid line boundaries after the fix.
    block = "data: line1\r\ndata: line2\rdata: line3\n\n"

    assert extract_sse_data(block) == "line1\nline2\nline3"
