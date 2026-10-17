from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import shutdown as shutdown_state
from app.core.config.settings import get_settings
from app.core.shutdown import DRAIN_DEADLINE_HEADER
from app.modules.health.api import router as health_router

pytestmark = pytest.mark.integration

_LOOPBACK_PEER = ("127.0.0.1", 50000)
_SPOOFED_LOOPBACK_HEADERS = {"X-Forwarded-For": "127.0.0.1"}


def _trust_every_forwarding_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    get_settings.cache_clear()


def _client(app: FastAPI, peer: tuple[str, int]) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=peer),
        base_url="http://lb.example",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_peer", ["203.0.113.24", "2001:db8::24"])
@pytest.mark.parametrize("deadline", ["1.0", "nan"])
async def test_drain_start_rejects_spoofed_loopback_from_remote_peer(app_instance, monkeypatch, raw_peer, deadline):
    _trust_every_forwarding_peer(monkeypatch)
    mark_draining = AsyncMock()
    app_instance.state.proxy_service = SimpleNamespace(mark_http_bridge_draining=mark_draining)

    async with _client(app_instance, (raw_peer, 50000)) as client:
        response = await client.post(
            "/internal/drain/start",
            headers={**_SPOOFED_LOOPBACK_HEADERS, DRAIN_DEADLINE_HEADER: deadline},
        )

    assert response.status_code == 403
    assert shutdown_state.is_draining() is False
    assert shutdown_state.is_shutdown_committed() is False
    mark_draining.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_peer", ["203.0.113.24", "2001:db8::24"])
async def test_drain_stop_and_status_reject_spoofed_loopback_from_remote_peer(app_instance, monkeypatch, raw_peer):
    _trust_every_forwarding_peer(monkeypatch)
    shutdown_state.begin_drain(timeout_seconds=30)
    snapshot = Mock(return_value={})
    app_instance.state.proxy_service = SimpleNamespace(http_bridge_activity_snapshot_nowait=snapshot)

    async with _client(app_instance, (raw_peer, 50000)) as client:
        stop_response = await client.post("/internal/drain/stop", headers=_SPOOFED_LOOPBACK_HEADERS)
        status_response = await client.get("/internal/drain/status", headers=_SPOOFED_LOOPBACK_HEADERS)

    assert (stop_response.status_code, status_response.status_code) == (403, 403)
    assert shutdown_state.is_draining() is True
    snapshot.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_peer", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
@pytest.mark.parametrize("forwarded", [False, True])
async def test_drain_control_allows_loopback_transport_peer(app_instance, monkeypatch, raw_peer, forwarded):
    _trust_every_forwarding_peer(monkeypatch)
    headers = {"X-Forwarded-For": "203.0.113.24"} if forwarded else {}

    async with _client(app_instance, (raw_peer, 50000)) as client:
        started = await client.post("/internal/drain/start", headers=headers)
        response = await client.get("/internal/drain/status", headers=headers)
        stopped = await client.post("/internal/drain/stop", headers=headers)

    assert started.status_code == 200
    assert response.status_code == 200
    assert response.json()["checks"]["draining"] == "true"
    assert stopped.status_code == 200
    assert shutdown_state.is_draining() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/internal/drain/start"), ("POST", "/internal/drain/stop"), ("GET", "/internal/drain/status")],
)
async def test_drain_control_fails_closed_without_raw_peer_capture(method, path):
    app = FastAPI()
    app.include_router(health_router)

    async with _client(app, _LOOPBACK_PEER) as client:
        response = await client.request(method, path)

    assert response.status_code == 403
