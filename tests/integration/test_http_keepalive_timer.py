"""Regression tests: the keep-alive timer must not outlive a lost connection.

After every completed response uvicorn arms a keep-alive ``TimerHandle``
(``loop.call_later(timeout_keep_alive, self.timeout_keep_alive_handler)``)
and cancels it in ``connection_lost`` only when ``exc is None`` — a peer FIN.
Any abnormal close (RST / ``ConnectionResetError``, ``ETIMEDOUT``, any read
error) reaches ``connection_lost(exc)`` with the handle still armed. The
handle wraps a bound method, so the event loop's timer table pins the whole
protocol graph — transport wrapper, ``RequestResponseCycle``, ASGI scope with
every request header — for ``--timeout-keep-alive`` seconds while uvicorn's
connection accounting already reports the connection gone. Reverse proxies
purge idle server-side connections with RST (HAProxy does), so behind one
*every* request leaked its protocol for the full window (2 h at the former
7200 s default; the 2026-09-03 production OOM).

The suite covers three layers:

- fake-transport tests over both application protocol subclasses proving the
  timer is released and the protocol is collectable after an error close, that
  a clean close is unchanged, and that the timer still closes idle connections;
- a live-server test over real sockets with the production protocol wiring,
  closing the client side with an RST;
- canary tests pinning the stock uvicorn behavior (if a uvicorn upgrade makes
  them fail, upstream fixed it and the ``connection_lost`` override can go).
"""

from __future__ import annotations

import asyncio
import errno
import gc
import socket
import struct
import weakref
from typing import Any

import pytest
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol
from uvicorn.server import ServerState

from app.cli import _load_http_protocol_class
from app.core.http_protocol import UpgradeTolerantH11Protocol
from app.core.http_protocol_httptools import UpgradeTolerantHttpToolsProtocol
from tests.integration.test_http_upgrade_tolerance import _echo_app, _make_protocol, _wait_for_response

pytestmark = pytest.mark.integration

# Far longer than any test: the session-scoped loop must never fire the timer
# on its own, so the tests observe exactly what ``connection_lost`` did to it.
_IDLE_WINDOW_SECONDS = 7200
_REQUEST = b"GET /echo HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
_APP_PROTOCOLS = [UpgradeTolerantHttpToolsProtocol, UpgradeTolerantH11Protocol]


def _peer_reset() -> ConnectionResetError:
    return ConnectionResetError(errno.ECONNRESET, "Connection reset by peer")


async def _complete_one_request(protocol_class: type[Any]) -> tuple[Any, Any]:
    """Serve one keep-alive GET and return the protocol with its timer armed."""
    protocol, transport = _make_protocol(protocol_class, timeout_keep_alive=_IDLE_WINDOW_SECONDS)
    protocol.data_received(_REQUEST)
    await _wait_for_response(transport)
    # Let ``run_asgi``'s ``finally`` drop ``cycle.on_response`` and the done
    # callback discard the task, so the timer is the only owner left.
    async with asyncio.timeout(5.0):
        while protocol.tasks:
            await asyncio.sleep(0)
    timer = protocol.timeout_keep_alive_task
    assert timer is not None and not timer.cancelled()
    return protocol, transport


@pytest.mark.parametrize("protocol_class", _APP_PROTOCOLS)
async def test_error_close_releases_keepalive_timer_and_protocol(protocol_class: type[Any]) -> None:
    protocol, transport = await _complete_one_request(protocol_class)
    timer = protocol.timeout_keep_alive_task
    ref = weakref.ref(protocol)

    protocol.connection_lost(_peer_reset())

    assert protocol not in protocol.connections
    assert protocol.timeout_keep_alive_task is None
    assert timer.cancelled()
    # Error closes keep uvicorn's stock teardown: the transport was already
    # force-closed by the loop, so the protocol must not close it again.
    assert transport.closed is False
    del protocol, transport
    gc.collect()
    assert ref() is None  # nothing (least of all the loop's timer table) pins the protocol


@pytest.mark.parametrize("protocol_class", _APP_PROTOCOLS)
async def test_clean_close_behavior_is_unchanged(protocol_class: type[Any]) -> None:
    protocol, transport = await _complete_one_request(protocol_class)
    ref = weakref.ref(protocol)

    protocol.connection_lost(None)

    assert transport.closed is True
    assert protocol.timeout_keep_alive_task is None
    del protocol, transport
    gc.collect()
    assert ref() is None


