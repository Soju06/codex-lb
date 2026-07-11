"""Tests for ``app.modules.claude.oauth.service.ClaudeOAuthService``.

State-machine behavior, single-in-flight supersession, TTL expiry, CSRF
state validation, and the full Anthropic stub round-trip — every documented
``error_code`` is exercised at least once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.clients.anthropic.errors import ClaudeAuthError, ClaudeUpstreamError
from app.core.clients.anthropic.oauth import (
    ClaudeAuthorizationCodeResult,
)
from app.modules.claude.auth_manager import ClaudeAccountAlreadyExists
from app.modules.claude.oauth import service as service_module
from app.modules.claude.oauth.service import ClaudeOAuthService

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class _FakeOAuthClient:
    """Stub matching the surface that ``ClaudeOAuthService`` consumes."""

    next_result: ClaudeAuthorizationCodeResult | None = None
    next_error: Exception | None = None
    last_code: str | None = None
    last_code_verifier: str | None = None
    last_redirect_uri: str | None = None

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> ClaudeAuthorizationCodeResult:
        self.last_code = code
        self.last_code_verifier = code_verifier
        self.last_redirect_uri = redirect_uri
        if self.next_error is not None:
            raise self.next_error
        assert self.next_result is not None, "test must set either next_result or next_error"
        return self.next_result


@dataclass
class _FakeAuthManager:
    """Stub matching the surface ``ClaudeOAuthService`` uses."""

    next_account_id: str = "claude-uuid-X"
    next_error: Exception | None = None
    last_access_token: str | None = None
    last_refresh_token: str | None = None
    last_expires_in: int | None = None
    last_claims: Any = None

    async def add_claude_account_from_oauth(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        id_token_claims: Any,
    ) -> str:
        self.last_access_token = access_token
        self.last_refresh_token = refresh_token
        self.last_expires_in = expires_in
        self.last_claims = id_token_claims
        if self.next_error is not None:
            raise self.next_error
        return self.next_account_id


def _make_settings(*, ttl: int = 600) -> Any:
    return SimpleNamespace(
        claude_oauth_authorize_endpoint="https://auth.example.test/oauth/authorize",
        claude_oauth_client_id="client-id-xyz",
        claude_oauth_redirect_uri="https://r.example.test/cb",
        claude_oauth_scopes="user:profile user:inference",
        claude_oauth_flow_ttl_seconds=ttl,
    )


def _make_service(
    *,
    client: _FakeOAuthClient | None = None,
    auth_manager: _FakeAuthManager | None = None,
    ttl: int = 600,
    settings: Any | None = None,
) -> tuple[ClaudeOAuthService, _FakeOAuthClient, _FakeAuthManager]:
    settings = settings or _make_settings(ttl=ttl)
    client = client or _FakeOAuthClient()
    auth_manager = auth_manager or _FakeAuthManager()
    svc = ClaudeOAuthService(
        settings=settings,
        oauth_client=client,  # type: ignore[arg-type]
        auth_manager=auth_manager,  # type: ignore[arg-type]
    )
    return svc, client, auth_manager


def _b64u(payload: str) -> str:
    import base64

    return base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode("ascii")


def _id_token(payload: dict) -> str:
    import base64
    import json

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode("ascii")
    return f"{header}.{body}.fakesig"


# ---------------------------------------------------------------------------
# start_oauth
# ---------------------------------------------------------------------------


async def test_start_oauth_returns_authorization_url_with_pkce() -> None:
    svc, _, _ = _make_service()
    resp = await svc.start_oauth()

    assert resp.flow_id
    assert resp.state_token  # exposed to dashboard session
    assert resp.authorization_url.startswith("https://auth.example.test/oauth/authorize?")
    assert "code_challenge=" in resp.authorization_url
    assert "code_challenge_method=S256" in resp.authorization_url
    assert "state=" in resp.authorization_url
    assert resp.expires_in_seconds == 600
    assert resp.redirect_uri == "https://r.example.test/cb"
    assert resp.callback_instructions


async def test_start_oauth_supersedes_previous_pending_flow() -> None:
    svc, _, _ = _make_service()

    first = await svc.start_oauth()
    second = await svc.start_oauth()

    assert first.flow_id != second.flow_id

    status_first = await svc.oauth_status(first.flow_id)
    assert status_first.status == "error"
    assert status_first.error_code == "superseded"

    status_second = await svc.oauth_status(second.flow_id)
    assert status_second.status == "pending"


# ---------------------------------------------------------------------------
# oauth_status
# ---------------------------------------------------------------------------


async def test_oauth_status_unknown_flow_returns_not_found_code() -> None:
    svc, _, _ = _make_service()
    status = await svc.oauth_status("nonexistent")
    assert status.status == "error"
    assert status.error_code == "flow_not_found"


async def test_oauth_status_ttl_expired_marks_flow_error() -> None:
    svc, _, _ = _make_service(ttl=0)
    started = await svc.start_oauth()

    status = await svc.oauth_status(started.flow_id)
    assert status.status == "error"
    assert status.error_code == "flow_expired"


# ---------------------------------------------------------------------------
# complete_oauth
# ---------------------------------------------------------------------------


async def test_complete_oauth_happy_path_creates_account() -> None:
    svc, client, mgr = _make_service()
    started = await svc.start_oauth()
    client.next_result = ClaudeAuthorizationCodeResult(
        access_token="AT",
        refresh_token="RT",
        id_token=_id_token(
            {
                "account_id": "acct-1",
                "email": "u@example.test",
                "scope": "user:inference",
            }
        ),
        expires_in=3600,
        scope="user:inference",
    )

    resp = await svc.complete_oauth(
        flow_id=started.flow_id,
        code="AUTH_CODE",
        state=started.state_token,
    )

    assert resp.status == "success"
    assert resp.account.id == "claude-uuid-X"

    # PKCE verifier was passed to the client
    assert client.last_code == "AUTH_CODE"
    assert client.last_redirect_uri == "https://r.example.test/cb"
    assert client.last_code_verifier and len(client.last_code_verifier) >= 43

    # Typed claims flowed into the auth manager
    assert mgr.last_access_token == "AT"
    assert mgr.last_refresh_token == "RT"
    assert mgr.last_expires_in == 3600
    assert mgr.last_claims.claude_account_uuid == "acct-1"

    # Status flips to success
    status = await svc.oauth_status(started.flow_id)
    assert status.status == "success"
    assert status.account_id == "claude-uuid-X"


async def test_complete_oauth_state_mismatch_returns_error_code() -> None:
    svc, _, _ = _make_service()
    started = await svc.start_oauth()

    with pytest.raises(service_module.ClaudeOauthFlowError) as exc:
        await svc.complete_oauth(
            flow_id=started.flow_id,
            code="AUTH_CODE",
            state="DIFFERENT_STATE",
        )
    assert exc.value.code == "state_mismatch"


async def test_complete_oauth_flow_not_found() -> None:
    svc, _, _ = _make_service()
    with pytest.raises(service_module.ClaudeOauthFlowError) as exc:
        await svc.complete_oauth(flow_id="nope", code="c", state="s")
    assert exc.value.code == "flow_not_found"


async def test_complete_oauth_invalid_grant_propagates_as_upstream_error() -> None:
    svc, client, _ = _make_service()
    started = await svc.start_oauth()
    client.next_error = ClaudeAuthError("invalid_grant: bad")

    with pytest.raises(service_module.ClaudeOauthFlowError) as exc:
        await svc.complete_oauth(flow_id=started.flow_id, code="C", state=started.state_token)
    assert exc.value.code == "invalid_grant"


async def test_complete_oauth_anthropic_5xx_propagates_as_unreachable() -> None:
    svc, client, _ = _make_service()
    started = await svc.start_oauth()
    client.next_error = ClaudeUpstreamError("upstream 503")

    with pytest.raises(service_module.ClaudeOauthFlowError) as exc:
        await svc.complete_oauth(flow_id=started.flow_id, code="C", state=started.state_token)
    assert exc.value.code == "anthropic_unreachable"


async def test_complete_oauth_account_already_exists_returns_409_error() -> None:
    svc, client, mgr = _make_service()
    started = await svc.start_oauth()
    client.next_result = ClaudeAuthorizationCodeResult(
        access_token="AT",
        refresh_token="RT",
        id_token=_id_token({"account_id": "dup"}),
        expires_in=3600,
        scope="x",
    )
    mgr.next_error = ClaudeAccountAlreadyExists("dup")

    with pytest.raises(service_module.ClaudeOauthFlowError) as exc:
        await svc.complete_oauth(flow_id=started.flow_id, code="C", state=started.state_token)
    assert exc.value.code == "account_already_exists"


async def test_complete_oauth_id_token_missing_returns_error_code() -> None:
    svc, client, _ = _make_service()
    started = await svc.start_oauth()
    client.next_result = ClaudeAuthorizationCodeResult(
        access_token="AT",
        refresh_token="RT",
        id_token=None,
        expires_in=3600,
        scope="x",
    )

    with pytest.raises(service_module.ClaudeOauthFlowError) as exc:
        await svc.complete_oauth(flow_id=started.flow_id, code="C", state=started.state_token)
    assert exc.value.code == "id_token_missing"


async def test_complete_oauth_id_token_claims_incomplete_returns_error_code() -> None:
    svc, client, _ = _make_service()
    started = await svc.start_oauth()
    # id_token present but no claude_account_uuid-derivable claim
    client.next_result = ClaudeAuthorizationCodeResult(
        access_token="AT",
        refresh_token="RT",
        id_token=_id_token({"email": "only@example.test"}),
        expires_in=3600,
        scope="x",
    )

    with pytest.raises(service_module.ClaudeOauthFlowError) as exc:
        await svc.complete_oauth(flow_id=started.flow_id, code="C", state=started.state_token)
    assert exc.value.code == "id_token_claims_incomplete"


async def test_complete_oauth_flow_already_terminal_returns_not_pending() -> None:
    svc, client, _ = _make_service()
    started = await svc.start_oauth()
    client.next_result = ClaudeAuthorizationCodeResult(
        access_token="AT",
        refresh_token="RT",
        id_token=_id_token({"account_id": "x"}),
        expires_in=3600,
        scope="x",
    )
    await svc.complete_oauth(flow_id=started.flow_id, code="C", state=started.state_token)

    # Second callback against the same flow.
    with pytest.raises(service_module.ClaudeOauthFlowError) as exc:
        await svc.complete_oauth(flow_id=started.flow_id, code="C2", state=started.state_token)
    assert exc.value.code == "flow_not_pending"


async def test_complete_oauth_logs_no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    """Regression guard: no log line carries a real code/state/token value."""
    caplog.set_level(logging.DEBUG)
    svc, client, _ = _make_service()
    started = await svc.start_oauth()
    client.next_result = ClaudeAuthorizationCodeResult(
        access_token="SECRET_AT",
        refresh_token="SECRET_RT",
        id_token=_id_token({"account_id": "x"}),
        expires_in=3600,
        scope="x",
    )
    await svc.complete_oauth(flow_id=started.flow_id, code="SECRET_CODE", state=started.state_token)

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    for secret in ("SECRET_AT", "SECRET_RT", "SECRET_CODE"):
        assert secret not in joined, f"log leaked token material: {secret!r}"


# ---------------------------------------------------------------------------
# claude-oauth-link endpoints (see openspec/changes/fix-claude-oauth-link-endpoints)
# ---------------------------------------------------------------------------


def _production_like_settings() -> Any:
    """Settings shaped exactly like the production defaults.

    Mirrors the values documented in ``app/core/config/settings.py`` after the
    fix in ``openspec/changes/fix-claude-oauth-link-endpoints``. Used by the
    URL-shape tests below so a regression in the default values is caught
    here instead of at operator runtime.
    """

    return SimpleNamespace(
        claude_oauth_authorize_endpoint="https://claude.com/cai/oauth/authorize",
        claude_oauth_client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        claude_oauth_redirect_uri="https://platform.claude.com/oauth/code/callback",
        claude_oauth_scopes="user:profile user:inference",
        claude_oauth_flow_ttl_seconds=600,
    )


async def test_start_oauth_emits_claude_code_cli_url_with_code_true_flag() -> None:
    """Regression guard for openspec/.../fix-claude-oauth-link-endpoints.

    Anthropic accepts the authorization request only when the URL matches the
    Claude Code CLI pattern: ``https://claude.com/cai/oauth/authorize?code=true&...
    &redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&...``.
    A previous attempt used ``https://platform.claude.com/oauth/authorize`` plus
    ``redirect_uri=https://console.anthropic.com/oauth/code`` and was rejected
    with "Redirect URI ... is not supported by client." (operator report).
    """
    from urllib.parse import parse_qs, urlparse

    svc, _, _ = _make_service(settings=_production_like_settings())
    resp = await svc.start_oauth()

    parsed = urlparse(resp.authorization_url)
    # Authorize endpoint matches Claude Code CLI.
    assert parsed.scheme == "https"
    assert parsed.netloc == "claude.com"
    assert parsed.path == "/cai/oauth/authorize"
    qs = parse_qs(parsed.query)
    # ``code=true`` must be the first query parameter (matches Claude Code CLI).
    assert qs.get("code") == ["true"], (
        "code=true is required to select Anthropic's OOB code-display flow"
    )
    # The order of the query string matters because Anthropic's authorize
    # endpoint requires ``code=true`` first; assert the literal substring
    # appears right after the question mark.
    assert resp.authorization_url.startswith(
        "https://claude.com/cai/oauth/authorize?code=true&"
    )
    # Redirect URI is the one Anthropic has whitelisted for the public
    # Claude Code client_id.
    assert qs.get("redirect_uri") == [
        "https://platform.claude.com/oauth/code/callback"
    ]
    assert qs.get("client_id") == ["9d1c250a-e61b-44d9-88ed-5944d1962f5e"]
    assert qs.get("response_type") == ["code"]
    assert qs.get("code_challenge_method") == ["S256"]
    assert resp.redirect_uri == "https://platform.claude.com/oauth/code/callback"


def test_default_settings_pin_claude_code_compatible_endpoints() -> None:
    """Pin the production defaults so they cannot drift back to the rejected values.

    If either default regresses, operators will hit Anthropic's
    "Redirect URI ... is not supported by client" error at runtime.
    """
    from app.core.config.settings import Settings

    # Construct Settings without reading the host's environment so the defaults
    # are exercised in isolation (env vars still take precedence in production).
    settings = Settings(_env_file=None)
    assert settings.claude_oauth_authorize_endpoint == "https://claude.com/cai/oauth/authorize"
    assert settings.claude_oauth_redirect_uri == "https://platform.claude.com/oauth/code/callback"
