from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import serve as websocket_serve

from app.core.clients.codex import CodexClient
from app.core.clients.native_egress import (
    NativeEgressRequest,
    NativeWebSocketMessage,
    SubprocessNativeEgressClient,
)
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute


class _UnexpectedPythonSession:
    async def request(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("native routed HTTP must not use aiohttp")

    def ws_connect(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("native routed websocket must not use aiohttp")


async def _copy_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_direct_sse_and_routed_http_websocket_share_native_helper() -> None:
    helper_value = os.environ.get("CODEX_LB_NATIVE_EGRESS_TEST_BINARY")
    if not helper_value:
        pytest.skip("set CODEX_LB_NATIVE_EGRESS_TEST_BINARY to run the native route wire probe")
    helper = Path(helper_value)
    if not helper.is_file():
        pytest.skip(f"native helper is unavailable: {helper}")

    proxy_hits: list[str] = []
    http_bodies: list[bytes] = []
    direct_hits: list[str] = []

    async def direct_http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        direct_hits.append(head.split(b"\r\n", 1)[0].decode("ascii"))
        body = b'data: {"type":"response.created"}\n\ndata: [DONE]\n\n'
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        midpoint = len(body) // 2
        writer.write(body[:midpoint])
        await writer.drain()
        await asyncio.sleep(0)
        writer.write(body[midpoint:])
        await writer.drain()
        writer.close()

    direct_server = await asyncio.start_server(direct_http_handler, "127.0.0.1", 0)
    direct_port = direct_server.sockets[0].getsockname()[1]

    async def websocket_handler(websocket: Any) -> None:
        message = await websocket.recv()
        await websocket.send(f"echo:{message}")

    async with websocket_serve(websocket_handler, "127.0.0.1", 0) as websocket_server:
        websocket_port = websocket_server.sockets[0].getsockname()[1]

        async def proxy_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            head = await reader.readuntil(b"\r\n\r\n")
            first_line = head.split(b"\r\n", 1)[0].decode("ascii")
            proxy_hits.append(first_line)
            if first_line.startswith("CONNECT "):
                target_reader, target_writer = await asyncio.open_connection("127.0.0.1", websocket_port)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await asyncio.gather(
                    _copy_stream(reader, target_writer),
                    _copy_stream(target_reader, writer),
                )
                return
            content_length = 0
            for line in head.split(b"\r\n")[1:]:
                name, _, value = line.partition(b":")
                if name.lower() == b"content-length":
                    content_length = int(value.strip())
            http_bodies.append(await reader.readexactly(content_length))
            body = b'{"ok":true}'
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
            writer.close()

        proxy_server = await asyncio.start_server(proxy_handler, "127.0.0.1", 0)
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        route = ResolvedUpstreamRoute(
            mode="account_bound",
            pool_id="wire-probe",
            endpoint=ResolvedProxyEndpoint("proxy-1", "http", "127.0.0.1", proxy_port),
        )
        native = SubprocessNativeEgressClient(helper)
        client = CodexClient(_UnexpectedPythonSession(), native_egress_client=native)
        try:
            direct_response = await native.request(
                NativeEgressRequest(
                    method="GET",
                    url=f"http://127.0.0.1:{direct_port}/v1/responses",
                    headers={"accept": "text/event-stream"},
                    timeout_seconds=2,
                )
            )
            assert await direct_response.read() == (b'data: {"type":"response.created"}\n\ndata: [DONE]\n\n')
            helper_process = native._process

            http_result = await client.request_with_route_metadata(
                "POST",
                "http://upstream.invalid/v1/responses",
                route=route,
                json={"input": "probe"},
                timeout=2,
            )
            assert await http_result.response.read() == b'{"ok":true}'
            assert native._process is helper_process

            websocket_result = await client.open_ws_with_route_metadata(
                f"ws://127.0.0.1:{websocket_port}/v1/responses",
                route=route,
                timeout=2,
                max_msg_size=1024,
            )
            assert websocket_result.native is True
            await websocket_result.websocket.send_text("probe")
            message = await websocket_result.websocket.receive()
            assert message == NativeWebSocketMessage(kind="text", text="echo:probe")
            await websocket_result.websocket.close()

            assert native._process is helper_process
            assert helper_process is not None and helper_process.returncode is None
            assert direct_hits == ["GET /v1/responses HTTP/1.1"]
            assert http_bodies == [b'{"input":"probe"}']
            assert proxy_hits[0].startswith("POST http://upstream.invalid/v1/responses ")
            assert any(hit.startswith("CONNECT ") for hit in proxy_hits[1:])
        finally:
            proxy_server.close()
            await proxy_server.wait_closed()
            await native.aclose()
            direct_server.close()
            await direct_server.wait_closed()