@pytest.mark.parametrize("protocol_class", _APP_PROTOCOLS)
async def test_keepalive_timer_still_closes_an_idle_connection(protocol_class: type[Any]) -> None:
    """Behavior preservation: an idle but intact connection is still closed by the timer."""
    protocol, transport = await _complete_one_request(protocol_class)
    protocol.timeout_keep_alive_task.cancel()  # fire by hand instead of waiting 2 h

    protocol.timeout_keep_alive_handler()

    assert transport.closed is True


@pytest.mark.parametrize("protocol_class", _APP_PROTOCOLS)
async def test_error_close_during_request_leaves_cycle_disconnected(protocol_class: type[Any]) -> None:
    """No timer is armed mid-request; the override must not disturb that path."""
    protocol, transport = _make_protocol(protocol_class, timeout_keep_alive=_IDLE_WINDOW_SECONDS)
    head = b"POST /echo HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 2\r\n\r\n"
    protocol.data_received(head)
    await asyncio.sleep(0.01)
    assert protocol.cycle is not None and not protocol.cycle.response_complete

    protocol.connection_lost(_peer_reset())

    assert protocol.cycle.disconnected is True
    assert protocol.timeout_keep_alive_task is None
    async with asyncio.timeout(5.0):
        while protocol.tasks:  # the ASGI task observes the disconnect and exits
            await asyncio.sleep(0)
    assert transport.closed is False


def _reset_after_one_request(port: int) -> None:
    """Blocking client: complete one GET, then close with an RST instead of a FIN."""
    # Finite timeout so a server that never completes the response fails the test instead of hanging it.
    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as client:
        client.sendall(_REQUEST)
        buffer = b""
        while b"\r\n\r\n" not in buffer or not buffer.split(b"\r\n\r\n", 1)[1]:
            chunk = client.recv(65536)
            if not chunk:
                raise AssertionError(f"server closed before responding: {buffer!r}")
            buffer += chunk
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))


async def test_live_server_releases_protocol_after_peer_reset() -> None:
    """End-to-end proof over real sockets with the production protocol wiring.

    HAProxy's idle-purge shape: the client completes a request, the response
    is fully read, then the connection is torn down with an RST so the server
    observes ``ConnectionResetError`` rather than EOF.
    """
    config = uvicorn.Config(app=_echo_app, lifespan="off", timeout_keep_alive=_IDLE_WINDOW_SECONDS)
    config.load()
    state = ServerState()
    refs: list[weakref.ref[Any]] = []
    lost_with: list[BaseException | None] = []

    class _Recording(_load_http_protocol_class()):  # type: ignore[misc]
        def connection_lost(self, exc: Exception | None) -> None:
            lost_with.append(exc)
            super().connection_lost(exc)

    def factory() -> Any:
        protocol = _Recording(config=config, server_state=state, app_state={})
        refs.append(weakref.ref(protocol))
        return protocol

    server = await asyncio.get_running_loop().create_server(factory, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        connections = 5
        for _ in range(connections):
            await asyncio.to_thread(_reset_after_one_request, port)
        async with asyncio.timeout(10.0):
            while len(lost_with) < connections:
                await asyncio.sleep(0.01)
    finally:
        server.close()
        await server.wait_closed()

    assert len(refs) == connections
    assert all(isinstance(exc, ConnectionResetError) for exc in lost_with), lost_with
    assert not state.connections
    gc.collect()
    assert [ref() for ref in refs] == [None] * connections


@pytest.mark.parametrize("protocol_class", [HttpToolsProtocol, H11Protocol])
async def test_stock_protocol_keeps_keepalive_timer_armed_after_error_close(protocol_class: type[Any]) -> None:
    """Canary pinning the upstream defect the ``connection_lost`` override works around.

    Stock uvicorn cancels the keep-alive timer only on ``exc is None``. If a
    uvicorn upgrade makes this test fail, upstream now cancels it on every
    connection loss and the override in ``UpgradeTolerantHttpToolsProtocol``
    / ``UpgradeTolerantH11Protocol`` can be dropped.
    """
    protocol, transport = await _complete_one_request(protocol_class)
    timer = protocol.timeout_keep_alive_task
    ref = weakref.ref(protocol)
    try:
        protocol.connection_lost(_peer_reset())

        assert protocol not in protocol.connections  # accounting says it is gone ...
        assert protocol.timeout_keep_alive_task is timer and not timer.cancelled()
        del protocol, transport
        gc.collect()
        assert ref() is not None  # ... but the armed timer still pins it
    finally:
        timer.cancel()  # do not leave a 2 h handle on the shared loop
