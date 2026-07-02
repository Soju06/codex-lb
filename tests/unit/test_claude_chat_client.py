"""Tests for ``app.core.clients.anthropic.chat.ClaudeChatClient``.

The chat client is a passthrough: it forwards the request body verbatim
(no translation, no copy) and surfaces upstream errors as typed exceptions
from ``app.core.clients.anthropic.errors``. Header values are pinned to the
verified contract in ``openspec/changes/add-claude-oauth-pool/notes.md`` §2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, AsyncIterator, Mapping

import pytest

from app.core.clients.anthropic.chat import (
    ANTHROPIC_API_VERSION,
    ANTHROPIC_BETA_FLAGS,
    ClaudeChatClient,
    StreamChunk,
)
from app.core.clients.anthropic.errors import ClaudeAPIError, ClaudeAuthError, ClaudeRateLimited

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Transport fakes
# ---------------------------------------------------------------------------


@dataclass
class _CapturedRequest:
    url: str
    body: Mapping[str, Any]
    headers: Mapping[str, str]


@dataclass
class _NonStreamingResponse:
    """Stub response matching the surface the chat client depends on."""

    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)


class _FakeTransport:
    """In-memory ClaudeChatTransport stub capturing each non-stream request."""

    def __init__(self, response: _NonStreamingResponse) -> None:
        self.response = response
        self.requests: list[_CapturedRequest] = []

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> _NonStreamingResponse:
        self.requests.append(_CapturedRequest(url=url, body=json, headers=dict(headers)))
        return self.response

    async def post_stream(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Any:
        raise NotImplementedError


@dataclass
class _StreamingResponse:
    """Stub for a streaming response.

    Mirrors the surface ``ClaudeChatClient.stream_messages`` needs:
    - ``status`` for the initial POST status code
    - ``headers`` for the ratelimit headers to forward as a 'headers' chunk
    - ``iter_chunks()`` async iterator yielding raw SSE byte chunks
    - ``close()`` to release the underlying aiohttp response
    """

    status: int
    chunks: list[bytes]
    headers: dict[str, str] = field(default_factory=dict)
    closed: bool = False
    close_calls: int = 0

    async def iter_chunks(self) -> AsyncIterator[bytes]:
        try:
            for chunk in self.chunks:
                yield chunk
        finally:
            await self.close()

    async def close(self) -> None:
        self.closed = True
        self.close_calls += 1


class _FakeStreamingTransport:
    """Transport stub returning a streaming response."""

    def __init__(self, response: _StreamingResponse) -> None:
        self.response = response
        self.requests: list[_CapturedRequest] = []

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> _NonStreamingResponse:
        raise NotImplementedError

    async def post_stream(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> _StreamingResponse:
        self.requests.append(_CapturedRequest(url=url, body=json, headers=dict(headers)))
        return self.response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings() -> SimpleNamespace:
    return SimpleNamespace(
        claude_messages_path="/v1/messages",
        claude_oauth_extra_headers={"X-Client": "codex-lb"},
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_module_constants_are_pinned_to_verified_contract() -> None:
    # Values are pinned to openspec/changes/add-claude-oauth-pool/notes.md §2
    # and MUST NOT drift without an updated verification pass.
    assert ANTHROPIC_API_VERSION == "2023-06-01"
    assert ANTHROPIC_BETA_FLAGS == "oauth-2025-04-20,claude-code-20250219"


# ---------------------------------------------------------------------------
# send_messages — non-streaming passthrough
# ---------------------------------------------------------------------------


async def test_send_messages_returns_upstream_body_and_headers_verbatim(
    settings: SimpleNamespace,
) -> None:
    body_in: dict[str, Any] = {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
        "max_tokens": 1024,
    }
    upstream_body: dict[str, Any] = {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "usage": {"input_tokens": 5, "output_tokens": 7},
        "model": "claude-opus-4-8",
        "stop_reason": "end_turn",
    }
    upstream_headers = {
        "content-type": "application/json",
        "anthropic-ratelimit-requests-remaining": "42",
        "anthropic-ratelimit-status": "allowed",
        "request-id": "req_abc",
    }
    transport = _FakeTransport(_NonStreamingResponse(status=200, body=upstream_body, headers=upstream_headers))
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    out_body, out_headers = await client.send_messages(access_token="AT", request_body=body_in)

    assert out_body == upstream_body
    assert out_headers["anthropic-ratelimit-status"] == "allowed"
    assert out_headers["anthropic-ratelimit-requests-remaining"] == "42"

    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req.url == "https://api.anthropic.com/v1/messages"
    # Passthrough invariant: the body sent upstream is the SAME object the
    # caller passed (no copy, no transformation).
    assert req.body is body_in


async def test_send_messages_authorization_header_is_bearer_token(
    settings: SimpleNamespace,
) -> None:
    transport = _FakeTransport(_NonStreamingResponse(status=200, body={"id": "msg"}, headers={}))
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    await client.send_messages(access_token="sk-ant-oat01-FAKE", request_body={"x": 1})

    sent = transport.requests[0].headers
    assert sent["Authorization"] == "Bearer sk-ant-oat01-FAKE"
    # x-api-key MUST NOT be sent when using OAuth.
    assert "x-api-key" not in sent
    # Pinned headers per notes.md §2.
    assert sent["anthropic-version"] == "2023-06-01"
    assert sent["anthropic-beta"] == "oauth-2025-04-20,claude-code-20250219"
    assert sent["Content-Type"] == "application/json"
    # Extra headers from settings merged in.
    assert sent["X-Client"] == "codex-lb"


async def test_send_messages_401_raises_claude_auth_error(
    settings: SimpleNamespace,
) -> None:
    transport = _FakeTransport(_NonStreamingResponse(status=401, body={"error": "unauthorized"}))
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    with pytest.raises(ClaudeAuthError):
        await client.send_messages(access_token="AT", request_body={"x": 1})


async def test_send_messages_429_raises_claude_rate_limited(
    settings: SimpleNamespace,
) -> None:
    transport = _FakeTransport(
        _NonStreamingResponse(
            status=429,
            body={"error": "rate_limit_exceeded"},
            headers={"anthropic-ratelimit-status": "rejected"},
        )
    )
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    with pytest.raises(ClaudeRateLimited) as excinfo:
        await client.send_messages(access_token="AT", request_body={"x": 1})

    assert isinstance(excinfo.value, ClaudeAPIError)


async def test_send_messages_500_raises_claude_api_error(
    settings: SimpleNamespace,
) -> None:
    transport = _FakeTransport(_NonStreamingResponse(status=500, body={"error": "boom"}))
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    with pytest.raises(ClaudeAPIError):
        await client.send_messages(access_token="AT", request_body={"x": 1})


async def test_send_messages_accepts_extra_headers_override(
    settings: SimpleNamespace,
) -> None:
    transport = _FakeTransport(_NonStreamingResponse(status=200, body={"id": "msg"}, headers={}))
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
        extra_headers={"User-Agent": "codex-lb/1.0"},
    )

    await client.send_messages(access_token="AT", request_body={"x": 1})

    sent = transport.requests[0].headers
    assert sent["User-Agent"] == "codex-lb/1.0"


async def test_send_messages_passthrough_invariant_request_body_is_identity(
    settings: SimpleNamespace,
) -> None:
    body_in: dict[str, Any] = {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "hi"}],
    }
    transport = _FakeTransport(_NonStreamingResponse(status=200, body={"id": "msg"}, headers={}))
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    await client.send_messages(access_token="AT", request_body=body_in)

    assert transport.requests[0].body is body_in


# ---------------------------------------------------------------------------
# stream_messages — SSE passthrough
# ---------------------------------------------------------------------------


async def test_stream_messages_yields_sse_bytes_verbatim(
    settings: SimpleNamespace,
) -> None:
    sse_chunks = [
        b'event: message_start\r\ndata: {"type":"message_start"}\r\n\r\n',
        b"event: content_block_delta\r\n"
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}\r\n\r\n',
        b'event: message_delta\r\ndata: {"type":"message_delta","usage":{"input_tokens":3,"output_tokens":5}}\r\n\r\n',
        b'event: message_stop\r\ndata: {"type":"message_stop"}\r\n\r\n',
    ]
    response = _StreamingResponse(status=200, chunks=sse_chunks, headers={})
    transport = _FakeStreamingTransport(response)
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    chunks: list[StreamChunk] = []
    async for chunk in await client.stream_messages(access_token="AT", request_body={"stream": True}):
        chunks.append(chunk)

    sse_bytes = b"".join(c.data for c in chunks if c.kind == "sse")
    assert sse_bytes == b"".join(sse_chunks)

    usage_chunks = [c for c in chunks if c.kind == "usage"]
    assert len(usage_chunks) == 1
    assert usage_chunks[0].data == {"input_tokens": 3, "output_tokens": 5}

    assert len(transport.requests) == 1
    sent = transport.requests[0].headers
    assert sent["Authorization"] == "Bearer AT"
    assert sent["Accept"] == "text/event-stream"
    assert sent["anthropic-version"] == "2023-06-01"
    assert sent["anthropic-beta"] == "oauth-2025-04-20,claude-code-20250219"
    assert "x-api-key" not in sent


async def test_stream_messages_closes_response_on_completion(
    settings: SimpleNamespace,
) -> None:
    sse_chunks = [
        b'event: message_start\r\ndata: {"type":"message_start"}\r\n\r\n',
        b'event: message_delta\r\ndata: {"type":"message_delta","usage":{"input_tokens":1,"output_tokens":2}}\r\n\r\n',
        b'event: message_stop\r\ndata: {"type":"message_stop"}\r\n\r\n',
    ]
    response = _StreamingResponse(status=200, chunks=sse_chunks, headers={})
    transport = _FakeStreamingTransport(response)
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    chunks: list[StreamChunk] = []
    async for chunk in await client.stream_messages(access_token="AT", request_body={"stream": True}):
        chunks.append(chunk)

    assert response.closed is True


async def test_stream_messages_401_raises_claude_auth_error(
    settings: SimpleNamespace,
) -> None:
    response = _StreamingResponse(status=401, chunks=[], headers={})
    transport = _FakeStreamingTransport(response)
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    with pytest.raises(ClaudeAuthError):
        async for _ in await client.stream_messages(access_token="AT", request_body={"stream": True}):
            pass


async def test_stream_messages_emits_headers_chunk_when_present(
    settings: SimpleNamespace,
) -> None:
    sse_chunks = [
        b'event: message_start\r\ndata: {"type":"message_start"}\r\n\r\n',
        b'event: message_delta\r\ndata: {"type":"message_delta","usage":{"input_tokens":1,"output_tokens":2}}\r\n\r\n',
        b'event: message_stop\r\ndata: {"type":"message_stop"}\r\n\r\n',
    ]
    response_headers = {
        "anthropic-ratelimit-requests-remaining": "17",
        "anthropic-ratelimit-status": "allowed_warning",
    }
    response = _StreamingResponse(status=200, chunks=sse_chunks, headers=response_headers)
    transport = _FakeStreamingTransport(response)
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    chunks: list[StreamChunk] = []
    async for chunk in await client.stream_messages(access_token="AT", request_body={"stream": True}):
        chunks.append(chunk)

    headers_chunks = [c for c in chunks if c.kind == "headers"]
    assert len(headers_chunks) == 1
    hdrs = headers_chunks[0].data
    assert hdrs["anthropic-ratelimit-requests-remaining"] == "17"
    assert hdrs["anthropic-ratelimit-status"] == "allowed_warning"


async def test_stream_messages_no_message_delta_yields_no_usage_chunk(
    settings: SimpleNamespace,
) -> None:
    sse_chunks = [
        b'event: message_start\r\ndata: {"type":"message_start"}\r\n\r\n',
        b'event: message_stop\r\ndata: {"type":"message_stop"}\r\n\r\n',
    ]
    response = _StreamingResponse(status=200, chunks=sse_chunks, headers={})
    transport = _FakeStreamingTransport(response)
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    chunks: list[StreamChunk] = []
    async for chunk in await client.stream_messages(access_token="AT", request_body={"stream": True}):
        chunks.append(chunk)

    assert [c for c in chunks if c.kind == "usage"] == []


async def test_stream_messages_closes_response_on_consumer_break(
    settings: SimpleNamespace,
) -> None:
    # When the consumer breaks out of the iterator early, the iterator's
    # ``aclose`` is not auto-invoked by ``async for`` in Python. Production
    # callers MUST drain the iterator OR await ``aclose`` explicitly. This
    # test pins the documented contract: calling ``aclose`` on the iterator
    # releases the underlying aiohttp response.
    sse_chunks = [
        b'event: message_start\r\ndata: {"type":"message_start"}\r\n\r\n',
        b"event: content_block_delta\r\n"
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"x"}}\r\n\r\n',
        b'event: message_delta\r\ndata: {"type":"message_delta","usage":{"input_tokens":1,"output_tokens":1}}\r\n\r\n',
        b'event: message_stop\r\ndata: {"type":"message_stop"}\r\n\r\n',
    ]
    response = _StreamingResponse(status=200, chunks=sse_chunks, headers={})
    transport = _FakeStreamingTransport(response)
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    iterator = await client.stream_messages(access_token="AT", request_body={"stream": True})
    seen = 0
    async for chunk in iterator:
        seen += 1
        if seen >= 2:
            break
    await iterator.aclose()

    assert response.closed is True
