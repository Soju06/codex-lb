from __future__ import annotations

import json

import pytest

from app.core.clients.proxy import (
    StreamLineTooLongError,
    _build_upstream_headers,
    _iter_sse_lines,
    filter_inbound_headers,
)
from app.core.openai.parsing import parse_sse_event

pytestmark = pytest.mark.unit


def test_filter_inbound_headers_strips_auth_and_account():
    headers = {
        "Authorization": "Bearer x",
        "chatgpt-account-id": "acc_1",
        "Content-Type": "application/json",
        "X-Request-Id": "req_1",
    }
    filtered = filter_inbound_headers(headers)
    assert "Authorization" not in filtered
    assert "chatgpt-account-id" not in filtered
    assert filtered["Content-Type"] == "application/json"
    assert filtered["X-Request-Id"] == "req_1"


def test_build_upstream_headers_overrides_auth():
    inbound = {"X-Request-Id": "req_1"}
    headers = _build_upstream_headers(inbound, "token", "acc_2")
    assert headers["Authorization"] == "Bearer token"
    assert headers["chatgpt-account-id"] == "acc_2"
    assert headers["Accept"] == "text/event-stream"
    assert headers["Content-Type"] == "application/json"


def test_build_upstream_headers_accept_override():
    inbound = {}
    headers = _build_upstream_headers(inbound, "token", None, accept="application/json")
    assert headers["Accept"] == "application/json"


def test_parse_sse_event_reads_json_payload():
    payload = {"type": "response.completed", "response": {"id": "resp_1"}}
    line = f"data: {json.dumps(payload)}\n"
    event = parse_sse_event(line)
    assert event is not None
    assert event.type == "response.completed"
    assert event.response
    assert event.response.id == "resp_1"


def test_parse_sse_event_reads_multiline_payload():
    payload = {
        "type": "response.failed",
        "response": {"id": "resp_1", "status": "failed", "error": {"code": "upstream_error"}},
    }
    line = f"event: response.failed\ndata: {json.dumps(payload)}\n\n"
    event = parse_sse_event(line)
    assert event is not None
    assert event.type == "response.failed"
    assert event.response
    assert event.response.id == "resp_1"


def test_parse_sse_event_ignores_non_data_lines():
    assert parse_sse_event("event: ping\n") is None


class _StubContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def readany(self) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _StubResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.content = _StubContent(chunks)


@pytest.mark.asyncio
async def test_iter_sse_lines_handles_large_single_line():
    raw = b"data: " + (b"x" * 200_000) + b"\n"
    chunks = [raw[i : i + 8192] for i in range(0, len(raw), 8192)]
    resp = _StubResponse(chunks)

    result: list[bytes] = []
    async for line in _iter_sse_lines(resp, idle_timeout_seconds=1.0, max_line_bytes=512_000):
        result.append(line)

    assert result == [raw]


@pytest.mark.asyncio
async def test_iter_sse_lines_raises_when_line_exceeds_limit():
    raw = b"x" * 2048
    chunks = [raw[i : i + 256] for i in range(0, len(raw), 256)]
    resp = _StubResponse(chunks)

    with pytest.raises(StreamLineTooLongError):
        async for _ in _iter_sse_lines(resp, idle_timeout_seconds=1.0, max_line_bytes=1024):
            pass
