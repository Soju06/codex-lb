"""Regression guard for the existing manual-paste ``POST /api/claude/accounts``.

The Claude OAuth link flow adds three new endpoints under
``/api/claude/oauth/*`` but MUST NOT change the existing
``POST /api/claude/accounts`` shape, status code, or token-leak
guarantees. This test asserts the manual-paste path still:

- Returns ``201 Created``.
- Surfaces the ``claudeAccountUuid`` it was given.
- Does NOT reflect the literal ``"AT_MANUAL"`` / ``"RT_MANUAL"``
  tokens in the raw response body.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from app.core.clients.anthropic.chat import StreamChunk

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Stub Claude proxy service (mirrors tests/integration/test_claude_api.py)
# ---------------------------------------------------------------------------


class _StubProxyService:
    """Minimal ClaudeProxyService stub.

    The admin endpoints share a route tree with the proxy routes; the
    lifespan does NOT install a real proxy service in tests. The admin
    endpoints only depend on a request-scoped session so this stub is
    effectively unused, but FastAPI still needs ``app.state.claude_proxy_service``
    to resolve ``_get_service`` if anything in the route tree probes it.
    """

    async def stream_or_complete_messages(
        self,
        *,
        request_body: dict[str, Any],
        api_key: Any,
        request_id: str,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        return ({"id": "msg_passed", "model": request_body.get("model")}, {})

    async def stream_messages(
        self,
        *,
        request_body: dict[str, Any],
        api_key: Any,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        async def _gen() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(kind="headers", data={})
            yield StreamChunk(kind="sse", data=b"")

        return _gen()


@pytest.fixture()
def stubbed_claude_service(app_instance):
    stub = _StubProxyService()
    stub.select_account = lambda *_a, **_kw: None  # type: ignore[attr-defined]
    stub.record_error = lambda *_a, **_kw: None  # type: ignore[attr-defined]
    stub.get_access_token = lambda _account: "AT"  # type: ignore[attr-defined]
    stub.rotate_claude_access_token = lambda *_a, **_kw: None  # type: ignore[attr-defined]
    app_instance.state.claude_proxy_service = stub  # type: ignore[attr-defined]
    return stub


# ---------------------------------------------------------------------------
# Regression guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_paste_post_claude_accounts_unchanged(async_client, stubbed_claude_service):
    payload = {
        "claudeAccountUuid": "manual-uuid-regression",
        "accessToken": "sk-ant-oat01-AT_MANUAL",
        "refreshToken": "sk-ant-ort01-RT_MANUAL",
        "expiresInSeconds": 3600,
        "userEmail": "manual@example.test",
    }

    response = await async_client.post("/api/claude/accounts", json=payload)
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["claudeAccountUuid"] == "manual-uuid-regression"
    assert body["userEmail"] == "manual@example.test"
    assert body["isActive"] is True

    # Token-leak regression guard: the literal paste-time values MUST NOT
    # appear anywhere in the raw response. This is the same invariant as
    # test_claude_api.py::test_post_claude_accounts_returns_201_and_does_not_leak_tokens.
    raw = response.text
    assert "AT_MANUAL" not in raw, "plaintext access-token material leaked"
    assert "RT_MANUAL" not in raw, "plaintext refresh-token material leaked"

    # Listing MUST also not reflect the tokens.
    listing = await async_client.get("/api/claude/accounts")
    assert listing.status_code == 200
    listed_bodies = listing.json()
    listed_raw = json.dumps(listed_bodies)
    assert "AT_MANUAL" not in listed_raw
    assert "RT_MANUAL" not in listed_raw
