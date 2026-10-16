from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import signal
import socket
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import pytest
import zstandard
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL

from app.desktop_bridge import BridgeConfig, create_bridge_app, relay

pytestmark = pytest.mark.integration

CLIENT_TOKEN = "synthetic-desktop-token"
LB_TOKEN = "synthetic-lb-token"
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def write_private(path: Path, content: str) -> None:
    """Write a synthetic credential file and restrict access to its owner."""
    path.write_text(content)
    path.chmod(0o600)


def write_auth(path: Path, token: str = CLIENT_TOKEN) -> None:
    """Create owner-only ChatGPT auth JSON containing a synthetic access token."""
    write_private(path, json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": token}}))


def bound_socket() -> socket.socket:
    """Reserve an ephemeral IPv4 loopback port on a nonblocking socket."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    return sock


@asynccontextmanager
async def serve(app: web.Application, sock: socket.socket) -> AsyncIterator[None]:
    """Serve an aiohttp app on the supplied socket and always clean up both resources."""
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True, shutdown_timeout=1)
    await runner.setup()
    try:
        await web.SockSite(runner, sock).start()
        yield
    finally:
        await runner.cleanup()
        sock.close()


@dataclass
class BridgeHarness:
    """Expose the running bridge client, URL, and credential paths to integration tests."""

    client: aiohttp.ClientSession
    url: str
    auth_file: Path
    token_file: Path


@pytest.fixture
def bridge(tmp_path: Path):
    """Provide an isolated bridge factory backed by temporary credentials and a local LB stub."""

    @asynccontextmanager
    async def start(handler: Handler) -> AsyncIterator[BridgeHarness]:
        """Run the bridge and supplied upstream handler, yielding an authenticated test client."""
        auth_file = tmp_path / "auth.json"
        token_file = tmp_path / "lb-token"
        write_auth(auth_file)
        write_private(token_file, LB_TOKEN + "\n")
        lb_sock, bridge_sock = bound_socket(), bound_socket()
        lb_port, bridge_port = lb_sock.getsockname()[1], bridge_sock.getsockname()[1]
        lb = web.Application(handler_args={"auto_decompress": False})
        lb.router.add_route("*", "/{tail:.*}", handler)
        config = BridgeConfig(listen_port=bridge_port, lb_port=lb_port, codex_home=tmp_path, lb_token_file=token_file)
        async with serve(lb, lb_sock), serve(create_bridge_app(config), bridge_sock):
            async with aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {CLIENT_TOKEN}"},
                auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as client:
                yield BridgeHarness(client, f"http://127.0.0.1:{bridge_port}", auth_file, token_file)

    return start


@pytest.mark.parametrize("path", ["/backend-api/codex", "/backend-api/codex/", "/backend-api/codex/responses"])
async def test_http_replaces_identity_preserves_metadata_and_duplicate_headers(bridge, path):
    """Verify bidirectional header filtering while preserving duplicate metadata, raw targets, and bodies."""

    async def upstream(request):
        """Assert sanitized LB-bound identity and payload, then return metadata mixed with private headers."""
        assert request.headers["Authorization"] == f"Bearer {LB_TOKEN}"
        for name in (
            "X-OpenAI-Actor-Authorization",
            "ChatGPT-Account-ID",
            "Cookie",
            "Proxy-Authorization",
            "Forwarded",
            "X-Forwarded-For",
            "X-Real-IP",
            "True-Client-IP",
            "CF-Connecting-IP",
            "X-Private-Hop",
        ):
            assert name not in request.headers
        assert request.headers.getall("X-Codex-Metadata") == ["one", "two"]
        assert request.raw_path == path + "?cursor=a%2Fb&cursor=c"
        assert await request.read() == b'{"synthetic":true}'
        return web.Response(
            body=b"ok",
            headers=CIMultiDict(
                [
                    ("X-Request-ID", "synthetic-request"),
                    ("X-Codex-Metadata", "a"),
                    ("X-Codex-Metadata", "b"),
                    ("Set-Cookie", "synthetic-cookie"),
                    ("Authorization", "synthetic-response-secret"),
                    ("Connection", "X-Private-Hop"),
                    ("X-Private-Hop", "hidden"),
                ]
            ),
        )

    headers = CIMultiDict(
        [
            ("X-OpenAI-Actor-Authorization", "synthetic-actor"),
            ("ChatGPT-Account-ID", "synthetic-account"),
            ("Cookie", "synthetic-cookie"),
            ("Proxy-Authorization", "synthetic-proxy"),
            ("Forwarded", "for=example.invalid"),
            ("X-Forwarded-For", "192.0.2.1"),
            ("X-Real-IP", "192.0.2.1"),
            ("True-Client-IP", "192.0.2.1"),
            ("CF-Connecting-IP", "192.0.2.1"),
            ("Connection", "X-Private-Hop"),
            ("X-Private-Hop", "hidden"),
            ("X-Codex-Metadata", "one"),
            ("X-Codex-Metadata", "two"),
        ]
    )
    async with bridge(upstream) as h:
        async with h.client.post(
            URL(h.url + path + "?cursor=a%2Fb&cursor=c", encoded=True), headers=headers, data=b'{"synthetic":true}'
        ) as response:
            assert response.status == 200
            assert await response.read() == b"ok"
            assert response.headers.getall("X-Codex-Metadata") == ["a", "b"]
            assert response.headers["X-Request-ID"] == "synthetic-request"
            for name in ("Set-Cookie", "Authorization", "X-Private-Hop"):
                assert name not in response.headers


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer wrong"},
        {"Authorization": ""},
        {"Host": "example.invalid"},
        {"Origin": "http://example.invalid"},
        [("Authorization", f"Bearer {CLIENT_TOKEN}"), ("Authorization", "other")],
    ],
)
async def test_rejected_identity_never_reaches_upstream(bridge, headers):
    """Reject invalid credentials, authorities, and origins before contacting the LB."""
    calls = []

    async def upstream(request):
        """Record any upstream dispatch so rejected requests cannot silently reach the LB."""
        calls.append(request.path)
        return web.Response()

    async with bridge(upstream) as h:
        async with h.client.get(h.url + "/backend-api/codex/models", headers=headers) as response:
            assert response.status == 403
    assert calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/backend-api/codexish",
        "/backend-api/codex//responses",
        "/backend-api/codex/%2e%2e/models",
        "/backend-api/codex/%252e%252e/models",
        "/backend-api/codex/responses%2fsecret",
        "/backend-api/codex/responses%5csecret",
    ],
)
async def test_invalid_routes_never_reach_upstream(bridge, path):
    """Reject non-Codex and ambiguous encoded paths before forwarding a request."""
    calls = []

    async def upstream(request):
        """Record any route that bypasses the bridge target policy."""
        calls.append(request.path)
        return web.Response()

    async with bridge(upstream) as h:
        async with h.client.get(URL(h.url + path, encoded=True)) as response:
            assert 400 <= response.status < 500 and response.status != 401
    assert calls == []


async def test_rotated_credentials_are_used_without_restart_and_never_written(bridge):
    """Reload rotated client and LB tokens, reject stale identity, and fail closed on invalid auth JSON."""
    received = []

    async def upstream(request):
        """Capture each forwarded bearer value to verify immediate LB token rotation."""
        received.append(request.headers["Authorization"])
        return web.Response(text="ok")

    async with bridge(upstream) as h:
        original = h.auth_file.read_bytes()
        async with h.client.get(h.url + "/backend-api/codex/models") as response:
            assert response.status == 200
        assert h.auth_file.read_bytes() == original
        write_auth(h.auth_file, "synthetic-rotated-client")
        write_private(h.token_file, "synthetic-rotated-lb")
        async with h.client.get(h.url + "/backend-api/codex/models") as response:
            assert response.status == 403
        async with h.client.get(
            h.url + "/backend-api/codex/models", headers={"Authorization": "Bearer synthetic-rotated-client"}
        ) as response:
            assert response.status == 200
        write_private(h.auth_file, "not-json")
        async with h.client.get(h.url + "/backend-api/codex/models") as response:
            assert response.status == 503
            assert str(h.auth_file) not in await response.text()
    assert received == [f"Bearer {LB_TOKEN}", "Bearer synthetic-rotated-lb"]


async def test_incomplete_credential_write_fails_closed_then_recovers_without_restart(bridge):
    """Return 503 during a partial auth rewrite and accept the completed replacement token."""
    received = []

    async def upstream(request):
        """Capture authorized dispatches to ensure incomplete credentials never reach the LB."""
        received.append(request.headers["Authorization"])
        return web.Response(text="ok")

    async with bridge(upstream) as h:
        # Codex may truncate and rewrite auth.json instead of replacing it atomically.
        with h.auth_file.open("w") as writer:
            writer.write('{"auth_mode":"chatgpt","tokens":')
            writer.flush()
            async with h.client.get(h.url + "/backend-api/codex/models") as response:
                assert response.status == 503
            writer.write('{"access_token":"synthetic-refreshed-token"}}')
            writer.flush()
        async with h.client.get(h.url + "/backend-api/codex/models") as response:
            assert response.status == 403
        async with h.client.get(
            h.url + "/backend-api/codex/models", headers={"Authorization": "Bearer synthetic-refreshed-token"}
        ) as response:
            assert response.status == 200
    assert received == [f"Bearer {LB_TOKEN}"]


@pytest.mark.parametrize("status,expected", [(401, 502), (301, 502), (307, 502), (403, 403), (426, 426), (429, 429)])
async def test_http_error_contract_has_no_refresh_or_redirect(bridge, status, expected):
    """Map upstream authentication and redirect failures without retrying or leaking their bodies."""
    calls = []

    async def upstream(request):
        """Return the selected failure and a redirect location while recording dispatch count."""
        calls.append(request.path)
        return web.Response(status=status, body=b"synthetic-upstream-error", headers={"Location": "/spy"})

    async with bridge(upstream) as h:
        async with h.client.post(h.url + "/backend-api/codex/responses", data=b"{}") as response:
            assert response.status == expected
            if expected == 502:
                assert b"synthetic-upstream-error" not in await response.read()
    assert calls == ["/backend-api/codex/responses"]


@pytest.mark.parametrize("encoding", ["gzip", "zstd"])
async def test_compressed_request_and_response_are_opaque(bridge, encoding):
    """Relay gzip and zstd payload bytes and encoding headers without decompression."""
    source = b'{"synthetic":"payload"}' * 100
    compressed = gzip.compress(source) if encoding == "gzip" else zstandard.ZstdCompressor().compress(source)

    async def upstream(request):
        """Validate the compressed request bytes and echo them with their original encoding."""
        assert request.headers["Content-Encoding"] == encoding
        assert await request.read() == compressed
        return web.Response(body=compressed, headers={"Content-Encoding": encoding})

    async with bridge(upstream) as h:
        async with h.client.post(
            h.url + "/backend-api/codex/responses", data=compressed, headers={"Content-Encoding": encoding}
        ) as response:
            assert response.status == 200
            assert response.headers["Content-Encoding"] == encoding
            assert await response.read() == compressed


async def test_sse_streams_before_completion_and_cancels_on_client_disconnect(bridge):
    """Deliver the first SSE event promptly and cancel upstream work when the client disconnects."""
    cancelled = asyncio.Event()

    async def upstream(request):
        """Emit one SSE event, then wait indefinitely and signal when cancellation cleans up."""
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b"data: synthetic-first\n\n")
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
        return response

    async with bridge(upstream) as h:
        async with h.client.post(h.url + "/backend-api/codex/responses", data=b"{}") as response:
            assert response.status == 200
            assert await response.content.readuntil(b"\n\n") == b"data: synthetic-first\n\n"
        await asyncio.wait_for(cancelled.wait(), timeout=2)


async def test_websocket_preserves_handshake_text_binary_large_messages_and_close(bridge, caplog):
    """Relay large text, binary data, and close details while filtering handshake secrets and logs."""
    caplog.set_level(logging.DEBUG)

    async def upstream(request):
        """Validate replaced identity, echo WebSocket messages, and close after the binary frame."""
        assert request.headers["Authorization"] == f"Bearer {LB_TOKEN}"
        assert "X-OpenAI-Actor-Authorization" not in request.headers
        assert "Cookie" not in request.headers
        ws = web.WebSocketResponse(max_msg_size=32 * 1024 * 1024)
        ws.headers["X-Codex-Turn-State"] = "synthetic-turn-state"
        ws.headers["Set-Cookie"] = "synthetic-cookie"
        await ws.prepare(request)
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                await ws.send_str(message.data)
            elif message.type == aiohttp.WSMsgType.BINARY:
                await ws.send_bytes(message.data)
                await ws.close(code=1001, message=b"synthetic-finished")
        return ws

    async with bridge(upstream) as h:
        async with h.client.ws_connect(
            h.url + "/backend-api/codex/responses",
            max_msg_size=32 * 1024 * 1024,
            headers={"X-OpenAI-Actor-Authorization": "synthetic-actor", "Cookie": "synthetic-cookie"},
        ) as ws:
            assert ws._response.headers["X-Codex-Turn-State"] == "synthetic-turn-state"
            assert "Set-Cookie" not in ws._response.headers
            payload = "s" * (20 * 1024 * 1024)
            await ws.send_str(payload)
            assert (await ws.receive(timeout=5)).data == payload
            await ws.send_bytes(b"synthetic-binary")
            assert (await ws.receive(timeout=2)).data == b"synthetic-binary"
            closed = await ws.receive(timeout=2)
            assert closed.type == aiohttp.WSMsgType.CLOSE
            assert closed.data == 1001
            assert closed.extra == "synthetic-finished"
    for secret in (CLIENT_TOKEN, LB_TOKEN, "synthetic-actor", "synthetic-cookie", "synthetic-binary"):
        assert secret not in caplog.text


@pytest.mark.parametrize("status,expected", [(401, 502), (403, 403), (426, 426), (429, 429), (307, 502)])
async def test_websocket_handshake_failure_and_redirect_policy(bridge, status, expected):
    """Map failed WebSocket handshakes without following redirects or replaying requests."""
    calls = []

    async def upstream(request):
        """Record a handshake attempt and return its configured failure with a redirect location."""
        calls.append(request.path)
        return web.Response(status=status, text="synthetic-error", headers={"Location": "/spy"})

    async with bridge(upstream) as h:
        with pytest.raises(aiohttp.WSServerHandshakeError) as error:
            await h.client.ws_connect(h.url + "/backend-api/codex/responses")
        assert error.value.status == expected
    assert calls == ["/backend-api/codex/responses"]


async def test_busy_limit_and_disconnect_release_request_slot(bridge, monkeypatch):
    """Reject excess requests while streaming and restore capacity after client disconnect."""
    monkeypatch.setattr(relay, "MAX_ACTIVE_REQUESTS", 1)
    cancelled = asyncio.Event()

    async def upstream(request):
        """Serve model probes immediately and hold other streams open until cancelled."""
        if request.path.endswith("models"):
            return web.Response(text="ok")
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"first\n")
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
        return response

    async with bridge(upstream) as h:
        response = await h.client.get(h.url + "/backend-api/codex/responses")
        assert await response.content.readline() == b"first\n"
        async with h.client.get(h.url + "/backend-api/codex/models") as second:
            assert second.status == 503
        response.close()
        await asyncio.wait_for(cancelled.wait(), 2)
        async with h.client.get(h.url + "/backend-api/codex/models") as third:
            assert third.status == 200


async def test_request_limit_prevents_known_oversize_upstream_dispatch(bridge, monkeypatch):
    """Reject requests with a known excessive body length before opening an upstream request."""
    monkeypatch.setattr(relay, "_MAX_BODY_BYTES", 32)
    calls = []

    async def upstream(request):
        """Record any dispatch that would violate the known request-size limit."""
        calls.append(request.path)
        return web.Response()

    async with bridge(upstream) as h:
        async with h.client.post(h.url + "/backend-api/codex/responses", data=b"x" * 33) as response:
            assert response.status == 413
    assert calls == []


async def test_secrets_and_bodies_are_absent_from_logs(bridge, caplog):
    """Keep tokens, query values, and request and response bodies out of debug logs."""
    caplog.set_level(logging.DEBUG)

    async def upstream(request):
        """Return an authentication failure containing a recognizable synthetic secret body."""
        return web.Response(status=401, text="synthetic-secret-body")

    async with bridge(upstream) as h:
        async with h.client.post(
            h.url + "/backend-api/codex/responses?secret=synthetic-query", data=b"synthetic-body"
        ) as response:
            assert response.status == 502
    for value in (CLIENT_TOKEN, LB_TOKEN, "synthetic-secret-body", "synthetic-query", "synthetic-body"):
        assert value not in caplog.text


async def test_future_route_and_method_preserve_no_automatic_headers(bridge):
    """Forward unfamiliar Codex routes and methods without inventing optional request headers."""

    async def upstream(request):
        """Check the opaque PATCH request and return a nonstandard success status unchanged."""
        assert request.method == "PATCH"
        assert request.raw_path == "/backend-api/codex/future-feature/?opaque=%20%2f"
        for name in ("User-Agent", "Accept", "Accept-Encoding", "Content-Type"):
            assert name not in request.headers
        assert await request.read() == b"synthetic-opaque"
        return web.Response(status=218, body=b"future-response")

    async with bridge(upstream) as h:
        async with h.client.patch(
            URL(h.url + "/backend-api/codex/future-feature/?opaque=%20%2f", encoded=True),
            data=b"synthetic-opaque",
            skip_auto_headers={"User-Agent", "Accept", "Accept-Encoding", "Content-Type"},
        ) as response:
            assert response.status == 218
            assert await response.read() == b"future-response"


@pytest.mark.parametrize("wire", ["http", "ws"])
async def test_environment_proxies_are_ignored(bridge, monkeypatch, wire):
    """Keep HTTP and WebSocket traffic on direct loopback connections despite proxy environment settings."""
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")

    async def upstream(request):
        """Respond over the selected transport to demonstrate direct access to the local LB."""
        if wire == "ws":
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_str("synthetic-proxy-bypass")
            await ws.close()
            return ws
        return web.Response(text="synthetic-proxy-bypass")

    async with bridge(upstream) as h:
        if wire == "ws":
            async with h.client.ws_connect(h.url + "/backend-api/codex/responses") as ws:
                assert (await ws.receive(timeout=2)).data == "synthetic-proxy-bypass"
        else:
            async with h.client.get(h.url + "/backend-api/codex/models") as response:
                assert response.status == 200


@pytest.mark.parametrize("variant", ["duplicate-host", "missing-auth", "absolute-target", "trace"])
async def test_raw_request_rejections_never_dispatch(bridge, variant):
    """Reject malformed authority, identity, target, and method combinations sent over raw TCP."""
    calls = []

    async def upstream(request):
        """Record any dispatch that escapes raw-request admission checks."""
        calls.append(request.path)
        return web.Response()

    async with bridge(upstream) as h:
        authority = h.url.removeprefix("http://")
        target = "/backend-api/codex/models"
        method = "TRACE" if variant == "trace" else "GET"
        if variant == "absolute-target":
            target = h.url + target
        fields = [f"Host: {authority}", "Connection: close"]
        if variant != "missing-auth":
            fields.append(f"Authorization: Bearer {CLIENT_TOKEN}")
        if variant == "duplicate-host":
            fields.append(f"Host: {authority}")
        wire = f"{method} {target} HTTP/1.1\r\n" + "\r\n".join(fields) + "\r\n\r\n"
        reader, writer = await asyncio.open_connection("127.0.0.1", URL(h.url).port)
        try:
            writer.write(wire.encode())
            await writer.drain()
            status = (await asyncio.wait_for(reader.readline(), 2)).split()[1]
            assert status in {b"400", b"403", b"405"}
        finally:
            writer.close()
            await writer.wait_closed()
    assert calls == []


@pytest.mark.parametrize("change", ["missing", "symlink", "public", "api-key", "other-mode", "multiline", "unicode"])
async def test_invalid_credential_file_never_reaches_lb(bridge, change):
    """Fail closed on missing, unsafe, or unsupported credentials without exposing their location."""
    if change == "public" and os.name != "posix":
        pytest.skip("POSIX ownership/mode contract")
    if change == "symlink" and not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("requires no-follow file open")
    calls = []

    async def upstream(request):
        """Record any upstream request admitted after credentials become invalid."""
        calls.append(request.path)
        return web.Response()

    async with bridge(upstream) as h:
        if change == "missing":
            h.token_file.unlink()
        elif change == "symlink":
            h.token_file.unlink()
            h.token_file.symlink_to(h.auth_file)
        elif change == "public":
            h.token_file.chmod(0o644)
        elif change == "api-key":
            write_private(
                h.auth_file, json.dumps({"OPENAI_API_KEY": "synthetic-key", "tokens": {"access_token": CLIENT_TOKEN}})
            )
        elif change == "other-mode":
            write_private(h.auth_file, json.dumps({"auth_mode": "apikey", "tokens": {"access_token": CLIENT_TOKEN}}))
        elif change == "multiline":
            write_private(h.token_file, "synthetic\nInjected: header")
        elif change == "unicode":
            write_private(h.token_file, "synthetic-\u2603")
        async with h.client.get(h.url + "/backend-api/codex/models") as response:
            assert response.status == 503
            assert str(h.auth_file.parent) not in await response.text()
    assert calls == []


async def test_truncated_upstream_response_does_not_complete_successfully(bridge):
    """Surface premature upstream EOF as a gateway error or incomplete downstream payload."""

    async def upstream(request):
        """Advertise a larger body than is sent, then close the transport abruptly."""
        response = web.StreamResponse(headers={"Content-Length": "100"})
        await response.prepare(request)
        await response.write(b"partial")
        request.transport.close()
        return response

    async with bridge(upstream) as h:
        async with h.client.get(h.url + "/backend-api/codex/responses") as response:
            if response.status == 200:
                with pytest.raises(aiohttp.ClientPayloadError):
                    await response.read()
            else:
                assert response.status == 502


async def test_websocket_downstream_close_reaches_lb_and_releases_slot(bridge, monkeypatch):
    """Forward the client close code and reason, then make the request slot reusable."""
    monkeypatch.setattr(relay, "MAX_ACTIVE_REQUESTS", 1)
    closed = asyncio.Event()
    received = []

    async def upstream(request):
        """Capture the relayed WebSocket close details and answer later capacity probes."""
        if request.path.endswith("models"):
            return web.Response(text="available")
        ws = web.WebSocketResponse(autoclose=False)
        await ws.prepare(request)
        message = await ws.receive()
        received.append((message.type, message.data, message.extra))
        await ws.close()
        closed.set()
        return ws

    async with bridge(upstream) as h:
        async with h.client.ws_connect(h.url + "/backend-api/codex/responses") as ws:
            await ws.close(code=4001, message=b"synthetic-client-close")
        await asyncio.wait_for(closed.wait(), 2)
        for _ in range(20):
            async with h.client.get(h.url + "/backend-api/codex/models") as response:
                if response.status == 200:
                    break
            await asyncio.sleep(0.01)
        assert response.status == 200
    assert received == [(aiohttp.WSMsgType.CLOSE, 4001, "synthetic-client-close")]


async def test_abrupt_websocket_disconnect_closes_upstream_and_releases_slot(bridge, monkeypatch):
    """Translate an aborted client transport into upstream close code 1001 and restore capacity."""
    monkeypatch.setattr(relay, "MAX_ACTIVE_REQUESTS", 1)
    closed = asyncio.Event()
    received = []

    async def upstream(request):
        """Capture the upstream close frame and answer probes after cleanup."""
        if request.path.endswith("models"):
            return web.Response(text="available")
        ws = web.WebSocketResponse(autoclose=False)
        await ws.prepare(request)
        message = await ws.receive()
        received.append((message.type, message.data))
        await ws.close()
        closed.set()
        return ws

    async with bridge(upstream) as h:
        reader, writer = await asyncio.open_connection("127.0.0.1", URL(h.url).port)
        writer.write(
            (
                "GET /backend-api/codex/responses HTTP/1.1\r\n"
                f"Host: {URL(h.url).raw_authority}\r\n"
                "Connection: Upgrade\r\nUpgrade: websocket\r\n"
                "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                f"Authorization: Bearer {CLIENT_TOKEN}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        handshake = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
        assert handshake.startswith(b"HTTP/1.1 101 ")
        writer.transport.abort()
        await writer.wait_closed()
        await asyncio.wait_for(closed.wait(), 2)
        for _ in range(20):
            async with h.client.get(h.url + "/backend-api/codex/models") as response:
                if response.status == 200:
                    break
            await asyncio.sleep(0.01)
        assert response.status == 200
    assert received == [(aiohttp.WSMsgType.CLOSE, 1001)]


@pytest.mark.parametrize("limit", ["IDLE_TIMEOUT_SECONDS", "MAX_LIFETIME_SECONDS"])
async def test_websocket_deadline_closes_both_sides(bridge, monkeypatch, limit):
    """Close client and upstream WebSockets with code 1001 when idle or lifetime limits expire."""
    monkeypatch.setattr(relay, limit, 0.15)
    closed = asyncio.Event()

    async def upstream(request):
        """Wait for bridge-initiated closure and signal that upstream cleanup completed."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()
        await ws.close()
        closed.set()
        return ws

    async with bridge(upstream) as h:
        async with h.client.ws_connect(h.url + "/backend-api/codex/responses") as ws:
            message = await ws.receive(timeout=2)
            assert message.type == aiohttp.WSMsgType.CLOSE
            assert message.data == 1001
        await asyncio.wait_for(closed.wait(), 2)


async def test_chunked_request_limit_does_not_replay(bridge, monkeypatch):
    """Reject a streamed request that crosses the body limit without replaying it upstream."""
    monkeypatch.setattr(relay, "_MAX_BODY_BYTES", 32)
    calls = []

    async def upstream(request):
        """Record dispatch and tolerate cancellation while consuming the oversized upload."""
        calls.append(request.path)
        try:
            await request.read()
        except (ConnectionError, asyncio.CancelledError):
            return web.Response(status=499)
        return web.Response(text="unexpected completion")

    async def chunks():
        """Yield an upload that crosses the patched body limit on its second chunk."""
        yield b"s" * 16
        yield b"s" * 17

    async with bridge(upstream) as h:
        async with h.client.post(h.url + "/backend-api/codex/responses", data=chunks()) as response:
            assert response.status == 413
    assert len(calls) <= 1


async def test_response_limit_never_reports_a_complete_oversize_body(bridge, monkeypatch):
    """Expose an oversized streamed response as a gateway failure or incomplete payload."""
    monkeypatch.setattr(relay, "_MAX_BODY_BYTES", 32)

    async def upstream(request):
        """Send two chunks whose combined length exceeds the patched response limit."""
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"s" * 16)
        await response.write(b"s" * 17)
        return response

    async with bridge(upstream) as h:
        async with h.client.get(h.url + "/backend-api/codex/responses") as response:
            if response.status == 200:
                with pytest.raises(aiohttp.ClientPayloadError):
                    await response.read()
            else:
                assert response.status == 502


