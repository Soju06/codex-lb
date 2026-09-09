"""Connection loss mid-response is stamped into the request's ``scope["state"]``.

Uvicorn's ``send`` silently returns once ``cycle.disconnected`` is set, and
``receive()`` yields ``http.disconnect`` after every *normal* completion as
well, so an application cannot tell a dropped late write from a finished one.
``stamp_disconnect_into_scope`` (called from both production protocol
subclasses' ``connection_lost``) records the loss kind under
``HTTP_DISCONNECTED_STATE``; ``DeliveryTracedStreamingResponse`` reads it to
classify an SSE terminal frame written after the peer went away.

Layers:

- fake-transport tests over both protocol subclasses (stamp value, absence
  after a completed response, no extra teardown side effects);
- a live-socket test with the production protocol wiring where the client
  half-closes (FIN) after the first SSE chunk — the shape that produces a
  full-looking body without ``response.completed`` and a ``success`` row —
  plus a control run that receives the terminal frame.
"""

from __future__ import annotations

import asyncio
import errno
import socket
from collections.abc import AsyncIterator
from typing import Any

import pytest
import uvicorn
from uvicorn.server import ServerState

from app.cli import _load_http_protocol_class
from app.core.http_protocol import HTTP_DISCONNECTED_STATE, UpgradeTolerantH11Protocol
from app.core.http_protocol_httptools import UpgradeTolerantHttpToolsProtocol
from app.modules.proxy.downstream_delivery import (
    OUTCOME_TERMINAL_AFTER_DISCONNECT,
    OUTCOME_TERMINAL_WRITTEN,
    DeliveryTracedStreamingResponse,
)
from tests.integration.test_http_upgrade_tolerance import _echo_app, _FakeTransport

pytestmark = pytest.mark.integration

_APP_PROTOCOLS = [UpgradeTolerantHttpToolsProtocol, UpgradeTolerantH11Protocol]
_REQUEST = b"POST /stream HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n"
_FIRST = 'event: response.created\ndata: {"type":"response.created"}\n\n'
_TERMINAL = 'event: response.completed\ndata: {"type":"response.completed"}\n\n'


