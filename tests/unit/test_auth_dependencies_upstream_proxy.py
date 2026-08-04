from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.auth import dependencies as auth_dependencies
from app.core.auth.codex_oauth_identity import VerifiedCodexOAuthIdentity
from app.core.exceptions import ProxyAuthError
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from app.core.usage.models import UsagePayload
from app.modules.api_keys.service import ApiKeyData

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_validate_codex_usage_identity_projects_verified_identity_to_request_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(
        headers={"Authorization": "Bearer oauth-token", "chatgpt-account-id": "workspace_account"},
        state=SimpleNamespace(),
    )
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    usage_payload = UsagePayload(workspace_id="workspace_1", workspace_label="Team")
    identity = VerifiedCodexOAuthIdentity(
        principal_id="acc_2",
        caller_account_id="acc_2",
        chatgpt_account_id="workspace_account",
        usage_payload=usage_payload,
        route=route,
    )
    calls: list[tuple[str | None, str | None]] = []

    async def resolve_identity(
        authorization: str | None,
        chatgpt_account_id: str | None,
    ) -> VerifiedCodexOAuthIdentity:
        calls.append((authorization, chatgpt_account_id))
        return identity

    monkeypatch.setattr(auth_dependencies, "resolve_verified_codex_oauth_identity", resolve_identity)

    result = await auth_dependencies.validate_codex_usage_identity(cast(Any, request))

    assert result is None
    assert calls == [("Bearer oauth-token", "workspace_account")]
    assert request.state.codex_usage_identity_access_token == "oauth-token"
    assert request.state.codex_usage_identity_chatgpt_account_id == "workspace_account"
    assert request.state.codex_usage_identity_account_id == "acc_2"
    assert request.state.codex_usage_identity_route is route
    assert request.state.codex_usage_identity_payload is usage_payload


@pytest.mark.asyncio
async def test_validate_codex_usage_identity_rejects_external_oauth_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(
        headers={"Authorization": "Bearer oauth-token", "chatgpt-account-id": "workspace_account"},
        state=SimpleNamespace(),
    )
    identity = VerifiedCodexOAuthIdentity(
        principal_id="principal:user-external",
        caller_account_id=None,
        chatgpt_account_id="workspace_account",
        usage_payload=UsagePayload(workspace_id="workspace_1"),
        route=None,
    )

    async def resolve_identity(
        authorization: str | None,
        chatgpt_account_id: str | None,
    ) -> VerifiedCodexOAuthIdentity:
        return identity

    monkeypatch.setattr(auth_dependencies, "resolve_verified_codex_oauth_identity", resolve_identity)

    with pytest.raises(ProxyAuthError, match="eligible imported account"):
        await auth_dependencies.validate_codex_usage_identity(cast(Any, request))

    assert not hasattr(request.state, "codex_usage_identity_payload")


@pytest.mark.asyncio
async def test_validate_codex_usage_identity_keeps_proxy_key_on_key_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(
        headers={"Authorization": "Bearer sk-clb-test", "chatgpt-account-id": "ignored"},
        state=SimpleNamespace(),
    )
    expected = cast(ApiKeyData, SimpleNamespace(id="key_1"))

    async def validate_key(token: str) -> ApiKeyData:
        assert token == "sk-clb-test"
        return expected

    async def unexpected_oauth(*args: object) -> VerifiedCodexOAuthIdentity:
        raise AssertionError("OAuth resolver must not receive a Proxy API Key")

    monkeypatch.setattr(auth_dependencies, "_validate_api_key_token", validate_key)
    monkeypatch.setattr(auth_dependencies, "resolve_verified_codex_oauth_identity", unexpected_oauth)

    result = await auth_dependencies.validate_codex_usage_identity(cast(Any, request))

    assert result is expected


@pytest.mark.asyncio
async def test_validate_codex_usage_identity_rejects_missing_bearer() -> None:
    request = SimpleNamespace(headers={"chatgpt-account-id": "workspace_account"}, state=SimpleNamespace())

    with pytest.raises(ProxyAuthError, match="Missing ChatGPT token"):
        await auth_dependencies.validate_codex_usage_identity(cast(Any, request))
