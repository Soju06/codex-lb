from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import cast

import pytest
from websockets.asyncio.server import serve

from app.core.clients.native_egress import NativeEgressWebSocket, NativeWebSocketRequest, SubprocessNativeEgressClient
from app.core.clients.proxy_websocket import NativeUpstreamWebSocket
from app.modules.proxy._service.websocket.helpers import _upstream_websocket_disconnect_message

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["drain", "close", "cancel"])
async def test_native_websocket_burst_preserves_frames_and_peer_progress(finish: str) -> None:
    helper = os.environ.get("CODEX_LB_TEST_NATIVE_HELPER")
    if not helper:
        pytest.skip("set CODEX_LB_TEST_NATIVE_HELPER to the candidate-built worker")
    burst_sent = asyncio.Event()
    frames = [json.dumps({"type": "response.output_text.delta", "delta": str(index)}) for index in range(99)]
    frames.append(json.dumps({"type": "response.completed", "response": {"id": "resp_burst"}}))

    async def upstream(socket):
        command = await socket.recv()
        if command == "burst":
            for frame in frames:
                await socket.send(frame)
            burst_sent.set()
        else:
            await socket.send("peer")
        await socket.wait_closed()

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = SubprocessNativeEgressClient(Path(helper))
        request = NativeWebSocketRequest(
            url=f"ws://127.0.0.1:{port}",
            headers={},
            connect_timeout_seconds=2,
            max_message_bytes=1024,
            interpret_responses=True,
            ping_interval_seconds=0.02,
            ping_timeout_seconds=0.02,
        )
        try:
            socket = await client.websocket(request)
            await socket.send_text("burst")
            await asyncio.wait_for(burst_sent.wait(), 2)
            # The old pump drains all 100 into a 64-message queue and aborts.
            # Also exceed several ping deadlines while locally paused.
            await asyncio.sleep(0.15)
            peer = await client.websocket(request)
            await asyncio.wait_for(peer.send_text("echo"), 2)
            assert (await asyncio.wait_for(peer.receive(), 2)).text == "peer"
            await asyncio.wait_for(peer.close(), 2)
            if finish == "drain":
                messages = [(await asyncio.wait_for(socket.receive(), 2)).text for _ in range(100)]
                assert messages == frames
                await asyncio.wait_for(socket.close(), 2)
            elif finish == "close":
                await asyncio.wait_for(socket.close(), 2)
                assert (await socket.receive()).text == frames[0]
            else:
                await asyncio.wait_for(client.aclose(), 2)
            assert not client._streams
        finally:
            await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["consumer_backpressure", "helper_exit", "transport", "liveness_timeout"])
async def test_native_failure_detail_reaches_client_and_logs(phase: str, caplog) -> None:
    from app.core.clients.native_egress import NativeEgressTransportError, NativeWebSocketMessage

    class Socket:
        def __init__(self):
            self.messages = iter(
                [NativeWebSocketMessage(kind="text", text="é"), NativeWebSocketMessage(kind="binary", data=b"abc")]
            )

        async def receive(self):
            message = next(self.messages, None)
            if message is None:
                raise NativeEgressTransportError("native transport failure", failure_phase=phase)
            return message

    socket = NativeUpstreamWebSocket(cast(NativeEgressWebSocket, Socket()))
    await socket.receive()
    await socket.receive()
    message = await socket.receive()
    client_detail = _upstream_websocket_disconnect_message(message)
    for detail in (f"phase={phase}", "close_code=None", "close_reason=None", "received_events=2", "received_bytes=5"):
        assert detail in client_detail
        assert detail in caplog.text


@pytest.mark.asyncio
async def test_native_close_reason_reaches_client() -> None:
    from app.core.clients.native_egress import NativeWebSocketMessage

    class Socket:
        async def receive(self):
            return NativeWebSocketMessage(kind="close", close_code=1012, close_reason="server restart")

    socket = NativeUpstreamWebSocket(cast(NativeEgressWebSocket, Socket()))
    message = await socket.receive()
    detail = _upstream_websocket_disconnect_message(message)
    assert "close_code=1012" in detail
    assert "server restart" in detail
    assert "received_events=0 received_bytes=0" in detail


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["failure", "cancel"])
async def test_credit_return_recovers_without_duplicate_dispatch(
    interruption: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = os.environ.get("CODEX_LB_TEST_NATIVE_HELPER")
    if not helper:
        pytest.skip("set CODEX_LB_TEST_NATIVE_HELPER to the candidate-built worker")

    async def upstream(socket):
        for text in ("one", "two", "three"):
            await socket.send(text)
        await socket.wait_closed()

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = SubprocessNativeEgressClient(Path(helper))
        socket = await client.websocket(
            NativeWebSocketRequest(
                url=f"ws://127.0.0.1:{port}",
                headers={},
                connect_timeout_seconds=2,
                max_message_bytes=1024,
            )
        )
        original = client._send_command
        sent = asyncio.Event()
        release = asyncio.Event()
        credit_calls = 0

        async def interrupted(process, generation, command):
            nonlocal credit_calls
            if command.get("type") == "websocket_consumed":
                credit_calls += 1
                await original(process, generation, command)
                if credit_calls == 1 and interruption == "failure":
                    raise RuntimeError("injected post-write drain failure")
                if credit_calls == 1 and interruption == "cancel":
                    sent.set()
                    await release.wait()
                return
            await original(process, generation, command)

        try:
            assert (await asyncio.wait_for(socket.receive(), 2)).text == "one"
            monkeypatch.setattr(client, "_send_command", interrupted)
            if interruption == "failure":
                with pytest.raises(Exception, match="receive credit dispatch failed"):
                    await socket.receive()
                with pytest.raises(Exception, match="receive credit dispatch failed"):
                    await socket.receive()
                assert credit_calls == 1
                peer = await client.websocket(
                    NativeWebSocketRequest(
                        url=f"ws://127.0.0.1:{port}", headers={}, connect_timeout_seconds=2, max_message_bytes=1024
                    )
                )
                assert (await asyncio.wait_for(peer.receive(), 2)).text == "one"
                await peer.close()
            else:
                receiving = asyncio.create_task(socket.receive())
                await asyncio.wait_for(sent.wait(), 2)
                receiving.cancel()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await receiving
                assert (await asyncio.wait_for(socket.receive(), 2)).text == "two"
                assert credit_calls == 1
                assert (await asyncio.wait_for(socket.receive(), 2)).text == "three"
        finally:
            await client.aclose()
