from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any, cast

import aiohttp
import pytest
from aiohttp.compression_utils import ZLibCompressor
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.frames import Frame, Opcode

from app.core.clients.proxy_websocket import (
    ArchivingUpstreamWebSocket,
    CodexUpstreamWebSocket,
    UpstreamWebSocket,
    WebsocketsUpstreamWebSocket,
)
from app.core.clients.websocket_dispatch import (
    WebSocketDispatchTransport,
    current_websocket_send_callback,
    websocket_send_context,
)


class _RecordingTransport(asyncio.Transport):
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.fail = False

    def write(self, data: Any) -> None:
        if self.fail:
            raise OSError("write failed")
        self.writes.append(bytes(data))


def _capture_callback() -> Callable[[], None]:
    callback = current_websocket_send_callback()
    assert callback is not None
    return callback


@pytest.mark.parametrize("outcome", ["return", "failure", "cancel"])
def test_callback_expires_with_its_send_scope(outcome: str) -> None:
    dispatched: list[str] = []
    with suppress(RuntimeError, asyncio.CancelledError):
        with websocket_send_context(lambda: dispatched.append("expired")):
            expired = _capture_callback()
            if outcome == "failure":
                raise RuntimeError("send failed")
            if outcome == "cancel":
                raise asyncio.CancelledError

    with websocket_send_context(lambda: dispatched.append("current")):
        expired()
        current = _capture_callback()
        current()
        current()

    assert dispatched == ["current"]
    assert current_websocket_send_callback() is None


@pytest.mark.parametrize("size", [0, 200, 70000])
def test_only_successful_complete_text_write_on_owned_transport_dispatches(size: int) -> None:
    raw = _RecordingTransport()
    owned = WebSocketDispatchTransport(raw)
    other = WebSocketDispatchTransport(_RecordingTransport())
    dispatched: list[int] = []
    text = Frame(Opcode.TEXT, b"a" * size).serialize(mask=True)
    with websocket_send_context(lambda: dispatched.append(len(raw.writes))):
        with owned.send_context():
            other.write(text)
            owned.write(Frame(Opcode.PING, b"ping").serialize(mask=True))
            owned.write(Frame(Opcode.BINARY, b"bytes").serialize(mask=True))
            owned.write(Frame(Opcode.TEXT, b"part", fin=False).serialize(mask=True))
            owned.write(text[:-1])
            assert dispatched == []
            raw.fail = True
            with pytest.raises(OSError, match="write failed"):
                owned.write(text)
            assert dispatched == []
            raw.fail = False
            owned.write(text)
            owned.write(text)

    assert dispatched == [5]


@asynccontextmanager
async def _connected_adapter(kind: str) -> AsyncIterator[tuple[UpstreamWebSocket, Any]]:
    async def echo(connection: ServerConnection) -> None:
        async for message in connection:
            await connection.send(message)

    async with serve(echo, "127.0.0.1", 0, ping_interval=None) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}"
        if kind == "websockets":
            async with connect(url, proxy=None, ping_interval=None) as connection:
                yield WebsocketsUpstreamWebSocket(connection), connection
        else:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, compress=15) as websocket:
                    yield CodexUpstreamWebSocket(websocket), websocket


