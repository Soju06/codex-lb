from __future__ import annotations

import json

import pytest

from app.core.clients.proxy import StreamEventTooLargeError
from app.core.config.settings import get_settings
from app.modules.proxy.api import _source_alias_stream_body

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("fragment_size", [1, 7, 4096])
async def test_alias_stream_preserves_framing_and_tool_arguments(newline, fragment_size):
    payload = {
        "model": "opaque",
        "choices": [
            {
                "delta": {
                    "content": "Tiếng Việt\u2028opaque",
                    "tool_calls": [{"function": {"name": "run", "arguments": '{"model":"opaque"}'}}],
                }
            }
        ],
    }
    data = json.dumps(payload, ensure_ascii=False)
    # Split JSON over legal multiline data, also split UTF-8 and CRLF across chunks.
    data = data.replace(', "choices"', "," + newline + 'data: "choices"')
    wire = (
        "\ufeff: comment"
        + newline
        + "id: 2"
        + newline
        + "retry: 500"
        + newline
        + "data: "
        + data
        + newline * 2
        + "data: [DONE]"
        + newline * 2
    ).encode()
    closed = []

    async def upstream():
        try:
            for offset in range(0, len(wire), fragment_size):
                yield wire[offset : offset + fragment_size]
        finally:
            closed.append(True)

    blocks = [block async for block in _source_alias_stream_body(upstream(), model="public", upstream_model="opaque")]
    text = b"".join(blocks).decode()
    actual = json.loads(next(line[6:] for line in text.splitlines() if line.startswith("data: {")))
    assert actual == {**payload, "model": "public"}
    assert "id: 2" in text and "retry: 500" in text and ": comment" in text and "data: [DONE]" in text
    assert closed == [True]


@pytest.mark.asyncio
async def test_alias_stream_bounds_buffer_and_closes_upstream(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_sse_event_bytes", 64)
    closed = []

    async def upstream():
        try:
            yield b"data: " + b"x" * 65
        finally:
            closed.append(True)

    with pytest.raises(StreamEventTooLargeError):
        async for _ in _source_alias_stream_body(upstream(), model="public", upstream_model="opaque"):
            pass
    assert closed == [True]
