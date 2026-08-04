from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from starlette.requests import HTTPConnection

import app.core.auth.dependencies as auth_dependencies
import app.core.request_locality as request_locality
import app.modules.proxy.realtime_auth as realtime_auth_module
from app.core.exceptions import ProxyAuthError
from app.modules.api_keys.service import ApiKeyData


def _connection() -> HTTPConnection:
    return HTTPConnection(
        {
            "type": "http",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 2455),
        }
    )


def _guarded_connection(*, client_host: str, host: str) -> HTTPConnection:
    return HTTPConnection(
        {
            "type": "http",
            "headers": [(b"host", host.encode())],
            "client": (client_host, 50000),
            "server": (host, 2455),
            "_codex_lb_raw_socket_peer": (client_host, 50000),
        }
    )


def _configure_keyless_guard(
    monkeypatch: pytest.MonkeyPatch,
    *,
    api_key_auth_enabled: bool,
    allowed_cidrs: list[str] | None = None,
) -> None:
    async def load_auth_setting() -> SimpleNamespace:
        return SimpleNamespace(api_key_auth_enabled=api_key_auth_enabled)

    monkeypatch.setattr(
        auth_dependencies,
        "get_settings_cache",
        lambda: SimpleNamespace(get=load_auth_setting),
    )
    monkeypatch.setattr(
        auth_dependencies,
        "get_settings",
        lambda: SimpleNamespace(proxy_unauthenticated_client_cidrs=allowed_cidrs or []),
    )
    monkeypatch.setattr(
        request_locality,
        "get_settings",
        lambda: SimpleNamespace(
            firewall_trust_proxy_headers=False,
            firewall_trusted_proxy_cidrs=[],
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_host", "host", "allowed_cidrs"),
    [
        ("127.0.0.1", "localhost", []),
        ("192.168.65.1", "lb.example", ["192.168.65.1/32"]),
    ],
    ids=["loopback", "configured-raw-peer-cidr"],
)
async def test_keyless_live_reuses_existing_unauthenticated_proxy_admission(
    monkeypatch: pytest.MonkeyPatch,
    client_host: str,
    host: str,
    allowed_cidrs: list[str],
) -> None:
    _configure_keyless_guard(
        monkeypatch,
        api_key_auth_enabled=False,
        allowed_cidrs=allowed_cidrs,
    )

    await realtime_auth_module._validate_keyless_origin(_guarded_connection(client_host=client_host, host=host))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_key_auth_enabled", "client_host", "host", "message"),
    [
        (False, "203.0.113.10", "lb.example", "remote access"),
        (True, "127.0.0.1", "localhost", "Missing API key"),
    ],
    ids=["untrusted-remote", "api-key-mode"],
)
async def test_keyless_live_guard_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    api_key_auth_enabled: bool,
    client_host: str,
    host: str,
    message: str,
) -> None:
    _configure_keyless_guard(
        monkeypatch,
        api_key_auth_enabled=api_key_auth_enabled,
    )

    with pytest.raises(ProxyAuthError, match=message):
        await realtime_auth_module._validate_keyless_origin(_guarded_connection(client_host=client_host, host=host))


@pytest.mark.asyncio
async def test_sk_clb_bearer_uses_strict_key_path_without_keyless_fallback() -> None:
    origin_calls = 0

    async def reject_key(_authorization: str | None) -> ApiKeyData:
        raise ProxyAuthError("invalid registered key")

    async def unexpected_origin(_connection: HTTPConnection) -> None:
        nonlocal origin_calls
        origin_calls += 1
        raise AssertionError("Keyless fallback must remain unreachable")

    with pytest.raises(ProxyAuthError, match="invalid registered key"):
        await realtime_auth_module.resolve_realtime_caller_scope(
            _connection(),
            "Bearer sk-clb-invalid",
            "workspace-id",
            api_key_validator=reject_key,
            keyless_origin_validator=unexpected_origin,
        )

    assert origin_calls == 0


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
        _connection(),
        "Bearer sk-clb-valid",
        None,
        api_key_validator=validate_key,
    )

    assert scope.kind == "api_key"
    assert scope.affinity_scope_material == "key-id"
    assert scope.api_key is api_key
    assert scope.allowed_account_ids is None


