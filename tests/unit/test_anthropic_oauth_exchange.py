"""Tests for ``ClaudeOAuthClient.exchange_authorization_code``.

Mirror tests for ``refresh``: same ``_Response`` / ``_Transport`` shape, same
status-code / error-class mapping. We do not duplicate the full file; this one
focuses on the new flow and on the differences the new method introduces
(tolerated-missing id_token, code+verifier+redirect_uri request shape).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from app.core.clients.anthropic.errors import ClaudeAPIError, ClaudeAuthError, ClaudeUpstreamError
from app.core.clients.anthropic.oauth import (
    ClaudeAuthorizationCodeResult,
    ClaudeOAuthClient,
)

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self.body = body

    async def json(self) -> dict:
        return self.body


class _Transport:
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


async def test_exchange_authorization_code_returns_full_result(settings: SimpleNamespace) -> None:
    resp = _Response(
        status=200,
        body={
            "access_token": "AT",
            "refresh_token": "RT",
            "id_token": "JWT.PAYLOAD.SIG",
            "expires_in": 3600,
            "scope": "user:profile user:inference",
            "token_type": "Bearer",
        },
    )
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)

    out = await client.exchange_authorization_code(
        code="AUTH_CODE", code_verifier="VERIFIER", redirect_uri="https://redirect.example/cb"
    )

    assert isinstance(out, ClaudeAuthorizationCodeResult)
    assert out.access_token == "AT"
    assert out.refresh_token == "RT"
    assert out.id_token == "JWT.PAYLOAD.SIG"
    assert out.expires_in == 3600
    assert out.scope == "user:profile user:inference"

    # Request body shape per design.md
    assert t.last_json == {
        "grant_type": "authorization_code",
        "code": "AUTH_CODE",
        "code_verifier": "VERIFIER",
        "client_id": client._client_id,
        "redirect_uri": "https://redirect.example/cb",
    }
    # URL is the configured endpoint
    assert t.last_url == "https://auth.example.test/oauth/token"


async def test_exchange_authorization_code_tolerates_missing_id_token(settings: SimpleNamespace) -> None:
    resp = _Response(
        status=200,
        body={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "scope": "x"},
    )
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)

    out = await client.exchange_authorization_code(
        code="AUTH_CODE", code_verifier="VERIFIER", redirect_uri="https://redirect.example/cb"
    )

    assert out.id_token is None
    assert out.access_token == "AT"


async def test_exchange_authorization_code_invalid_grant_raises_auth_error(settings: SimpleNamespace) -> None:
    resp = _Response(status=400, body={"error": "invalid_grant"})
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)

    with pytest.raises(ClaudeAuthError):
        await client.exchange_authorization_code(code="BAD", code_verifier="V", redirect_uri="https://r.example/cb")


async def test_exchange_authorization_code_5xx_raises_upstream_error(settings: SimpleNamespace) -> None:
    resp = _Response(status=503, body={"error": "temporarily_unavailable"})
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)

    with pytest.raises(ClaudeUpstreamError):
        await client.exchange_authorization_code(code="C", code_verifier="V", redirect_uri="https://r.example/cb")


async def test_exchange_authorization_code_malformed_body_raises_api_error(settings: SimpleNamespace) -> None:
    resp = _Response(status=200, body={"access_token": "AT"})  # missing refresh_token + expires_in
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)

    with pytest.raises(ClaudeAPIError):
        await client.exchange_authorization_code(code="C", code_verifier="V", redirect_uri="https://r.example/cb")
