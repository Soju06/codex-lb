"""End-to-end large-payload IPC through the REAL native helper process.

Drives the actual ``codex-lb-native-egress`` worker binary and the Python
``SubprocessNativeEgressClient`` (no mocks, no external endpoints — the
"upstream" is a local ``websockets`` server): a >24 MiB upstream message
crosses the bounded stdout IPC boundary as chunked ``websocket_text`` events
and reassembles exactly in the wrapper, a >24 MiB client send crosses stdin
as one command, and an over-budget receive fails through the configured
websocket cap (the `websocket_text_chunking_v1` pair contract of
allow-bounded-inline-images-on-bridge).

The helper binary is located via ``CODEX_LB_NATIVE_EGRESS_TEST_BINARY`` (the
name CI exports), ``CODEX_LB_NATIVE_EGRESS_TEST_BIN`` or ``PATH``; without it
the suite skips (CI builds the binary and exports the env var before this
suite).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from typing import Any

import pytest
from websockets.asyncio.server import serve as websockets_serve

from app.core.clients.native_egress import (
    NativeEgressTransportError,
    NativeWebSocketRequest,
    SubprocessNativeEgressClient,
)


def _helper_executable() -> str | None:
    for name in ("CODEX_LB_NATIVE_EGRESS_TEST_BINARY", "CODEX_LB_NATIVE_EGRESS_TEST_BIN"):
        configured = os.environ.get(name)
        if configured and os.path.isfile(configured) and os.access(configured, os.X_OK):
            return configured
    return shutil.which("codex-lb-native-egress")


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_helper_executable() is None, reason="native egress helper binary not available"),
]

_LARGE_CAP = 64 * 1024 * 1024
# Comfortably above the 24 MiB Python readline IPC limit — the shape that
# fails with "Separator is found, but chunk is longer than limit" before
# chunking existed.
_OVER_LINE_LIMIT_BYTES = 25 * 1024 * 1024 + 1337


def _big_text(size: int) -> str:
    # Deterministic ASCII payload (valid JSON string content, no escapes) so
    # byte-exact reassembly is checkable by hash. Synthetic data only.
    block = "0123456789abcdef"
    repeats = size // len(block) + 1
    return (block * repeats)[:size]


@pytest.fixture
async def helper_client():
    executable = _helper_executable()
    assert executable is not None
    client = SubprocessNativeEgressClient(executable)
    try:
        yield client
    finally:
        await client.aclose()


async def _connect(
    helper_client: SubprocessNativeEgressClient,
    port: int,
    *,
    max_message_bytes: int,
    interpret_responses: bool,
):
    return await helper_client.websocket(
        NativeWebSocketRequest(
            url=f"ws://127.0.0.1:{port}/v1/responses",
            headers={},
            connect_timeout_seconds=5.0,
            max_message_bytes=max_message_bytes,
            ping_interval_seconds=None,
            ping_timeout_seconds=None,
            proxy_url=None,
            interpret_responses=interpret_responses,
        )
    )


def _server_port(server: Any) -> int:
    return server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_helper_receives_and_reassembles_oversize_ipc_message(
    helper_client: SubprocessNativeEgressClient,
) -> None:
    big_text = _big_text(_OVER_LINE_LIMIT_BYTES)
    big_digest = hashlib.sha256(big_text.encode("utf-8")).hexdigest()
    small_event = json.dumps(
        {"type": "response.created", "response": {"id": "resp_ipc", "status": "in_progress"}},
        separators=(",", ":"),
    )

    async def handler(connection: Any) -> None:
        # Small interpreted-shape event first, then the oversize opaque
        # message: both must arrive through the one IPC boundary.
        await connection.send(small_event)
        await connection.send(big_text)

    async with websockets_serve(handler, "127.0.0.1", 0, max_size=_LARGE_CAP) as server:
        websocket = await _connect(
            helper_client, _server_port(server), max_message_bytes=_LARGE_CAP, interpret_responses=True
        )
        try:
            interpreted = await asyncio.wait_for(websocket.receive(), timeout=10.0)
            assert interpreted.kind == "text"
            assert interpreted.responses_interpreted is True
            assert interpreted.event_type == "response.created"
            assert interpreted.routing is not None
            assert interpreted.routing.payload_response_id == "resp_ipc"

            oversize = await asyncio.wait_for(websocket.receive(), timeout=30.0)
            assert oversize.kind == "text"
            # The wrapper delivered the COMPLETE message: exact length and
            # exact bytes by hash, proving chunk reassembly across the
            # >24 MiB readline boundary.
            assert len(oversize.text) == _OVER_LINE_LIMIT_BYTES
            assert hashlib.sha256(oversize.text.encode("utf-8")).hexdigest() == big_digest
        finally:
            await websocket.close()


@pytest.mark.asyncio
async def test_helper_sends_oversize_command_line(helper_client: SubprocessNativeEgressClient) -> None:
    # The send direction: a >24 MiB response.create-shaped frame leaves the
    # Python side as one stdin command line and must arrive at the local
    # server byte-exact (tokio's line reader is unbounded by design).
    big_frame = json.dumps(
        {"type": "response.create", "input": [{"type": "message", "data": _big_text(_OVER_LINE_LIMIT_BYTES)}]},
        separators=(",", ":"),
    )
    received: list[str] = []

    async def handler(connection: Any) -> None:
        received.append(await connection.recv())

    async with websockets_serve(handler, "127.0.0.1", 0, max_size=_LARGE_CAP) as server:
        websocket = await _connect(
            helper_client, _server_port(server), max_message_bytes=_LARGE_CAP, interpret_responses=False
        )
        try:
            await asyncio.wait_for(websocket.send_text(big_frame), timeout=60.0)
            deadline = asyncio.get_running_loop().time() + 30.0
            while not received and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
            assert received, "server did not receive the oversize frame"
            assert received[0] == big_frame
        finally:
            await websocket.close()


@pytest.mark.asyncio
async def test_helper_rejects_receive_over_configured_budget(helper_client: SubprocessNativeEgressClient) -> None:
    # The configured websocket message cap is enforced by the helper: a
    # message over ``max_message_bytes`` fails the connection through the
    # typed transport error instead of being delivered.
    async def handler(connection: Any) -> None:
        await connection.send(_big_text(2 * 1024 * 1024))

    async with websockets_serve(handler, "127.0.0.1", 0, max_size=_LARGE_CAP) as server:
        websocket = await _connect(
            helper_client, _server_port(server), max_message_bytes=1024 * 1024, interpret_responses=False
        )
        try:
            with pytest.raises(NativeEgressTransportError):
                await asyncio.wait_for(websocket.receive(), timeout=30.0)
        finally:
            await websocket.close()