async def test_http_idle_timeout_recovers_request_slot(bridge, monkeypatch):
    """Return 502 for an idle upstream and release the occupied request slot after cancellation."""
    monkeypatch.setattr(relay, "IDLE_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(relay, "MAX_ACTIVE_REQUESTS", 1)
    cancelled = asyncio.Event()

    async def upstream(request):
        """Stall response requests until cancelled while allowing model probes to finish."""
        if request.path.endswith("models"):
            return web.Response(text="available")
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
        return web.Response()

    async with bridge(upstream) as h:
        async with h.client.get(h.url + "/backend-api/codex/responses") as response:
            assert response.status == 502
        await asyncio.wait_for(cancelled.wait(), 2)
        async with h.client.get(h.url + "/backend-api/codex/models") as response:
            assert response.status == 200


async def test_response_cannot_enable_browser_access(bridge):
    """Strip upstream CORS headers so a response cannot grant browser access."""

    async def upstream(request):
        """Attempt to grant cross-origin credential access through response headers."""
        return web.Response(headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Credentials": "true"})

    async with bridge(upstream) as h:
        async with h.client.get(h.url + "/backend-api/codex/models") as response:
            assert response.status == 200
            assert not any(name.lower().startswith("access-control-") for name in response.headers)


async def test_malformed_http_parser_does_not_log_request_content(bridge, caplog):
    """Reject malformed HTTP with 400 without logging the private bytes in its request line."""
    caplog.set_level(logging.DEBUG)

    async def upstream(request):
        """Fail if parser-rejected input reaches the upstream handler."""
        pytest.fail("malformed request must not dispatch")

    async with bridge(upstream) as h:
        reader, writer = await asyncio.open_connection("127.0.0.1", URL(h.url).port)
        try:
            writer.write(b"synthetic-private? /backend-api/codex HTTP/1.1\r\nConnection: close\r\n\r\n")
            await writer.drain()
            assert b"400" in await asyncio.wait_for(reader.readline(), 2)
        finally:
            writer.close()
            await writer.wait_closed()
    assert "synthetic-private" not in caplog.text


@pytest.mark.parametrize("selected", [False, True])
async def test_websocket_protocol_negotiation_does_not_log_offer(bridge, caplog, selected):
    """Preserve accepted or declined subprotocol negotiation without logging the offered value."""
    caplog.set_level(logging.DEBUG)
    protocol = "synthetic-private-protocol"

    async def upstream(request):
        """Accept or silently decline the offered subprotocol and send a confirmation message."""
        assert request.headers["Sec-WebSocket-Protocol"] == protocol
        ws = web.WebSocketResponse(protocols=[protocol] if selected else [])
        if not selected:
            # The stub intentionally declines the offer without aiohttp's own warning.
            headers = request.headers.copy()
            headers.popall("Sec-WebSocket-Protocol")
            request = request.clone(headers=headers)
        await ws.prepare(request)
        await ws.send_str("ok")
        await ws.close()
        return ws

    async with bridge(upstream) as h:
        async with h.client.ws_connect(h.url + "/backend-api/codex/responses", protocols=[protocol]) as ws:
            assert ws.protocol == (protocol if selected else None)
            assert (await ws.receive(timeout=2)).data == "ok"
    assert protocol not in caplog.text


@asynccontextmanager
async def bridge_process(tmp_path: Path, lb_port: int) -> AsyncIterator[tuple[asyncio.subprocess.Process, str, int]]:
    """Run the real CLI with synthetic credentials and an exclusively owned process."""
    write_auth(tmp_path / "auth.json")
    token_file = tmp_path / "lb-token"
    write_private(token_file, LB_TOKEN)
    with bound_socket() as sock:
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.cli",
        "desktop-bridge",
        "--listen-port",
        str(port),
        "--lb-port",
        str(lb_port),
        "--codex-home",
        str(tmp_path),
        "--lb-token-file",
        str(token_file),
        cwd=Path(__file__).resolve().parents[2],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(10), aiohttp.ClientSession() as client:
            while True:
                if process.returncode is not None:
                    pytest.fail(f"Bridge exited during startup: {(await process.communicate())!r}")
                try:
                    async with client.get(url) as response:
                        assert response.status == 404
                    break
                except aiohttp.ClientConnectorError:
                    await asyncio.sleep(0.02)
        yield process, url, port
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process signals")
@pytest.mark.parametrize("finish_http", [True, False])
async def test_sigterm_closes_websocket_drains_or_aborts_http_and_releases_port(tmp_path, finish_http):
    """Verify bounded SIGTERM cleanup, HTTP drain or abort, secret-free logs, and listener release."""
    finish = asyncio.Event()
    cancelled = asyncio.Event()
    upstream_closed = asyncio.Event()
    calls = []
    close_codes = []

    async def upstream(request):
        """Track WebSocket closure and stream HTTP until the test finishes it or shutdown cancels it."""
        calls.append(request.path)
        if request.path.endswith("responses"):
            ws = web.WebSocketResponse(autoclose=False)
            await ws.prepare(request)
            message = await ws.receive()
            close_codes.append(message.data)
            await ws.close()
            upstream_closed.set()
            return ws
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b"data: started\n\n")
        try:
            await finish.wait()
            await response.write(b"data: completed\n\n")
            return response
        except asyncio.CancelledError:
            cancelled.set()
            raise

    lb_sock = bound_socket()
    lb = web.Application()
    lb.router.add_route("*", "/{tail:.*}", upstream)
    async with serve(lb, lb_sock), bridge_process(tmp_path, lb_sock.getsockname()[1]) as (process, url, port):
        async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {CLIENT_TOKEN}"}) as client:
            async with client.ws_connect(url + "/backend-api/codex/responses") as ws:
                async with client.post(url + "/backend-api/codex/synthetic-stream") as response:
                    assert await response.content.readexactly(15) == b"data: started\n\n"
                    started = time.monotonic()
                    process.send_signal(signal.SIGTERM)
                    message = await ws.receive(timeout=2)
                    assert message.type == aiohttp.WSMsgType.CLOSE
                    assert message.data == 1001
                    if finish_http:
                        finish.set()
                        assert await asyncio.wait_for(response.read(), 2) == b"data: completed\n\n"
                    else:
                        with pytest.raises(aiohttp.ClientPayloadError):
                            await asyncio.wait_for(response.read(), 15)
                        await asyncio.wait_for(cancelled.wait(), 2)
                    await asyncio.wait_for(upstream_closed.wait(), 2)
                    assert await asyncio.wait_for(process.wait(), 3) == 0
                    elapsed = time.monotonic() - started
                    assert elapsed < (3 if finish_http else 15)
                    print(f"SIGTERM finish_http={finish_http}: {elapsed:.3f}s")
        stdout, stderr = await process.communicate()
        for secret in (CLIENT_TOKEN.encode(), LB_TOKEN.encode()):
            assert secret not in stdout + stderr
        assert b"Traceback" not in stderr
        assert b"Task was destroyed" not in stderr
        with socket.socket() as released:
            released.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            released.bind(("127.0.0.1", port))
    assert calls == ["/backend-api/codex/responses", "/backend-api/codex/synthetic-stream"]
    assert close_codes == [1001]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process signals")
async def test_sigterm_closes_websockets_when_peers_do_not_acknowledge(tmp_path):
    """Bound shutdown and close transports even when multiple clients ignore WebSocket close frames."""
    close_codes = []

    async def upstream(request):
        """Record and acknowledge each shutdown close frame received from the bridge."""
        ws = web.WebSocketResponse(autoclose=False)
        await ws.prepare(request)
        message = await ws.receive()
        close_codes.append(message.data)
        await ws.close()
        return ws

    lb_sock = bound_socket()
    lb = web.Application()
    lb.router.add_route("*", "/{tail:.*}", upstream)
    peers = []
    async with serve(lb, lb_sock), bridge_process(tmp_path, lb_sock.getsockname()[1]) as (process, _, port):
        try:
            for _ in range(2):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                peers.append((reader, writer))
                writer.write(
                    (
                        "GET /backend-api/codex/responses HTTP/1.1\r\n"
                        f"Host: 127.0.0.1:{port}\r\n"
                        "Connection: Upgrade\r\nUpgrade: websocket\r\n"
                        "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                        f"Authorization: Bearer {CLIENT_TOKEN}\r\n\r\n"
                    ).encode()
                )
                await writer.drain()
                assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 101 ")
            started = time.monotonic()
            process.send_signal(signal.SIGTERM)
            for reader, _ in peers:
                # Deliberately leave both close frames unacknowledged.
                assert await asyncio.wait_for(reader.readexactly(4), 2) == b"\x88\x02\x03\xe9"
            assert await asyncio.wait_for(process.wait(), 13) == 0
            assert time.monotonic() - started < 13
            for reader, _ in peers:
                assert await asyncio.wait_for(reader.read(), 1) == b""
        finally:
            for _, writer in peers:
                writer.close()
                await writer.wait_closed()
    assert close_codes == [1001, 1001]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process signals")
async def test_sigterm_rejects_upstream_handshake_that_completes_after_shutdown(tmp_path):
    """Reject a late upstream handshake with 503 and close its WebSocket during shutdown."""
    received = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()
    close_codes = []

    async def upstream(request):
        """Delay handshake completion until shutdown starts, then observe the bridge closing it."""
        received.set()
        await release.wait()
        ws = web.WebSocketResponse(autoclose=False)
        await ws.prepare(request)
        close_codes.append((await ws.receive()).data)
        await ws.close()
        closed.set()
        return ws

    lb_sock = bound_socket()
    lb = web.Application()
    lb.router.add_route("*", "/{tail:.*}", upstream)
    async with serve(lb, lb_sock), bridge_process(tmp_path, lb_sock.getsockname()[1]) as (process, url, port):
        async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {CLIENT_TOKEN}"}) as client:

            async def connect():
                """Start a client WebSocket handshake that can remain pending while shutdown begins."""
                return await client.ws_connect(url + "/backend-api/codex/responses")

            handshake = asyncio.create_task(connect())
            try:
                await asyncio.wait_for(received.wait(), 2)
                process.send_signal(signal.SIGTERM)
                async with asyncio.timeout(2):
                    while True:
                        try:
                            _, writer = await asyncio.open_connection("127.0.0.1", port)
                        except (ConnectionRefusedError, ConnectionResetError):
                            break
                        writer.close()
                        await writer.wait_closed()
                        await asyncio.sleep(0.01)
                release.set()
                with pytest.raises(aiohttp.WSServerHandshakeError) as error:
                    await asyncio.wait_for(handshake, 2)
                assert error.value.status == 503
                await asyncio.wait_for(closed.wait(), 2)
                assert await asyncio.wait_for(process.wait(), 2) == 0
            finally:
                handshake.cancel()
                await asyncio.gather(handshake, return_exceptions=True)
    assert close_codes == [1001]


async def test_unavailable_upstream_recovers_when_lb_starts_without_bridge_restart(tmp_path):
    """Recover from an unavailable LB on a later request without restarting the bridge."""
    write_auth(tmp_path / "auth.json")
    token_file = tmp_path / "lb-token"
    write_private(token_file, LB_TOKEN)
    lb_sock, bridge_sock = bound_socket(), bound_socket()
    config = BridgeConfig(bridge_sock.getsockname()[1], lb_sock.getsockname()[1], tmp_path, token_file)
    calls = []

    async def upstream(request):
        """Verify LB credentials and record the successful request after the upstream starts."""
        calls.append(request.path)
        assert request.headers["Authorization"] == f"Bearer {LB_TOKEN}"
        return web.Response(text="ready")

    lb = web.Application()
    lb.router.add_route("*", "/{tail:.*}", upstream)
    try:
        async with serve(create_bridge_app(config), bridge_sock):
            async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {CLIENT_TOKEN}"}) as client:
                url = f"http://127.0.0.1:{config.listen_port}/backend-api/codex/models"
                async with client.get(url) as response:
                    assert response.status == 502
                async with serve(lb, lb_sock):
                    async with client.get(url) as response:
                        assert response.status == 200
                        assert await response.text() == "ready"
    finally:
        lb_sock.close()
    assert calls == ["/backend-api/codex/models"]
