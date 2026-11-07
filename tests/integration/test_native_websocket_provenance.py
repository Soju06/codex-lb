from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from app.core.clients.native_egress import (
    NativeEgressTransportError,
    NativeWebSocketRequest,
    SubprocessNativeEgressClient,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("ending,phase", [("reset", "transport"), ("invalid_frame", "protocol")])
async def test_native_websocket_reset_and_invalid_frame_provenance(ending: str, phase: str) -> None:
    helper = os.environ.get("CODEX_LB_TEST_NATIVE_HELPER")
    if not helper:
        pytest.skip("set CODEX_LB_TEST_NATIVE_HELPER to the candidate-built worker")

    async def upstream(socket):
        await socket.recv()
        if ending == "reset":
            socket.transport.abort()
        else:
            # FIN + reserved opcode 3 is invalid independently of closing state.
            socket.transport.write(bytes([0x83, 0]))
            await socket.wait_closed()

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = SubprocessNativeEgressClient(Path(helper))
        try:
            socket = await client.websocket(
                NativeWebSocketRequest(
                    url=f"ws://127.0.0.1:{port}",
                    headers={},
                    connect_timeout_seconds=2,
                    max_message_bytes=1024,
                )
            )
            await socket.send_text("start")
            with pytest.raises(NativeEgressTransportError) as caught:
                await asyncio.wait_for(socket.receive(), 2)
            assert caught.value.failure_phase == phase
            assert not caught.value.retryable_same_contract
        finally:
            await client.aclose()
