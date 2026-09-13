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
  plus a control run that receives the terminal frame;
- HTTP/1.1 pipelining over both layers: while the first response streams the
  second request is already parsed, so uvicorn's ``self.cycle`` is the *queued*
  request and stock ``connection_lost`` marks only that one. The stamp must
  still reach the active response (and the queued request).
"""

from __future__ import annotations

import asyncio
import errno
import socket
import struct
from collections.abc import AsyncIterator
from typing import Any

import pytest
import uvicorn
from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol
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
_PIPELINED = _REQUEST + _REQUEST  # two requests in one segment; the second is queued behind the first
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


class _LosableTransport(_FakeTransport):
    """Fake transport with asyncio's post-loss accounting: once lost, writes are dropped and it reports closing."""

    def __init__(self) -> None:
        super().__init__()
        self.lost = False

    def write(self, data: bytes | bytearray | memoryview) -> None:
        if not self.lost:
            super().write(data)

    def is_closing(self) -> bool:
        return self.closed or self.lost


def _make_protocol(
    protocol_class: type[Any], app: Any, *, transport: _FakeTransport | None = None
) -> tuple[Any, _FakeTransport]:
    config = uvicorn.Config(app=app, lifespan="off")
    config.load()
    protocol = protocol_class(config=config, server_state=ServerState(), app_state={})
    transport = _FakeTransport() if transport is None else transport
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


