"""Team mode closes the dashboard and its admin APIs to untrusted clients.

The live deployment runs with ``AGENT_LB_DASHBOARD_AUTH_MODE=disabled``, so without this
gate every admin route would be open to any tailnet peer that team mode lets reach the
port.  The check therefore runs ahead of the DISABLED / trusted-header short-circuit.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

import app.core.config.settings as settings_module
import app.core.request_locality as request_locality
from app.core.auth import dependencies as auth_dependencies
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.exceptions import DashboardAuthError, DashboardForbiddenError

pytestmark = pytest.mark.unit

_TRUSTED_TAILNET_IP = "100.64.1.5"
_UNTRUSTED_TAILNET_IP = "100.64.9.9"


def _make_request(client_host: str, *, headers: list[tuple[str, str]] | None = None) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or [])]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/accounts",
            "headers": raw_headers,
            "client": (client_host, 54321),
            "query_string": b"",
            "scheme": "https",
            # Not "testserver": is_local_request treats the test server as local.
            "server": ("lb.example", 443),
            "http_version": "1.1",
        }
    )


def _patch_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    team_mode_enabled: bool,
    dashboard_auth_mode: DashboardAuthMode = DashboardAuthMode.DISABLED,
    trust_proxy_headers: bool = True,
) -> None:
    process_settings = SimpleNamespace(
        proxy_unauthenticated_client_cidrs=[f"{_TRUSTED_TAILNET_IP}/32", "127.0.0.1/32"],
        firewall_trust_proxy_headers=trust_proxy_headers,
        firewall_trusted_proxy_cidrs=["127.0.0.1/32", "::1/128"],
        dashboard_auth_mode=dashboard_auth_mode,
    )
    for module in (auth_dependencies, request_locality, settings_module):
        monkeypatch.setattr(module, "get_settings", lambda: process_settings)

    settings_cache = AsyncMock()
    settings_cache.get.return_value = SimpleNamespace(
        api_key_auth_enabled=False,
        team_mode_enabled=team_mode_enabled,
        password_hash=None,
        totp_required_on_login=False,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache)


@pytest.mark.asyncio
async def test_untrusted_client_is_forbidden_when_team_mode_on(monkeypatch):
    _patch_environment(monkeypatch, team_mode_enabled=True)

    # Arrives through the trusted loopback reverse proxy, carrying its own tailnet IP.
    request = _make_request("127.0.0.1", headers=[("x-forwarded-for", _UNTRUSTED_TAILNET_IP)])

    with pytest.raises(DashboardForbiddenError) as excinfo:
        await auth_dependencies.validate_dashboard_session(request)

    error = excinfo.value
    assert error.status_code == 403
    assert error.code == "team_mode_untrusted_client"
    assert isinstance(error, DashboardAuthError)


@pytest.mark.asyncio
async def test_trusted_cidr_client_passes_when_team_mode_on(monkeypatch):
    _patch_environment(monkeypatch, team_mode_enabled=True)

    request = _make_request("127.0.0.1", headers=[("x-forwarded-for", _TRUSTED_TAILNET_IP)])

    assert await auth_dependencies.validate_dashboard_session(request) is None


@pytest.mark.asyncio
async def test_local_client_passes_when_team_mode_on(monkeypatch):
    _patch_environment(monkeypatch, team_mode_enabled=True, trust_proxy_headers=False)

    request = _make_request("127.0.0.1", headers=[("host", "localhost")])

    assert await auth_dependencies.validate_dashboard_session(request) is None


@pytest.mark.asyncio
async def test_team_mode_off_keeps_the_disabled_short_circuit_for_remote_clients(monkeypatch):
    _patch_environment(monkeypatch, team_mode_enabled=False)

    request = _make_request("127.0.0.1", headers=[("x-forwarded-for", _UNTRUSTED_TAILNET_IP)])

    assert await auth_dependencies.validate_dashboard_session(request) is None


@pytest.mark.asyncio
async def test_settings_without_team_mode_attribute_keeps_the_short_circuit(monkeypatch):
    _patch_environment(monkeypatch, team_mode_enabled=False)
    settings_cache = AsyncMock()
    settings_cache.get.return_value = SimpleNamespace(
        api_key_auth_enabled=False,
        password_hash=None,
        totp_required_on_login=False,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache)

    request = _make_request("127.0.0.1", headers=[("x-forwarded-for", _UNTRUSTED_TAILNET_IP)])

    assert await auth_dependencies.validate_dashboard_session(request) is None


@pytest.mark.asyncio
async def test_untrusted_client_is_forbidden_before_any_password_prompt(monkeypatch):
    """Standard mode: the 403 lands ahead of the session-cookie check, not after it."""

    _patch_environment(
        monkeypatch,
        team_mode_enabled=True,
        dashboard_auth_mode=DashboardAuthMode.STANDARD,
    )
    settings_cache = AsyncMock()
    settings_cache.get.return_value = SimpleNamespace(
        api_key_auth_enabled=False,
        team_mode_enabled=True,
        password_hash="argon2-hash",
        totp_required_on_login=False,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache)

    request = _make_request("127.0.0.1", headers=[("x-forwarded-for", _UNTRUSTED_TAILNET_IP)])

    with pytest.raises(DashboardForbiddenError) as excinfo:
        await auth_dependencies.validate_dashboard_session(request)

    assert excinfo.value.code == "team_mode_untrusted_client"


@pytest.mark.asyncio
async def test_trusted_client_still_hits_the_password_prompt_in_standard_mode(monkeypatch):
    """The gate must not become a bypass: trusted clients keep their normal auth path."""

    _patch_environment(
        monkeypatch,
        team_mode_enabled=True,
        dashboard_auth_mode=DashboardAuthMode.STANDARD,
    )
    settings_cache = AsyncMock()
    settings_cache.get.return_value = SimpleNamespace(
        api_key_auth_enabled=False,
        team_mode_enabled=True,
        password_hash="argon2-hash",
        totp_required_on_login=False,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache)

    request = _make_request("127.0.0.1", headers=[("x-forwarded-for", _TRUSTED_TAILNET_IP)])

    with pytest.raises(DashboardAuthError) as excinfo:
        await auth_dependencies.validate_dashboard_session(request)

    assert not isinstance(excinfo.value, DashboardForbiddenError)
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_loopback_tooling_without_forwarding_header_passes_in_deployment_config(monkeypatch):
    """The real deployment config: trust_proxy_headers on, loopback the only trusted proxy.

    Local tooling on the box dials loopback and sends no forwarding header. It resolves
    to 127.0.0.1 and is admitted through the unauthenticated CIDR allowlist, using the
    real _is_proxy_unauthenticated_client_allowed rather than a stub.
    """
    _patch_environment(monkeypatch, team_mode_enabled=True, trust_proxy_headers=True)

    request = _make_request("127.0.0.1", headers=[("host", "localhost")])

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(request) is True
    assert await auth_dependencies.validate_dashboard_session(request) is None
