"""Team mode changes the keyless proxy-auth fallback for untrusted clients.

Trusted clients (local or in ``proxy_unauthenticated_client_cidrs``) keep working
without a key.  With team mode on, every other client may still get in, but only
by presenting a valid ``sk-clb-`` key.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import HTTPConnection

import app.core.config.settings as settings_module
import app.core.request_locality as request_locality

from app.core.auth import dependencies as auth_dependencies
from app.core.exceptions import ProxyAuthError
from app.modules.api_keys.service import ApiKeyData

pytestmark = pytest.mark.unit


def _make_connection(
    socket_host: str = "203.0.113.5",
    *,
    headers: list[tuple[str, str]] | None = None,
) -> HTTPConnection:
    return HTTPConnection(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/models",
            "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or [])],
            "client": (socket_host, 12345),
            "query_string": b"",
            "scheme": "http",
            "server": ("lb.example", 443),
            "http_version": "1.1",
        }
    )


def _api_key_data() -> ApiKeyData:
    return ApiKeyData(
        id="key-1",
        name="teammate",
        key_prefix="sk-clb-abcdefg",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 9, 18, 12, 0, 0),
        last_used_at=None,
        member_id="member-1",
    )


def _patch_settings(monkeypatch, *, team_mode_enabled: bool) -> None:
    settings_cache = AsyncMock()
    settings_cache.get.return_value = SimpleNamespace(
        api_key_auth_enabled=False,
        team_mode_enabled=team_mode_enabled,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache)


def _patch_client_trust(monkeypatch, *, trusted: bool) -> None:
    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _request: trusted)
    monkeypatch.setattr(auth_dependencies, "_is_proxy_unauthenticated_client_allowed", lambda _request: trusted)


def _patch_token_validation(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def _validate(token: str) -> ApiKeyData:
        seen.append(token)
        return _api_key_data()

    monkeypatch.setattr(auth_dependencies, "_validate_api_key_token", _validate)
    return seen


@pytest.mark.asyncio
async def test_untrusted_client_with_valid_key_is_validated_when_team_mode_on(monkeypatch):
    _patch_settings(monkeypatch, team_mode_enabled=True)
    _patch_client_trust(monkeypatch, trusted=False)
    seen = _patch_token_validation(monkeypatch)

    result = await auth_dependencies.validate_proxy_api_key_authorization(
        "Bearer sk-clb-teammate-key",
        request=_make_connection(),
    )

    assert result is not None
    assert result.member_id == "member-1"
    assert seen == ["sk-clb-teammate-key"]


@pytest.mark.asyncio
async def test_untrusted_client_without_key_is_rejected_when_team_mode_on(monkeypatch):
    _patch_settings(monkeypatch, team_mode_enabled=True)
    _patch_client_trust(monkeypatch, trusted=False)
    _patch_token_validation(monkeypatch)

    with pytest.raises(ProxyAuthError, match="Proxy authentication must be configured"):
        await auth_dependencies.validate_proxy_api_key_authorization(None, request=_make_connection())


@pytest.mark.asyncio
async def test_untrusted_client_with_non_agent_lb_bearer_is_rejected_when_team_mode_on(monkeypatch):
    _patch_settings(monkeypatch, team_mode_enabled=True)
    _patch_client_trust(monkeypatch, trusted=False)
    seen = _patch_token_validation(monkeypatch)

    with pytest.raises(ProxyAuthError, match="Proxy authentication must be configured"):
        await auth_dependencies.validate_proxy_api_key_authorization(
            "Bearer sk-ant-somebody-elses-key",
            request=_make_connection(),
        )

    assert seen == []


@pytest.mark.asyncio
async def test_trusted_client_with_junk_bearer_stays_keyless(monkeypatch):
    _patch_settings(monkeypatch, team_mode_enabled=True)
    _patch_client_trust(monkeypatch, trusted=True)
    seen = _patch_token_validation(monkeypatch)

    result = await auth_dependencies.validate_proxy_api_key_authorization(
        "Bearer not-a-real-key",
        request=_make_connection("127.0.0.1"),
    )

    assert result is None
    assert seen == []


@pytest.mark.asyncio
async def test_team_mode_off_keeps_old_behaviour(monkeypatch):
    _patch_settings(monkeypatch, team_mode_enabled=False)
    _patch_client_trust(monkeypatch, trusted=False)
    seen = _patch_token_validation(monkeypatch)

    with pytest.raises(ProxyAuthError, match="Proxy authentication must be configured"):
        await auth_dependencies.validate_proxy_api_key_authorization(
            "Bearer sk-clb-teammate-key",
            request=_make_connection(),
        )

    assert seen == []


@pytest.mark.asyncio
async def test_settings_without_team_mode_attribute_keeps_old_behaviour(monkeypatch):
    settings_cache = AsyncMock()
    settings_cache.get.return_value = SimpleNamespace(api_key_auth_enabled=False)
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache)
    _patch_client_trust(monkeypatch, trusted=False)
    _patch_token_validation(monkeypatch)

    with pytest.raises(ProxyAuthError, match="Proxy authentication must be configured"):
        await auth_dependencies.validate_proxy_api_key_authorization(
            "Bearer sk-clb-teammate-key",
            request=_make_connection(),
        )


@pytest.mark.asyncio
async def test_loopback_tooling_without_forwarding_header_stays_keyless(monkeypatch):
    """Deployment config: trust_proxy_headers on, loopback the only trusted proxy.

    Local tooling sends no forwarding header, so the socket peer is the resolved client
    and the loopback entry in proxy_unauthenticated_client_cidrs admits it keyless. Uses
    the real trust helpers rather than stubs.
    """
    _patch_settings(monkeypatch, team_mode_enabled=True)
    process_settings = SimpleNamespace(
        proxy_unauthenticated_client_cidrs=["127.0.0.1/32"],
        firewall_trust_proxy_headers=True,
        firewall_trusted_proxy_cidrs=["127.0.0.1/32", "::1/128"],
    )
    for module in (auth_dependencies, request_locality, settings_module):
        monkeypatch.setattr(module, "get_settings", lambda: process_settings)
    seen = _patch_token_validation(monkeypatch)

    request = _make_connection("127.0.0.1")

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(request) is True
    assert await auth_dependencies.validate_proxy_api_key_authorization(None, request=request) is None
    assert seen == []


@pytest.mark.asyncio
async def test_forwarded_public_client_is_still_rejected_in_deployment_config(monkeypatch):
    """The hardening guarantee survives: a forwarded public IP is not the loopback entry."""

    _patch_settings(monkeypatch, team_mode_enabled=True)
    process_settings = SimpleNamespace(
        proxy_unauthenticated_client_cidrs=["127.0.0.1/32"],
        firewall_trust_proxy_headers=True,
        firewall_trusted_proxy_cidrs=["127.0.0.1/32", "::1/128"],
    )
    for module in (auth_dependencies, request_locality, settings_module):
        monkeypatch.setattr(module, "get_settings", lambda: process_settings)
    _patch_token_validation(monkeypatch)

    request = _make_connection("127.0.0.1", headers=[("x-forwarded-for", "198.51.100.42")])

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(request) is False
    with pytest.raises(ProxyAuthError, match="Proxy authentication must be configured"):
        await auth_dependencies.validate_proxy_api_key_authorization(None, request=request)
