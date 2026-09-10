from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientConnectorError, ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer

from app.core.config.settings import get_settings
from app.modules.desktop_relay import lifecycle
from app.modules.desktop_relay.api import _create_app
from app.modules.desktop_relay.transport import RelayTransport

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def restore_settings(monkeypatch):
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture
def relay_port(monkeypatch, unused_tcp_port):
    # Production always asks for 8000; tests own an isolated ephemeral port.
    site = web.TCPSite

    def isolated_site(runner, host, port):
        assert port == 8000
        return site(runner, host, unused_tcp_port)

    monkeypatch.setattr(lifecycle.web, "TCPSite", isolated_site)
    monkeypatch.setattr(lifecycle, "_SHUTDOWN_TIMEOUT", 0.05)
    return unused_tcp_port


@asynccontextmanager
async def upstream_relay(monkeypatch, handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    async with TestServer(app) as upstream:
        relay_apps = []

        def create_relay(_origin):
            relay = _create_app(upstream.make_url(""), upstream.make_url(""))
            relay_apps.append(relay)
            return relay

        monkeypatch.setattr(lifecycle, "create_app", create_relay)
        yield str(upstream.make_url("")), relay_apps


async def test_off_does_not_allocate_listener_or_transport(monkeypatch):
    def unexpected(_origin):
        pytest.fail("Disabled relay allocated an application")

    monkeypatch.setattr(lifecycle, "create_app", unexpected)
    async with lifecycle.serve_relay("off", None):
        pass


@pytest.mark.parametrize("mode", ["loopback", "container"])
async def test_listener_routes_usage_preserves_host_guard_and_closes(monkeypatch, relay_port, mode):
    async def upstream(request):
        assert request.path == "/api/codex/desktop/usage"
        return web.Response(status=401)

    async with upstream_relay(monkeypatch, upstream) as (origin, _apps), ClientSession() as client:
        async with lifecycle.serve_relay(mode, origin):
            url = f"http://127.0.0.1:{relay_port}/backend-api/wham/usage"
            async with client.get(url, headers={"Host": "localhost:8000"}) as response:
                assert response.status == 401
            async with client.get(url, headers={"Host": "evil.example"}) as response:
                assert response.status == 403
        with pytest.raises(ClientConnectorError):
            await client.get(url)


@pytest.mark.parametrize("cancel_owner", [False, True])
async def test_active_http_and_websocket_close_with_lifecycle(monkeypatch, relay_port, cancel_owner):
    http_closed, websocket_closed, serving = asyncio.Event(), asyncio.Event(), asyncio.Event()
    stop = asyncio.Event()

    async def upstream(request):
        if request.path.endswith("/ws"):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            try:
                async for message in ws:
                    if message.type == WSMsgType.TEXT:
                        await ws.send_str(message.data)
            finally:
                websocket_closed.set()
            return ws
        response = web.StreamResponse()
        await response.prepare(request)
        try:
            while True:
                await response.write(b"data\n")
                await asyncio.sleep(0.01)
        except (ConnectionError, asyncio.CancelledError):
            http_closed.set()
        return response

    async with upstream_relay(monkeypatch, upstream) as (origin, apps), ClientSession() as client:

        async def owner():
            async with lifecycle.serve_relay("container", origin):
                serving.set()
                await stop.wait()

        task = asyncio.create_task(owner())
        try:
            await asyncio.wait_for(serving.wait(), 2)
            base = f"http://127.0.0.1:{relay_port}/backend-api"
            async with client.get(base + "/events", headers={"Host": "localhost:8000"}) as response:
                assert await response.content.readline() == b"data\n"
                async with client.ws_connect(base + "/ws", headers={"Host": "localhost:8000"}) as ws:
                    await ws.send_str("ready")
                    assert (await ws.receive()).data == "ready"
                    if cancel_owner:
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 2)
                    else:
                        stop.set()
                        await asyncio.wait_for(task, 2)
                    await asyncio.wait_for(http_closed.wait(), 2)
                    await asyncio.wait_for(websocket_closed.wait(), 2)
                    assert (await ws.receive()).type in {WSMsgType.CLOSE, WSMsgType.CLOSED}
                    transport = next(value for value in apps[0].values() if isinstance(value, RelayTransport))
                    assert all(route.session.closed for route in (transport.local, transport.http, transport.websocket))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_partial_dual_stack_bind_failure_closes_first_listener_and_transport(monkeypatch, relay_port):
    closed = asyncio.Event()
    app = web.Application()

    async def transport_context(_app):
        try:
            yield
        finally:
            closed.set()

    app.cleanup_ctx.append(transport_context)
    monkeypatch.setattr(lifecycle, "create_app", lambda origin: app)
    with socket.socket(socket.AF_INET6) as occupied:
        occupied.bind(("::1", relay_port))
        occupied.listen()
        with pytest.raises(RuntimeError, match="Desktop relay could not start"):
            async with lifecycle.serve_relay("loopback", "http://127.0.0.1:2455"):
                pytest.fail("Occupied listener must fail startup")
        assert closed.is_set()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", relay_port))