@pytest.mark.parametrize("protocol_class", _APP_PROTOCOLS)
@pytest.mark.parametrize(
    ("exc", "expected"),
    [(None, "eof"), (ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"), "ConnectionResetError")],
    ids=["fin", "rst"],
)
async def test_pipelined_loss_stamps_the_active_response_not_only_the_queued_request(
    protocol_class: type[Any], exc: Exception | None, expected: str
) -> None:
    """Regression: with a request queued behind the streaming one, the stamp must reach the active response.

    httptools parses the second request immediately and replaces ``self.cycle``
    with it (queued in ``self.pipeline``); h11 keeps it unparsed (``PAUSED``) so
    ``self.cycle`` stays the active one. Either way the active response's
    ``scope["state"]`` must carry the stamp and its late terminal must be
    classified dropped.
    """
    app = _StreamingApp()
    transport = _LosableTransport()
    protocol, _ = _make_protocol(protocol_class, app, transport=transport)
    protocol.data_received(_PIPELINED)
    await asyncio.wait_for(app.first_chunk_sent.wait(), timeout=5.0)
    assert len(app.states) == 1  # the second request is queued, not running
    active_state = app.states[0]
    queued_cycles = [cycle for cycle, _app in getattr(protocol, "pipeline", ())]
    if issubclass(protocol_class, HttpToolsProtocol):
        # The precondition the stamp used to miss: ``self.cycle`` is the queued request.
        assert len(queued_cycles) == 1
        assert protocol.cycle is queued_cycles[0]
        assert protocol.cycle.scope["state"] is not active_state
        assert protocol._active_cycle is not protocol.cycle
        assert protocol._active_cycle.scope["state"] is active_state
    else:
        assert queued_cycles == []
        assert protocol.cycle.scope["state"] is active_state
    written_before_loss = len(transport.buffer)

    # Same ordering as production: the app's terminal yield is scheduled, then
    # the loop reports the loss (asyncio already dropped writes at this point).
    app.release.set()
    transport.lost = True
    protocol.connection_lost(exc)

    assert active_state[HTTP_DISCONNECTED_STATE] == expected
    for cycle in queued_cycles:
        assert cycle.scope["state"][HTTP_DISCONNECTED_STATE] == expected
    await _drain_tasks(protocol)
    assert [response.outcome for response in app.responses] == [OUTCOME_TERMINAL_AFTER_DISCONNECT]
    assert len(transport.buffer) == written_before_loss
    assert b"response.completed" not in bytes(transport.buffer)
    assert len(app.states) == 1  # the queued request never started on the lost connection


async def test_pipelined_requests_served_in_order_are_both_terminal_written() -> None:
    """Behavior preservation: the active-cycle bookkeeping does not disturb pipelined serving."""
    app = _StreamingApp()
    protocol, transport = _make_protocol(UpgradeTolerantHttpToolsProtocol, app)
    protocol.data_received(_PIPELINED)
    await asyncio.wait_for(app.first_chunk_sent.wait(), timeout=5.0)
    app.release.set()
    await _drain_tasks(protocol)

    assert [response.outcome for response in app.responses] == [OUTCOME_TERMINAL_WRITTEN] * 2
    assert bytes(transport.buffer).count(b"event: response.completed") == 2
    assert protocol._active_cycle is protocol.cycle and protocol.cycle.response_complete
    assert not protocol.pipeline
    assert all(HTTP_DISCONNECTED_STATE not in state for state in app.states)
    protocol.connection_lost(None)
    assert all(HTTP_DISCONNECTED_STATE not in state for state in app.states)


def test_stock_httptools_still_routes_cycle_starts_through_start_asgi_task() -> None:
    """Canary: the active-cycle hook overrides a private uvicorn method; fail loudly if it moves."""
    assert callable(getattr(HttpToolsProtocol, "_start_asgi_task", None))
    assert UpgradeTolerantHttpToolsProtocol._start_asgi_task is not HttpToolsProtocol._start_asgi_task


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


def _abort_pipelined_after_first_chunk(port: int, *, reset: bool) -> bytes:
    """Blocking client: pipeline two requests, read the first SSE chunk, then leave with an RST or a FIN."""
    with socket.create_connection(("127.0.0.1", port), timeout=10.0) as client:
        client.sendall(_PIPELINED)
        buffer = _read_until(client, b"response.created")
        if reset:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            return buffer
        client.shutdown(socket.SHUT_WR)
        return buffer + _read_to_eof(client)


@pytest.mark.parametrize("reset", [True, False], ids=["peer-rst", "peer-fin"])
async def test_live_server_pipelined_loss_classifies_active_terminal_dropped(reset: bool) -> None:
    """Real sockets, production wiring, two pipelined requests: the active stream's terminal is dropped.

    The second request is parsed and queued while the first response streams,
    so uvicorn's ``self.cycle`` is the queued one. The active response's
    Starlette disconnect listener has resumed reading, the peer's RST/FIN is
    observed, ``connection_lost`` runs (releasing the app's terminal *before*
    the stock teardown so its write is scheduled behind the stamp), and the
    write is dropped by the closed transport. The trace must report
    ``terminal_after_disconnect`` — before the fix it reported
    ``terminal_written`` because only the queued request was stamped.
    """
    app = _StreamingApp()
    config = uvicorn.Config(app=app, lifespan="off")
    config.load()
    state = ServerState()
    lost_with: list[BaseException | None] = []
    protocols: list[Any] = []

    class _Recording(_load_http_protocol_class()):  # type: ignore[misc]
        def connection_lost(self, exc: Exception | None) -> None:
            lost_with.append(exc)
            app.release.set()
            super().connection_lost(exc)

    def factory() -> Any:
        protocol = _Recording(config=config, server_state=state, app_state={})
        protocols.append(protocol)
        return protocol

    server = await asyncio.get_running_loop().create_server(factory, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        received = await asyncio.to_thread(_abort_pipelined_after_first_chunk, port, reset=reset)
        async with asyncio.timeout(10.0):
            while not app.responses or app.responses[0].outcome is None:
                await asyncio.sleep(0.01)
    finally:
        server.close()
        await server.wait_closed()

    assert b"response.created" in received
    assert b"response.completed" not in received
    assert len(lost_with) == 1
    if reset:
        assert isinstance(lost_with[0], ConnectionResetError), lost_with
    else:
        assert lost_with == [None]
    [protocol] = protocols
    if isinstance(protocol, HttpToolsProtocol):
        # The pipelined precondition really held: the queued request replaced ``self.cycle``.
        assert len(protocol.pipeline) == 1
        assert protocol.cycle is protocol.pipeline[0][0]
        assert protocol.cycle is not protocol._active_cycle
        assert protocol.cycle.scope["state"][HTTP_DISCONNECTED_STATE] == ("ConnectionResetError" if reset else "eof")
    assert app.states[0][HTTP_DISCONNECTED_STATE] == ("ConnectionResetError" if reset else "eof")
    assert [response.outcome for response in app.responses] == [OUTCOME_TERMINAL_AFTER_DISCONNECT]