async def _tracked_send(upstream: UpstreamWebSocket, text: str, on_dispatched: Callable[[], None]) -> None:
    with websocket_send_context(on_dispatched):
        await upstream.send_text(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["websockets", "aiohttp"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_real_transport_dispatches_before_drain_and_only_once(
    kind: str, cancel: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _connected_adapter(kind) as (upstream, raw):
        drain_started = asyncio.Event()
        finish_drain = asyncio.Event()
        dispatched: list[str] = []

        async def held_drain() -> None:
            drain_started.set()
            await finish_drain.wait()

        if kind == "websockets":
            monkeypatch.setattr(raw, "drain", held_drain)
        else:
            raw._writer._limit = 0
            raw._writer.protocol._paused = True
            monkeypatch.setattr(raw._writer.protocol, "_drain_helper", held_drain)
        sending = asyncio.create_task(_tracked_send(upstream, "explicit", lambda: dispatched.append("explicit")))
        try:
            await asyncio.wait_for(drain_started.wait(), 2)
            message = await asyncio.wait_for(upstream.receive(), 2)
            assert message.text == "explicit"
            assert dispatched == ["explicit"]
            assert not sending.done()
            if cancel:
                sending.cancel()
            finish_drain.set()
            with suppress(asyncio.CancelledError):
                await sending
            await upstream.send_text("untracked")
            assert (await asyncio.wait_for(upstream.receive(), 2)).text == "untracked"
            assert dispatched == ["explicit"]
        finally:
            finish_drain.set()
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)
            if kind == "aiohttp":
                raw._writer.protocol._paused = False


class _HeldCompressor(ZLibCompressor):
    def __init__(self, window_bits: int) -> None:
        super().__init__(wbits=-window_bits)
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()

    async def compress(self, data: Any) -> bytes:
        self.started.set()
        await self.proceed.wait()
        return await super().compress(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_websockets_waiting_for_prior_send_cannot_claim_ping_or_late_send(cancel: bool) -> None:
    async with _connected_adapter("websockets") as (upstream, raw):
        prior_send = asyncio.get_running_loop().create_future()
        raw.send_in_progress = prior_send
        dispatched: list[str] = []
        sending = asyncio.create_task(_tracked_send(upstream, "explicit", lambda: dispatched.append("explicit")))
        try:
            await asyncio.sleep(0)
            pong = await raw.ping(b"unrelated control")
            await asyncio.wait_for(pong, 2)
            assert not sending.done()
            assert dispatched == []
            if cancel:
                sending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await sending
            raw.send_in_progress = None
            prior_send.set_result(None)
            if not cancel:
                await asyncio.wait_for(sending, 2)
                assert (await asyncio.wait_for(upstream.receive(), 2)).text == "explicit"
            await _tracked_send(upstream, "next", lambda: dispatched.append("next"))
            assert (await asyncio.wait_for(upstream.receive(), 2)).text == "next"
            assert dispatched == (["next"] if cancel else ["explicit", "next"])
        finally:
            raw.send_in_progress = None
            if not prior_send.done():
                prior_send.set_result(None)
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_aiohttp_async_compression_retains_exact_send_ownership(cancel: bool) -> None:
    async with _connected_adapter("aiohttp") as (upstream, raw):
        compressor = _HeldCompressor(raw._writer.compress)
        raw._writer._compressobj = compressor
        await upstream.send_text("parent")
        assert (await asyncio.wait_for(upstream.receive(), 2)).text == "parent"
        dispatched: list[str] = []
        payload = "explicit" * 4096
        sending = asyncio.create_task(_tracked_send(upstream, payload, lambda: dispatched.append("explicit")))
        sending_next: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(compressor.started.wait(), 2)
            assert dispatched == []
            # Ping bypasses the compressor lock on this same real connection.
            await raw.ping(b"unrelated control")
            assert dispatched == []
            if cancel:
                sending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await sending
            sending_next = asyncio.create_task(_tracked_send(upstream, "next", lambda: dispatched.append("next")))
            await asyncio.sleep(0)
            assert dispatched == []
            compressor.proceed.set()
            # aiohttp shields compression and may finish writing after caller
            # cancellation. It must never notify the expired send scope.
            message = await asyncio.wait_for(upstream.receive(), 2)
            assert message.text == payload
            if not cancel:
                await asyncio.wait_for(sending, 2)
            await asyncio.wait_for(sending_next, 2)
            assert (await asyncio.wait_for(upstream.receive(), 2)).text == "next"
            assert dispatched == (["next"] if cancel else ["explicit", "next"])
        finally:
            compressor.proceed.set()
            sending.cancel()
            if sending_next is not None:
                sending_next.cancel()
            await asyncio.gather(
                sending,
                *([sending_next] if sending_next is not None else []),
                *raw._writer._background_tasks,
                return_exceptions=True,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["websockets", "aiohttp"])
async def test_concurrent_connections_do_not_claim_each_others_writes(kind: str) -> None:
    async with _connected_adapter(kind) as (first, _):
        async with _connected_adapter(kind) as (second, _):
            dispatched: list[str] = []
            await asyncio.gather(
                _tracked_send(first, "first", lambda: dispatched.append("first")),
                _tracked_send(second, "second", lambda: dispatched.append("second")),
            )
            assert sorted(dispatched) == ["first", "second"]
            assert (await asyncio.wait_for(first.receive(), 2)).text == "first"
            assert (await asyncio.wait_for(second.receive(), 2)).text == "second"


@pytest.mark.asyncio
async def test_archiving_preserves_scope_without_dispatching_archive_io(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _connected_adapter("websockets") as (upstream, _):
        archive_transport = WebSocketDispatchTransport(_RecordingTransport())
        dispatched: list[str] = []

        def archive(**_kwargs: Any) -> None:
            archive_transport.write(Frame(Opcode.TEXT, b"archive").serialize(mask=True))
            assert dispatched == []

        monkeypatch.setattr("app.core.clients.proxy_websocket.archive_text", archive)
        archived = ArchivingUpstreamWebSocket(upstream, url="ws://localhost", headers={}, account_id="account")
        await _tracked_send(archived, "explicit", lambda: dispatched.append("explicit"))
        assert (await asyncio.wait_for(archived.receive(), 2)).text == "explicit"
        assert dispatched == ["explicit"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["websockets", "aiohttp"])
async def test_adapter_double_without_transport_never_infers_handoff(kind: str) -> None:
    class SendDouble:
        async def send(self, _text: str) -> None:
            return None

        async def send_str(self, _text: str) -> None:
            return None

    double = SendDouble()
    upstream = (
        WebsocketsUpstreamWebSocket(cast(Any, double)) if kind == "websockets" else CodexUpstreamWebSocket(double)
    )
    dispatched: list[str] = []
    await _tracked_send(upstream, "explicit", lambda: dispatched.append("explicit"))
    assert dispatched == []
