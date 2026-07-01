from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from app.core.clients.anthropic.errors import ClaudeAuthError, ClaudeUpstreamError
from app.core.clients.anthropic.oauth import (
    ANTHROPIC_OAUTH_CLIENT_ID,
    ANTHROPIC_OAUTH_DEFAULT_TOKEN_ENDPOINT,
    ClaudeOAuthClient,
    ClaudeRefreshResult,
)

pytestmark = pytest.mark.unit


class _Response:
    """Stub response matching the surface our client depends on.

    Holds a status, JSON body, and the captured request tuple for assertions.
    """

    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self.body = body

    async def json(self) -> dict:
        return self.body


class _Transport:
    """Stub transport implementing the ClaudeOAuthTransport protocol."""

    def __init__(self, response: _Response) -> None:
        self.response = response
        self.last_url: str | None = None
        self.last_json: Mapping[str, Any] | None = None
        self.last_headers: Mapping[str, str] | None = None

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> _Response:
        self.last_url = url
        self.last_json = json
        self.last_headers = headers
        return self.response


@pytest.fixture()
def settings() -> SimpleNamespace:
    return SimpleNamespace(
        claude_oauth_token_endpoint="https://auth.example.test/oauth/token",
        claude_oauth_extra_headers={"X-Client": "codex-lb"},
    )


async def test_refresh_returns_access_token_and_new_refresh(settings: SimpleNamespace) -> None:
    resp = _Response(
        status=200,
        body={"access_token": "AT", "refresh_token": "NEW_RT", "expires_in": 3600},
    )
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)

    out = await client.refresh("OLD_RT")

    assert isinstance(out, ClaudeRefreshResult)
    assert out.access_token == "AT"
    assert out.refresh_token == "NEW_RT"  # rotated
    assert out.expires_in == 3600

    # Request shape per notes.md §1
    assert t.last_url == "https://auth.example.test/oauth/token"
    assert t.last_json == {
        "grant_type": "refresh_token",
        "refresh_token": "OLD_RT",
        "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
    }
    assert t.last_headers is not None
    assert t.last_headers["Content-Type"] == "application/json"
    assert t.last_headers["Accept"] == "application/json"
    assert t.last_headers["X-Client"] == "codex-lb"  # extra headers passed through


async def test_refresh_returns_none_refresh_when_not_rotated(settings: SimpleNamespace) -> None:
    # Defensive: server always rotates per notes.md §3, but the client must
    # not crash if the response ever omits a refresh_token.
    resp = _Response(status=200, body={"access_token": "AT", "expires_in": 3600})
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)

    out = await client.refresh("RT")

    assert out.access_token == "AT"
    assert out.refresh_token is None  # server did not return a new RT
    assert out.expires_in == 3600


async def test_refresh_invalid_grant_raises_auth_error(settings: SimpleNamespace) -> None:
    resp = _Response(status=400, body={"error": "invalid_grant"})
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)

    with pytest.raises(ClaudeAuthError):
        await client.refresh("EXPIRED_RT")


async def test_refresh_server_error_raises_upstream_error(settings: SimpleNamespace) -> None:
    resp = _Response(status=500, body={"error": "boom"})
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)

    with pytest.raises(ClaudeUpstreamError):
        await client.refresh("RT")


def test_module_constants_are_pinned() -> None:
    """Constants are exported for reuse by Phase 6 (auth_manager)."""
    assert ANTHROPIC_OAUTH_DEFAULT_TOKEN_ENDPOINT == "https://platform.claude.com/v1/oauth/token"
    assert ANTHROPIC_OAUTH_CLIENT_ID == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"