@pytest.mark.asyncio
async def test_oauth_scope_uses_keyless_origin_and_account_affinity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    origin_connections: list[HTTPConnection] = []
    affinity_inputs: list[str] = []

    async def validate_origin(candidate: HTTPConnection) -> None:
        origin_connections.append(candidate)

    async def load_policy() -> frozenset[str]:
        return frozenset({"allowed-a", "allowed-b"})

    def build_affinity(account_id: str) -> str:
        affinity_inputs.append(account_id)
        return "oauth-local:digest"

    monkeypatch.setattr(realtime_auth_module, "_active_oauth_live_allowed_account_ids", load_policy)

    scope = await realtime_auth_module.resolve_realtime_caller_scope(
        connection,
        "Bearer oauth-token",
        " workspace-id ",
        keyless_origin_validator=validate_origin,
        affinity_material_builder=build_affinity,
    )

    assert origin_connections == [connection]
    assert affinity_inputs == ["workspace-id"]
    assert scope.kind == "oauth"
    assert scope.affinity_scope_material == "oauth-local:digest"
    assert scope.api_key is None
    assert scope.allowed_account_ids == frozenset({"allowed-a", "allowed-b"})


@pytest.mark.asyncio
async def test_oauth_scope_rejects_untrusted_origin_before_policy_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_calls = 0

    async def reject_origin(_connection: HTTPConnection) -> None:
        raise ProxyAuthError("Proxy authentication must be configured before remote access is allowed")

    async def unexpected_policy() -> frozenset[str]:
        nonlocal policy_calls
        policy_calls += 1
        return frozenset({"allowed-a"})

    monkeypatch.setattr(realtime_auth_module, "_active_oauth_live_allowed_account_ids", unexpected_policy)

    with pytest.raises(ProxyAuthError, match="remote access"):
        await realtime_auth_module.resolve_realtime_caller_scope(
            _connection(),
            "Bearer oauth-token",
            "workspace-id",
            keyless_origin_validator=reject_origin,
        )

    assert policy_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization", "account_id", "message"),
    [
        (None, "workspace-id", "Missing ChatGPT token"),
        ("Basic oauth-token", "workspace-id", "Missing ChatGPT token"),
        ("Bearer oauth-token", None, "Missing chatgpt-account-id"),
        ("Bearer oauth-token", " ", "Missing chatgpt-account-id"),
    ],
)
async def test_oauth_scope_requires_bearer_and_account_header(
    authorization: str | None,
    account_id: str | None,
    message: str,
) -> None:
    async def allow_origin(_connection: HTTPConnection) -> None:
        return None

    with pytest.raises(ProxyAuthError, match=message):
        await realtime_auth_module.resolve_realtime_caller_scope(
            _connection(),
            authorization,
            account_id,
            keyless_origin_validator=allow_origin,
        )


@pytest.mark.asyncio
async def test_oauth_scope_fails_closed_when_policy_has_no_active_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate_origin(_connection: HTTPConnection) -> None:
        return None

    async def load_policy() -> frozenset[str]:
        return frozenset()

    monkeypatch.setattr(realtime_auth_module, "_active_oauth_live_allowed_account_ids", load_policy)

    with pytest.raises(realtime_auth_module.OAuthLiveNotEnabledError) as raised:
        await realtime_auth_module.resolve_realtime_caller_scope(
            _connection(),
            "Bearer oauth-token",
            "workspace-id",
            keyless_origin_validator=validate_origin,
        )

    assert raised.value.status_code == 403
    assert raised.value.code == "oauth_live_not_enabled"


def test_oauth_affinity_is_deterministic_account_bound_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime_auth_module, "get_or_create_key", lambda: b"persistent-test-key")

    first = realtime_auth_module._oauth_live_affinity_scope_material("account-a")
    repeated = realtime_auth_module._oauth_live_affinity_scope_material("account-a")
    other_account = realtime_auth_module._oauth_live_affinity_scope_material("account-b")

    assert first == repeated
    assert first.startswith("oauth-local:")
    assert len(first) == len("oauth-local:") + 64
    assert first != other_account
    assert "account-a" not in first


@pytest.mark.asyncio
async def test_oauth_scope_survives_bearer_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow_origin(_connection: HTTPConnection) -> None:
        return None

    async def load_policy() -> frozenset[str]:
        return frozenset({"allowed-a"})

    monkeypatch.setattr(realtime_auth_module, "get_or_create_key", lambda: b"persistent-test-key")
    monkeypatch.setattr(realtime_auth_module, "_active_oauth_live_allowed_account_ids", load_policy)

    before_refresh = await realtime_auth_module.resolve_realtime_caller_scope(
        _connection(),
        "Bearer oauth-token-a",
        "workspace-id",
        keyless_origin_validator=allow_origin,
    )
    after_refresh = await realtime_auth_module.resolve_realtime_caller_scope(
        _connection(),
        "Bearer oauth-token-b",
        "workspace-id",
        keyless_origin_validator=allow_origin,
    )
    other_account = await realtime_auth_module.resolve_realtime_caller_scope(
        _connection(),
        "Bearer oauth-token-b",
        "other-workspace-id",
        keyless_origin_validator=allow_origin,
    )

    assert before_refresh.affinity_scope_material == after_refresh.affinity_scope_material
    assert before_refresh.affinity_scope_material != other_account.affinity_scope_material