class _StreamingApp:
    """ASGI app that streams the first SSE chunk, then waits for ``release`` before the terminal."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.first_chunk_sent = asyncio.Event()
        self.responses: list[DeliveryTracedStreamingResponse] = []
        self.states: list[dict[str, object]] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        assert scope["type"] == "http"
        self.states.append(scope["state"])

        async def body() -> AsyncIterator[str]:
            yield _FIRST
            self.first_chunk_sent.set()
            await self.release.wait()
            yield _TERMINAL

        response = DeliveryTracedStreamingResponse(body(), surface="responses")
        self.responses.append(response)
        await response(scope, receive, send)


def _make_protocol(protocol_class: type[Any], app: Any) -> tuple[Any, _FakeTransport]:
    config = uvicorn.Config(app=app, lifespan="off")
    config.load()
    protocol = protocol_class(config=config, server_state=ServerState(), app_state={})
    transport = _FakeTransport()
    protocol.connection_made(transport)
    return protocol, transport


async def _drain_tasks(protocol: Any) -> None:
    async with asyncio.timeout(5.0):
        while protocol.tasks:
            await asyncio.sleep(0)


@pytest.mark.parametrize("protocol_class", _APP_PROTOCOLS)
@pytest.mark.parametrize(
    ("exc", "expected"),
    [(None, "eof"), (ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"), "ConnectionResetError")],
    ids=["fin", "rst"],
)
async def test_mid_response_loss_is_stamped_and_terminal_is_classified_dropped(
    protocol_class: type[Any], exc: Exception | None, expected: str
) -> None:
    app = _StreamingApp()
    protocol, transport = _make_protocol(protocol_class, app)
    protocol.data_received(_REQUEST)
    await asyncio.wait_for(app.first_chunk_sent.wait(), timeout=5.0)
    assert protocol.cycle is not None and not protocol.cycle.response_complete
    written_before_loss = len(transport.buffer)

    # Release the terminal first and lose the connection in the same tick: the
    # app resumes only after connection_lost ran, exactly the production race.
    app.release.set()
    protocol.connection_lost(exc)

    assert protocol.cycle.disconnected is True
    assert app.states[0][HTTP_DISCONNECTED_STATE] == expected
    assert protocol.cycle.scope["state"] is app.states[0]  # the stamp landed on the dict the app sees
    await _drain_tasks(protocol)
    assert app.responses[0].outcome == OUTCOME_TERMINAL_AFTER_DISCONNECT
    assert len(transport.buffer) == written_before_loss  # uvicorn dropped the late write
    assert b"response.completed" not in bytes(transport.buffer)
    # Stock teardown is unchanged: clean close closes the transport, an error close does not close it again.
    assert transport.closed is (exc is None)


@pytest.mark.parametrize("protocol_class", _APP_PROTOCOLS)
async def test_loss_after_completed_response_leaves_no_stamp(protocol_class: type[Any]) -> None:
    app = _StreamingApp()
    protocol, transport = _make_protocol(protocol_class, app)
    protocol.data_received(_REQUEST)
    await asyncio.wait_for(app.first_chunk_sent.wait(), timeout=5.0)
    app.release.set()
    await _drain_tasks(protocol)
    assert app.responses[0].outcome == OUTCOME_TERMINAL_WRITTEN
    assert b"response.completed" in bytes(transport.buffer)
    assert protocol.cycle.response_complete is True

    protocol.connection_lost(None)

    assert HTTP_DISCONNECTED_STATE not in app.states[0]
    assert protocol.timeout_keep_alive_task is None
    assert transport.closed is True


@pytest.mark.parametrize("protocol_class", _APP_PROTOCOLS)
async def test_non_streaming_request_loss_stamps_the_open_cycle(protocol_class: type[Any]) -> None:
    """The stamp is protocol-level: any in-flight cycle gets it, not only traced responses."""
    protocol, _ = _make_protocol(protocol_class, _echo_app)
    protocol.data_received(b"POST /echo HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 2\r\n\r\n")
    await asyncio.sleep(0.01)
    assert protocol.cycle is not None and not protocol.cycle.response_complete

    protocol.connection_lost(None)

    assert protocol.cycle.scope["state"][HTTP_DISCONNECTED_STATE] == "eof"
    await _drain_tasks(protocol)


def _read_until(client: socket.socket, marker: bytes) -> bytes:
    buffer = b""
    while marker not in buffer:
        chunk = client.recv(65536)
        if not chunk:
            raise AssertionError(f"server closed before {marker!r}: {buffer!r}")
        buffer += chunk
    return buffer


def _read_to_eof(client: socket.socket) -> bytes:
    buffer = b""
    while True:
        chunk = client.recv(65536)
        if not chunk:
            return buffer
        buffer += chunk


def _half_close_after_first_chunk(port: int) -> bytes:
    """Blocking client: read the first SSE chunk, send FIN, then read whatever the server still writes."""
    with socket.create_connection(("127.0.0.1", port), timeout=10.0) as client:
        client.sendall(_REQUEST)
        buffer = _read_until(client, b"response.created")
        client.shutdown(socket.SHUT_WR)
        return buffer + _read_to_eof(client)


def _read_full_stream(port: int) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=10.0) as client:
        client.sendall(_REQUEST)
        return _read_until(client, b"0\r\n\r\n")


@pytest.mark.parametrize("half_close", [True, False], ids=["peer-fin-before-terminal", "control"])
async def test_live_server_classifies_terminal_after_peer_half_close(half_close: bool) -> None:
    """End-to-end over real sockets with the production protocol wiring.

    The client half-closes after the first chunk while the app is still waiting
    on upstream; the app yields the terminal only after ``connection_lost`` ran
    (deterministic ordering via the ``release`` event set from the protocol
    hook), so uvicorn drops the write and the trace must say so. The control
    run receives the terminal and the chunked terminator.
    """
    app = _StreamingApp()
    config = uvicorn.Config(app=app, lifespan="off")
    config.load()
    state = ServerState()
    lost_with: list[BaseException | None] = []

    class _Recording(_load_http_protocol_class()):  # type: ignore[misc]
        def connection_lost(self, exc: Exception | None) -> None:
            lost_with.append(exc)
            # Wake the app before super() so its terminal yield is scheduled
            # ahead of Starlette's disconnect-driven cancellation; everything
            # in connection_lost (including the stamp) still runs before either
            # callback gets the loop.
            app.release.set()
            super().connection_lost(exc)

    server = await asyncio.get_running_loop().create_server(
        lambda: _Recording(config=config, server_state=state, app_state={}), "127.0.0.1", 0
    )
    try:
        port = server.sockets[0].getsockname()[1]
        if half_close:
            received = await asyncio.to_thread(_half_close_after_first_chunk, port)
        else:
            client = asyncio.to_thread(_read_full_stream, port)
            task = asyncio.ensure_future(client)
            await asyncio.wait_for(app.first_chunk_sent.wait(), timeout=10.0)
            app.release.set()
            received = await asyncio.wait_for(task, timeout=10.0)
        async with asyncio.timeout(10.0):
            while not app.responses or app.responses[0].outcome is None:
                await asyncio.sleep(0.01)
    finally:
        server.close()
        await server.wait_closed()

    assert b"response.created" in received
    if half_close:
        assert lost_with == [None]  # peer FIN reaches connection_lost as a clean close
        assert app.states[0][HTTP_DISCONNECTED_STATE] == "eof"
        assert app.responses[0].outcome == OUTCOME_TERMINAL_AFTER_DISCONNECT
        assert b"response.completed" not in received
        assert not received.endswith(b"0\r\n\r\n")
    else:
        assert app.responses[0].outcome == OUTCOME_TERMINAL_WRITTEN
        assert b"response.completed" in received
        assert received.endswith(b"0\r\n\r\n")
        assert HTTP_DISCONNECTED_STATE not in app.states[0]
