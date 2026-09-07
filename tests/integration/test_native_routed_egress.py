from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Any, cast

import aiohttp
import pytest
from websockets.asyncio.server import serve as websocket_serve

from app.core.clients.codex import CodexClient
from app.core.clients.native_egress import (
    SubprocessNativeEgressClient,
    close_discovered_native_egress_client,
    discover_native_egress_client,
)
from app.core.clients.proxy import stream_responses
from app.core.clients.proxy_websocket import (
    _RESPONSES_WEBSOCKET_POLICY,
    _connect_upstream_websocket,
)
from app.core.openai.requests import ResponsesRequest
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
async def test_direct_sse_and_routed_http_websocket_share_native_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper_value = os.environ.get("CODEX_LB_NATIVE_EGRESS_TEST_BINARY")
    if not helper_value:
        pytest.skip("set CODEX_LB_NATIVE_EGRESS_TEST_BINARY to run the native route wire probe")
    helper = Path(helper_value)
    if not helper.is_file():
        pytest.skip(f"native helper is unavailable: {helper}")
    access_token = secrets.token_urlsafe(32)

    proxy_hits: list[str] = []
    http_bodies: list[bytes] = []
    direct_hits: list[str] = []
    direct_bodies: list[bytes] = []

    async def direct_http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        direct_hits.append(head.split(b"\r\n", 1)[0].decode("ascii"))
        content_length = 0
        for line in head.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if name.lower() == b"content-length":
                content_length = int(value.strip())
        direct_bodies.append(await reader.readexactly(content_length))
        body = b'data: {"type":"response.completed","response":{"id":"resp_direct"}}\n\n'
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
            body = b'data: {"type":"response.completed","response":{"id":"resp_wire"}}\n\n'
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
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
        await close_discovered_native_egress_client()
        monkeypatch.setenv("PATH", f"{helper.parent}{os.pathsep}{os.environ.get('PATH', '')}")
        native = discover_native_egress_client()
        assert isinstance(native, SubprocessNativeEgressClient)
        python_session = _UnexpectedPythonSession()
        client = CodexClient(python_session)
        try:
            direct_events = [
                event
                async for event in stream_responses(
                    ResponsesRequest(
                        model="gpt-5.4",
                        instructions="",
                        input="direct probe",
                        stream=True,
                    ),
                    {},
                    access_token,
                    "account-1",
                    base_url=f"http://127.0.0.1:{direct_port}",
                    upstream_stream_transport_override="http",
                    allow_direct_egress=False,
                    suppress_live_usage=True,
                    session=cast(aiohttp.ClientSession, python_session),
                )
            ]
            assert direct_events == ['data: {"type":"response.completed","response":{"id":"resp_direct"}}\n\n']
            helper_process = native._process

            routed_events = [
                event
                async for event in stream_responses(
                    ResponsesRequest(
                        model="gpt-5.4",
                        instructions="",
                        input="probe",
                        stream=True,
                    ),
                    {},
                    access_token,
                    "account-1",
                    base_url="http://upstream.invalid",
                    upstream_stream_transport_override="http",
                    route=route,
                    codex_client=client,
                    allow_direct_egress=False,
                    suppress_live_usage=True,
                    session=cast(aiohttp.ClientSession, python_session),
                )
            ]
            assert routed_events == ['data: {"type":"response.completed","response":{"id":"resp_wire"}}\n\n']
            assert native._process is helper_process

            websocket = await _connect_upstream_websocket(
                {},
                access_token,
                "account-1",
                url=f"ws://127.0.0.1:{websocket_port}/v1/responses",
                route=route,
                codex_client=client,
                policy=_RESPONSES_WEBSOCKET_POLICY,
            )
            assert getattr(websocket, "upstream_proxy_endpoint_id", None) == "proxy-1"
            assert getattr(websocket, "upstream_proxy_fallback_used", None) is False
            await websocket.send_text("probe")
            message = await websocket.receive()
            assert message.kind == "text"
            assert message.text == "echo:probe"
            await websocket.close()

            assert native._process is helper_process
            assert helper_process is not None and helper_process.returncode is None
            assert direct_hits == ["POST /codex/responses HTTP/1.1"]
            assert len(direct_bodies) == 1
            direct_body = json.loads(direct_bodies[0])
            assert direct_body["model"] == "gpt-5.4"
            assert direct_body["input"]
            assert len(http_bodies) == 1
            routed_body = json.loads(http_bodies[0])
            assert routed_body["model"] == "gpt-5.4"
            assert routed_body["input"]
            assert proxy_hits[0].startswith("POST http://upstream.invalid/codex/responses ")
            assert any(hit.startswith("CONNECT ") for hit in proxy_hits[1:])
        finally:
            proxy_server.close()
            await proxy_server.wait_closed()
            await close_discovered_native_egress_client()
            direct_server.close()
            await direct_server.wait_closed()
