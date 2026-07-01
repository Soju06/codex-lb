"""Tests for ``app.core.clients.anthropic.chat.ClaudeChatClient``.

The chat client is a passthrough: it forwards the request body verbatim
(no translation, no copy) and surfaces upstream errors as typed exceptions
from ``app.core.clients.anthropic.errors``. Header values are pinned to the
verified contract in ``openspec/changes/add-claude-oauth-pool/notes.md`` §2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from app.core.clients.anthropic.chat import (
    ANTHROPIC_API_VERSION,
    ANTHROPIC_BETA_FLAGS,
    ClaudeChatClient,
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
    transport = _FakeTransport(
        _NonStreamingResponse(status=200, body=upstream_body, headers=upstream_headers)
    )
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    out_body, out_headers = await client.send_messages(
        access_token="AT", request_body=body_in
    )

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
    transport = _FakeTransport(
        _NonStreamingResponse(status=200, body={"id": "msg"}, headers={})
    )
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
    transport = _FakeTransport(
        _NonStreamingResponse(status=401, body={"error": "unauthorized"})
    )
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
    transport = _FakeTransport(
        _NonStreamingResponse(status=200, body={"id": "msg"}, headers={})
    )
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
    transport = _FakeTransport(
        _NonStreamingResponse(status=200, body={"id": "msg"}, headers={})
    )
    client = ClaudeChatClient(
        transport=transport,  # type: ignore[arg-type]
        settings=settings,
        base_url="https://api.anthropic.com",
    )

    await client.send_messages(access_token="AT", request_body=body_in)

    assert transport.requests[0].body is body_in