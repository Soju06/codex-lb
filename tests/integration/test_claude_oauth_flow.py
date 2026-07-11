"""Integration tests for the Claude OAuth link flow (happy path end-to-end).

The full FastAPI app is stood up via the ``async_client`` fixture in
:mod:`tests.conftest`, which also runs the real lifespan. The Anthropic
OAuth transport is stubbed at the dependency seam exposed by
:func:`app.modules.claude.oauth.api.get_claude_oauth_service`; the rest
of the wiring (the in-memory SQLite session, the
:class:`ClaudeAuthManager`, the :class:`SqlClaudeAccountRepository`)
is the real production code.

This black-box test:

1. Starts a flow.
2. Polls the flow status mid-flight.
3. Completes the flow with the pasted authorization code.
4. Asserts the new account is visible via ``GET /api/claude/accounts`` and
   that no plaintext token material ever leaves the boundary (token-leak
   regression guard).
5. Asserts the Anthropic transport was hit with the PKCE verifier — not
   the raw pasted ``code`` — in the JSON body.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Mapping, cast

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _StubResponse:
    """Mimics the aiohttp ``ClientResponse`` shape consumed by
    :class:`ClaudeOAuthClient`.

    ``status`` and ``json()`` are the only fields read by the production
    client (``_extract_json`` / ``_extract_raw_body``).
    """

    status: int
    body: dict[str, Any]
    raw_body: bytes | None = None


class _StubOAuthTransport:
    """Drop-in stub satisfying :class:`ClaudeOAuthTransport`.

    Captures the JSON body of every ``post`` so tests can assert on what
    the production code sent to Anthropic (e.g. that the PKCE verifier
    is forwarded rather than the raw authorization code).
    """

    def __init__(self, response: _StubResponse | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, *, json: Mapping[str, Any], headers: Mapping[str, str]) -> _StubResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _encode_id_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


# ---------------------------------------------------------------------------
# Fixture: install a stubbed OAuth client + real repo/auth-manager wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def stubbed_oauth_transport(app_instance):
    """Replace the OAuth transport with a stub.

    The default ``get_claude_oauth_service`` builds a :class:`ClaudeOAuthService`
    with the lifespan-provided ``ClaudeOAuthClient``. We override the
    dependency to keep the same ``SqlClaudeAccountRepository`` /
    ``ClaudeAuthManager`` pairing (driven by the request-scoped
    ``AsyncSession``) but inject our stubbed transport into the client so
    the Anthropic exchange call is fully observable.

    The ``ClaudeOAuthService`` flow store is process-local state, so we
    share one ``_FlowStore`` instance across every request in the test.
    """
    from app.core.clients.anthropic.oauth import ClaudeOAuthClient
    from app.core.config.settings import get_settings
    from app.db.session import get_session
    from app.modules.claude.auth_manager import ClaudeAuthManager
    from app.modules.claude.oauth import api as oauth_api_module
    from app.modules.claude.oauth.service import (
        ClaudeOAuthService,
        _FlowStore,
    )
    from app.modules.claude.repository import SqlClaudeAccountRepository

    transport = _StubOAuthTransport(
        _StubResponse(
            status=200,
            body={
                "access_token": "sk-ant-oat01-STUB-AT",
                "refresh_token": "sk-ant-ort01-STUB-RT",
                "id_token": _encode_id_token(
                    {
                        "account_id": "acct-integration",
                        "email": "integration@example.test",
                        "scope": "user:profile user:inference",
                    }
                ),
                "expires_in": 3600,
                "scope": "user:profile user:inference",
            },
        )
    )
    settings = get_settings()
    flow_store = _FlowStore()

    async def _override_service(request: Any = None):
        # Open a short-lived session that mirrors what
        # ``get_claude_oauth_service`` does in production. The
        # ``_claude_admin_context`` admin route explicitly commits on the
        # success path — we do the same here so the new account row is
        # visible to subsequent admin GETs in the same test.
        session_gen = get_session()
        session = await session_gen.__anext__()
        committed = False
        try:
            repo = SqlClaudeAccountRepository(session)
            manager = ClaudeAuthManager(repo=repo)
            oauth_client = ClaudeOAuthClient(transport=transport, settings=settings)
            try:
                yield ClaudeOAuthService(
                    settings=settings,
                    oauth_client=oauth_client,
                    auth_manager=manager,
                    accounts_repo=repo,
                    flow_store=flow_store,
                )
                await session.commit()
                committed = True
            except BaseException:
                if session.in_transaction():
                    await session.rollback()
                raise
        finally:
            if not committed and session.in_transaction():
                try:
                    await session.rollback()
                except Exception:
                    pass
            try:
                await cast(AsyncGenerator[Any, None], session_gen).aclose()
            except Exception:
                pass

    app_instance.dependency_overrides[oauth_api_module.get_claude_oauth_service] = _override_service
    try:
        yield transport
    finally:
        app_instance.dependency_overrides.pop(oauth_api_module.get_claude_oauth_service, None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_link_flow_creates_account_and_does_not_leak_tokens(async_client, stubbed_oauth_transport):
    # 1) Start the flow.
    start = await async_client.post("/api/claude/oauth/start", json={})
    assert start.status_code == 200
    start_payload = start.json()
    assert start_payload["flowId"]
    assert start_payload["stateToken"]
    # The authorize endpoint + the ``code=true`` first-param flag MUST match
    # Anthropic's whitelist for the public Claude Code OAuth client. Without
    # these, Anthropic rejects the request with "Redirect URI ... is not
    # supported by client." See openspec/.../fix-claude-oauth-link-endpoints.
    assert start_payload["authorizationUrl"].startswith(
        "https://claude.com/cai/oauth/authorize?code=true&"
    )
    assert "redirect_uri=" in start_payload["authorizationUrl"]
    assert (
        "platform.claude.com%2Foauth%2Fcode%2Fcallback" in start_payload["authorizationUrl"]
        or "platform.claude.com/oauth/code/callback" in start_payload["authorizationUrl"]
    )
    assert start_payload["expiresInSeconds"] > 0
    assert start_payload["callbackInstructions"]
    assert start_payload["redirectUri"]
    # The state token MUST round-trip in the URL (CSRF binding).
    assert start_payload["stateToken"] in start_payload["authorizationUrl"]

    flow_id = start_payload["flowId"]
    state_token = start_payload["stateToken"]

    # 2) Status is pending mid-flight.
    status = await async_client.get("/api/claude/oauth/status", params={"flowId": flow_id})
    assert status.status_code == 200
    status_payload = status.json()
    assert status_payload["status"] == "pending"
    assert status_payload["flowId"] == flow_id

    # 3) Complete the flow.
    callback = await async_client.post(
        "/api/claude/oauth/callback",
        json={"flowId": flow_id, "code": "AUTH_CODE", "state": state_token},
    )
    assert callback.status_code == 200, callback.text
    callback_payload = callback.json()
    assert callback_payload["status"] == "success"
    account = callback_payload["account"]
    assert account["id"]
    assert account["claudeAccountUuid"] == "acct-integration"
    assert account["isActive"] is True

    # 4) The new account MUST appear in the existing admin listing.
    listing = await async_client.get("/api/claude/accounts")
    assert listing.status_code == 200
    listing_payload = listing.json()
    assert any(row["claudeAccountUuid"] == "acct-integration" for row in listing_payload), (
        f"new account missing from listing: {listing_payload!r}"
    )

    # 4a) Token-leak regression guard: the literal paste-time values MUST
    # NOT appear anywhere in the admin responses.
    for raw_response in (callback.text, listing.text):
        assert "AT" not in raw_response, "plaintext access-token material leaked"
        assert "RT" not in raw_response, "plaintext refresh-token material leaked"
        assert "AUTH_CODE" not in raw_response, "plaintext pasted code leaked"

    # 5) The Anthropic transport MUST have been hit with the PKCE
    # verifier — not the raw pasted code — in the JSON body.
    assert len(stubbed_oauth_transport.calls) == 1
    sent_body = stubbed_oauth_transport.calls[0]["json"]
    assert sent_body["grant_type"] == "authorization_code"
    assert sent_body["code"] == "AUTH_CODE"  # pasted code IS forwarded
    assert sent_body["code_verifier"]  # PKCE verifier was also forwarded
    # The PKCE verifier is NOT the same string as the pasted code.
    assert sent_body["code_verifier"] != "AUTH_CODE"
    # Token-leak guard: the stub's response body (which contains the
    # plaintext AT/RT) must never be reflected through the public surface.
    sent_body_text = json.dumps(sent_body)
    assert "STUB-AT" not in sent_body_text
    assert "STUB-RT" not in sent_body_text


@pytest.mark.asyncio
async def test_oauth_flow_status_unknown_id_returns_error_code(async_client):
    """The contract says an unknown flow id returns 200 with
    ``status: "error", error_code: "flow_not_found"`` — NOT a 404."""
    status = await async_client.get("/api/claude/oauth/status", params={"flowId": "does-not-exist"})
    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "error"
    assert payload["errorCode"] == "flow_not_found"
    assert payload["flowId"] == "does-not-exist"
