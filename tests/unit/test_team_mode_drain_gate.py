"""Team mode closes the unauthenticated drain endpoints to untrusted clients.

`_is_internal_client_host` reads the raw socket peer, which is loopback for everything
arriving through the local reverse proxy, so on its own it would let any tailnet peer
stop the service.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.core.config.settings as settings_module
import app.core.request_locality as request_locality
import app.modules.health.api as health_api

pytestmark = pytest.mark.unit

_TRUSTED_TAILNET_IP = "100.64.1.5"
_UNTRUSTED_TAILNET_IP = "100.64.9.9"


def _make_request(headers: list[tuple[str, str]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/drain/start",
            "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or [])],
            # Loopback socket peer: what the reverse proxy always presents.
            "client": ("127.0.0.1", 54321),
            "query_string": b"",
            "scheme": "http",
            "server": ("lb.example", 443),
            "http_version": "1.1",
        }
    )


def _patch_environment(monkeypatch: pytest.MonkeyPatch, *, team_mode_enabled: bool) -> None:
    process_settings = SimpleNamespace(
        proxy_unauthenticated_client_cidrs=[f"{_TRUSTED_TAILNET_IP}/32", "127.0.0.1/32"],
        firewall_trust_proxy_headers=True,
        firewall_trusted_proxy_cidrs=["127.0.0.1/32", "::1/128"],
    )
    for module in (request_locality, settings_module):
        monkeypatch.setattr(module, "get_settings", lambda: process_settings)

    settings_cache = AsyncMock()
    settings_cache.get.return_value = SimpleNamespace(team_mode_enabled=team_mode_enabled)
    monkeypatch.setattr(health_api, "get_settings_cache", lambda: settings_cache)


@pytest.mark.asyncio
async def test_untrusted_tailnet_peer_cannot_drain_in_team_mode(monkeypatch):
    _patch_environment(monkeypatch, team_mode_enabled=True)

    request = _make_request([("x-forwarded-for", _UNTRUSTED_TAILNET_IP)])

    with pytest.raises(HTTPException) as excinfo:
        await health_api._require_trusted_drain_client(request)

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Internal access required"


@pytest.mark.asyncio
async def test_trusted_cidr_peer_can_drain_in_team_mode(monkeypatch):
    _patch_environment(monkeypatch, team_mode_enabled=True)

    request = _make_request([("x-forwarded-for", _TRUSTED_TAILNET_IP)])

    assert await health_api._require_trusted_drain_client(request) is None


@pytest.mark.asyncio
async def test_local_tooling_without_forwarding_header_can_drain_in_team_mode(monkeypatch):
    _patch_environment(monkeypatch, team_mode_enabled=True)

    assert await health_api._require_trusted_drain_client(_make_request()) is None


@pytest.mark.asyncio
async def test_team_mode_off_leaves_drain_gating_unchanged(monkeypatch):
    _patch_environment(monkeypatch, team_mode_enabled=False)

    request = _make_request([("x-forwarded-for", _UNTRUSTED_TAILNET_IP)])

    assert await health_api._require_trusted_drain_client(request) is None
