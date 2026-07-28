from __future__ import annotations

import asyncio
import json
import signal
import socket
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class _RunningServer:
    process: asyncio.subprocess.Process
    http_url: str
    websocket_url: str


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_until_ready(server: _RunningServer) -> None:
    async with httpx.AsyncClient(timeout=0.2) as client:
        for _ in range(100):
            if server.process.returncode is not None:
                output = await server.process.stdout.read() if server.process.stdout is not None else b""
                raise AssertionError(f"fixture server exited early: {output.decode(errors='replace')}")
            try:
                response = await client.get(f"{server.http_url}/internal/drain/status")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.02)
    raise AssertionError("fixture server did not become ready")


async def _wait_until_draining(server: _RunningServer) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=0.2) as client:
        for _ in range(100):
            response = await client.get(f"{server.http_url}/internal/drain/status")
            checks = response.json()["checks"]
            if checks["draining"] == "true":
                return checks
            await asyncio.sleep(0.01)
    raise AssertionError("fixture server did not expose the SIGTERM drain barrier")


@asynccontextmanager
async def _run_server(
    *,
    mode: str,
    drain_timeout_seconds: float,
    completion_delay_seconds: float = 0.0,
    post_drain_cleanup_timeout_seconds: float = 25.0,
) -> AsyncIterator[_RunningServer]:
    port = _unused_local_port()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.fixtures.graceful_websocket_server",
        "--port",
        str(port),
        "--mode",
        mode,
        "--drain-timeout-seconds",
        str(drain_timeout_seconds),
        "--completion-delay-seconds",
        str(completion_delay_seconds),
        "--post-drain-cleanup-timeout-seconds",
        str(post_drain_cleanup_timeout_seconds),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    server = _RunningServer(
        process=process,
        http_url=f"http://127.0.0.1:{port}",
        websocket_url=f"ws://127.0.0.1:{port}/v1/responses",
    )
    try:
        await _wait_until_ready(server)
        yield server
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.kill()
                await process.wait()


@pytest.mark.asyncio
async def test_sigterm_delivers_terminal_before_close_and_rejects_late_websocket() -> None:
    async with _run_server(
        mode="complete",
        drain_timeout_seconds=2.0,
        completion_delay_seconds=0.25,
    ) as server:
        async with connect(server.websocket_url) as websocket:
            await websocket.send(json.dumps({"type": "response.create"}))
            created = json.loads(await websocket.recv())
            assert created["type"] == "response.created"

            server.process.send_signal(signal.SIGTERM)
            checks = await _wait_until_draining(server)
            assert checks["in_flight"] == "1"

            with pytest.raises(InvalidStatus):
                async with connect(server.websocket_url):
                    pytest.fail("late WebSocket admission unexpectedly succeeded")

            terminal = json.loads(await websocket.recv())
            assert terminal["type"] == "response.completed"
            with pytest.raises(ConnectionClosed):
                await websocket.recv()
            assert websocket.close_code == 1012

        assert await asyncio.wait_for(server.process.wait(), timeout=2) == -signal.SIGTERM


@pytest.mark.asyncio
async def test_sigterm_bounds_stuck_websocket_turn() -> None:
    async with _run_server(mode="stuck", drain_timeout_seconds=0.25) as server:
        async with connect(server.websocket_url) as websocket:
            await websocket.send(json.dumps({"type": "response.create"}))
            created = json.loads(await websocket.recv())
            assert created["type"] == "response.created"

            started_at = time.monotonic()
            server.process.send_signal(signal.SIGTERM)
            with pytest.raises(ConnectionClosed):
                await websocket.recv()
            assert await asyncio.wait_for(server.process.wait(), timeout=2) == -signal.SIGTERM
            assert time.monotonic() - started_at < 1.5


@pytest.mark.asyncio
async def test_sigterm_exits_when_lifespan_absorbs_cleanup_cancellation() -> None:
    async with _run_server(
        mode="cleanup_stuck",
        drain_timeout_seconds=0.1,
        post_drain_cleanup_timeout_seconds=0.1,
    ) as server:
        started_at = time.monotonic()
        server.process.send_signal(signal.SIGTERM)

        assert await asyncio.wait_for(server.process.wait(), timeout=2) == -signal.SIGTERM
        assert time.monotonic() - started_at < 1.5


@pytest.mark.asyncio
async def test_sigint_exits_when_lifespan_absorbs_cleanup_cancellation() -> None:
    async with _run_server(
        mode="cleanup_stuck",
        drain_timeout_seconds=0.1,
        post_drain_cleanup_timeout_seconds=0.1,
    ) as server:
        started_at = time.monotonic()
        server.process.send_signal(signal.SIGINT)

        assert await asyncio.wait_for(server.process.wait(), timeout=2) == -signal.SIGINT
        assert time.monotonic() - started_at < 1.5


@pytest.mark.asyncio
async def test_prestop_commits_deadline_before_sigterm_and_cannot_reopen() -> None:
    async with _run_server(
        mode="complete",
        drain_timeout_seconds=2.0,
        completion_delay_seconds=0.25,
    ) as server:
        async with connect(server.websocket_url) as websocket:
            await websocket.send(json.dumps({"type": "response.create"}))
            assert json.loads(await websocket.recv())["type"] == "response.created"

            prestop_process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.core.prestop",
                "--base-url",
                server.http_url,
                "--routing-dwell-seconds",
                "0.1",
                "--drain-timeout-seconds",
                "1.5",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            async with httpx.AsyncClient(timeout=0.5) as client:
                checks = await _wait_until_draining(server)
                assert checks["shutdown_committed"] == "true"
                assert checks["in_flight"] == "1"
                assert prestop_process.returncode is None

                stop_response = await client.post(f"{server.http_url}/internal/drain/stop")
            assert stop_response.status_code == 409

            with pytest.raises(InvalidStatus):
                async with connect(server.websocket_url):
                    pytest.fail("late WebSocket admission unexpectedly succeeded")

            terminal = json.loads(await websocket.recv())
            assert terminal["type"] == "response.completed"
            with pytest.raises(ConnectionClosed):
                await websocket.recv()

            assert await asyncio.wait_for(prestop_process.wait(), timeout=2) == 0
            server.process.send_signal(signal.SIGTERM)

        assert await asyncio.wait_for(server.process.wait(), timeout=2) == -signal.SIGTERM