async def test_transport_startup_failure_cleans_previously_entered_context(monkeypatch):
    closed = asyncio.Event()
    app = web.Application()

    async def opened(_app):
        try:
            yield
        finally:
            closed.set()

    async def failure(_app):
        raise ValueError("http://private:secret@proxy")
        yield

    app.cleanup_ctx.extend((opened, failure))
    monkeypatch.setattr(lifecycle, "create_app", lambda origin: app)
    with pytest.raises(RuntimeError, match="Desktop relay could not start") as error:
        async with lifecycle.serve_relay("container", "http://127.0.0.1:2455"):
            pytest.fail("Transport startup failed")
    assert closed.is_set()
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("mode", ["off", "container"])
async def test_normal_lifespan_owns_listener_and_closes_it_before_shared_clients(
    db_setup, monkeypatch, relay_port, mode
):
    import app.main as main

    monkeypatch.setenv("CODEX_LB_DESKTOP_RELAY_MODE", mode)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "2455")
    monkeypatch.delenv("SSL_CERTFILE", raising=False)
    monkeypatch.delenv("SSL_KEYFILE", raising=False)
    get_settings.cache_clear()
    if mode == "off":

        def unexpected(_origin):
            pytest.fail("Disabled normal server started a relay")

        monkeypatch.setattr(lifecycle, "create_app", unexpected)
    url = f"http://127.0.0.1:{relay_port}/outside-backend"
    close_clients = main.close_http_client
    checked_cleanup = False

    async def close_after_relay():
        nonlocal checked_cleanup
        async with ClientSession() as probe:
            with pytest.raises(ClientConnectorError):
                await probe.get(url)
        checked_cleanup = True
        await close_clients()

    monkeypatch.setattr(main, "close_http_client", close_after_relay)
    app = main.create_app()
    async with main.lifespan(app), ClientSession() as probe:
        if mode == "container":
            async with probe.get(url, headers={"Host": "localhost:8000"}) as response:
                assert response.status == 404
    assert checked_cleanup


async def test_normal_lifespan_cleans_main_resources_when_relay_startup_fails(db_setup, monkeypatch):
    import app.main as main

    monkeypatch.setenv("CODEX_LB_DESKTOP_RELAY_MODE", "container")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "2455")
    monkeypatch.delenv("SSL_CERTFILE", raising=False)
    monkeypatch.delenv("SSL_KEYFILE", raising=False)
    get_settings.cache_clear()

    @asynccontextmanager
    async def failure(_mode, _origin):
        raise RuntimeError("Desktop relay could not start")
        yield

    monkeypatch.setattr(main, "serve_relay", failure)
    close_clients = AsyncMock(wraps=main.close_http_client)
    monkeypatch.setattr(main, "close_http_client", close_clients)
    app = main.create_app()
    with pytest.raises(RuntimeError, match="Desktop relay could not start"):
        async with main.lifespan(app):
            pytest.fail("Normal server must not become ready")
    close_clients.assert_awaited_once()
