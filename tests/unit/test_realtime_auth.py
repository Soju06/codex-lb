from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

import app.core.auth.codex_oauth_identity as oauth_identity_module
import app.modules.proxy.realtime_auth as realtime_auth_module
from app.core.exceptions import ProxyAuthError
from app.modules.api_keys.service import ApiKeyData


@pytest.mark.asyncio
async def test_sk_clb_bearer_uses_strict_key_path_without_oauth_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth_calls = 0

    async def reject_key(_authorization: str | None) -> ApiKeyData:
        raise ProxyAuthError("invalid registered key")

    async def unexpected_oauth(*_args: object) -> object:
        nonlocal oauth_calls
        oauth_calls += 1
        raise AssertionError("OAuth fallback must remain unreachable")

    monkeypatch.setattr(oauth_identity_module, "resolve_verified_codex_oauth_identity", unexpected_oauth)

    with pytest.raises(ProxyAuthError, match="invalid registered key"):
        await realtime_auth_module.resolve_realtime_caller_scope(
            "Bearer sk-clb-invalid",
            "workspace-id",
            api_key_validator=reject_key,
        )

    assert oauth_calls == 0


@pytest.mark.asyncio
async def test_registered_key_scope_preserves_key_assignments_in_service_layer() -> None:
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-id",
            account_assignment_scope_enabled=True,
            assigned_account_ids=frozenset({"account-a"}),
        ),
    )

    async def validate_key(_authorization: str | None) -> ApiKeyData:
        return api_key

    scope = await realtime_auth_module.resolve_realtime_caller_scope(
        "Bearer sk-clb-valid",
        None,
        api_key_validator=validate_key,
    )

    assert scope.kind == "api_key"
    assert scope.affinity_scope_material == "key-id"
    assert scope.api_key is api_key
    assert scope.allowed_account_ids is None


@pytest.mark.asyncio
async def test_oauth_scope_uses_verified_principal_and_fresh_global_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = SimpleNamespace(principal_id="principal-1")

    async def resolve_identity(_authorization: str | None, _account_id: str | None) -> object:
        return identity

    async def load_policy() -> frozenset[str]:
        return frozenset({"allowed-a", "allowed-b"})

    monkeypatch.setattr(oauth_identity_module, "resolve_verified_codex_oauth_identity", resolve_identity)
    monkeypatch.setattr(realtime_auth_module, "_active_oauth_live_allowed_account_ids", load_policy)

    scope = await realtime_auth_module.resolve_realtime_caller_scope(
        "Bearer oauth-token",
        "workspace-id",
    )

    assert scope.kind == "oauth"
    assert scope.affinity_scope_material == "oauth:principal-1"
    assert scope.api_key is None
    assert scope.allowed_account_ids == frozenset({"allowed-a", "allowed-b"})


@pytest.mark.asyncio
async def test_oauth_scope_fails_closed_when_policy_has_no_active_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve_identity(_authorization: str | None, _account_id: str | None) -> object:
        return SimpleNamespace(principal_id="principal-1")

    async def load_policy() -> frozenset[str]:
        return frozenset()

    monkeypatch.setattr(oauth_identity_module, "resolve_verified_codex_oauth_identity", resolve_identity)
    monkeypatch.setattr(realtime_auth_module, "_active_oauth_live_allowed_account_ids", load_policy)

    with pytest.raises(realtime_auth_module.OAuthLiveNotEnabledError) as raised:
        await realtime_auth_module.resolve_realtime_caller_scope(
            "Bearer oauth-token",
            "workspace-id",
        )

    assert raised.value.status_code == 403
    assert raised.value.code == "oauth_live_not_enabled"
