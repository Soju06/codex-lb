from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import aiohttp
import anyio
import pytest

import app.core.clients.proxy as proxy_module
from app.core.clients.codex import CodexClient
from app.core.clients.native_egress import SubprocessNativeEgressClient
from app.core.clients.proxy import ProxyResponseError, compact_responses, override_stream_timeouts, stream_responses
from app.core.openai.models import CompactResponsePayload
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.integration

type _HttpHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter, bytes, bytes], Awaitable[None]]


class _UnexpectedPythonSession:
    closed = False

    async def close(self) -> None:
        self.closed = True

    async def request(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("routed native HTTP must not use aiohttp")

    def post(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("direct native HTTP must not use aiohttp")

    def ws_connect(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("direct native HTTP must not use aiohttp websocket")


async def _read_request(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    head = await reader.readuntil(b"\r\n\r\n")
    content_length = 0
    for line in head.split(b"\r\n")[1:]:
        name, separator, value = line.partition(b":")
        if separator and name.lower() == b"content-length":
            content_length = int(value.strip())
            break
    return head, await reader.readexactly(content_length)


async def _start_chunked_response(
    writer: asyncio.StreamWriter,
    *,
    status: str = "200 OK",
    content_type: str | None = "text/event-stream",
) -> None:
    content_type_header = f"Content-Type: {content_type}\r\n" if content_type is not None else ""
    writer.write(
        (
            f"HTTP/1.1 {status}\r\n" + content_type_header + "Transfer-Encoding: chunked\r\n"
            "Connection: keep-alive\r\n"
            "\r\n"
        ).encode("ascii")
    )
    await writer.drain()


async def _write_chunk(writer: asyncio.StreamWriter, chunk: bytes) -> None:
    writer.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
    await writer.drain()


async def _finish_chunks(writer: asyncio.StreamWriter) -> None:
    writer.write(b"0\r\n\r\n")
    await writer.drain()


@asynccontextmanager
async def _serve_http(handler: _HttpHandler) -> AsyncIterator[str]:
    connections: set[asyncio.StreamWriter] = set()
    tasks: set[asyncio.Task[None]] = set()

    async def dispatch(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        connections.add(writer)
        try:
            head, body = await _read_request(reader)
            await handler(reader, writer, head, body)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            connections.discard(writer)
            tasks.discard(task)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(dispatch, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(connections):
            writer.close()
        for task in tuple(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
async def native_worker() -> AsyncIterator[SubprocessNativeEgressClient]:
    helper_value = os.environ.get("CODEX_LB_NATIVE_EGRESS_TEST_BINARY")
    if not helper_value:
        pytest.skip("set CODEX_LB_NATIVE_EGRESS_TEST_BINARY to run the native SSE wire probes")
    helper = Path(helper_value)
    if not helper.is_file() or not os.access(helper, os.X_OK):
        pytest.skip(f"native helper is unavailable: {helper}")

    client = SubprocessNativeEgressClient(helper)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
def direct_loopback_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_module, "resolve_http_proxy_from_env", lambda _url: None)


@pytest.fixture(params=[False, True], ids=["direct", "routed"])
def routed(request: pytest.FixtureRequest) -> bool:
    return request.param


def _payload(event_block: str) -> dict[str, object]:
    data_lines = [line.removeprefix("data:").lstrip() for line in event_block.splitlines() if line.startswith("data:")]
    assert data_lines
    value = json.loads("\n".join(data_lines))
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _request(marker: str, *, stream: bool = True) -> ResponsesRequest:
    return ResponsesRequest(
        model="gpt-5.4",
        instructions="native SSE integration probe",
        input=marker,
        stream=stream,
    )


def _stream(
    base_url: str,
    native_worker: SubprocessNativeEgressClient,
    marker: str,
    *,
    stream: bool = True,
    raise_for_status: bool = False,
    routed: bool = False,
    own_codex_client: bool = False,
) -> AsyncIterator[str]:
    session = _UnexpectedPythonSession()
    route = (
        ResolvedUpstreamRoute(
            mode="account_bound",
            pool_id="sse-probe",
            endpoint=ResolvedProxyEndpoint("sse-proxy", "http", "127.0.0.1", urlsplit(base_url).port or 80),
        )
        if routed
        else None
    )
    return stream_responses(
        _request(marker, stream=stream),
        {},
        "test-access-token",
        "test-account",
        base_url="http://upstream.invalid" if routed else base_url,
        raise_for_status=raise_for_status,
        session=cast(aiohttp.ClientSession, session),
        route=route,
        codex_client=CodexClient(session, native_egress_client=native_worker)
        if routed and not own_codex_client
        else None,
        upstream_stream_transport_override="http",
        allow_direct_egress=False,
        suppress_live_usage=True,
        native_egress_client=native_worker,
    )


@pytest.mark.asyncio
async def test_native_proxy_frames_large_non_utf8_sse_without_python_scanning(
    monkeypatch: pytest.MonkeyPatch,
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
) -> None:
    comment = b": " + (b"x" * (20 * 1024)) + b"\x00\xff\xfe\xe2\x82\xac"
    raw_event = comment + b'\ndata: {"type":"response.completed","response":{"id":"resp_large"}}\n\n'
    expected_event = raw_event.decode("utf-8", errors="replace")
    assert len(raw_event) > 16 * 1024
    assert len(expected_event.encode("utf-8")) > len(raw_event)

    settings = proxy_module.get_settings().model_copy(update={"max_sse_event_bytes": len(raw_event)})
    monkeypatch.setattr(proxy_module, "get_settings", lambda: settings)

    def forbidden_python_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Python SSE byte framing ran for a native framed response")

    monkeypatch.setattr(proxy_module, "_find_sse_separator", forbidden_python_scan)

    async def handler(
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        _head: bytes,
        _body: bytes,
    ) -> None:
        await _start_chunked_response(writer)
        boundaries = (8191, 16385, len(raw_event) - 3)
        start = 0
        for end in boundaries:
            await _write_chunk(writer, raw_event[start:end])
            start = end
        await _write_chunk(writer, raw_event[start:])
        await _finish_chunks(writer)

    async with _serve_http(handler) as base_url:
        events = [event async for event in _stream(base_url, native_worker, "large-event", routed=routed)]

    assert events == [expected_event]


@pytest.mark.asyncio
async def test_native_proxy_reports_public_event_size_failure(
    monkeypatch: pytest.MonkeyPatch,
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
) -> None:
    limit = 64
    first = b'data: {"type":"response.created"}\n\n'
    oversized = b"data: " + (b"x" * 80) + b"\n\n"
    settings = proxy_module.get_settings().model_copy(update={"max_sse_event_bytes": limit})
    monkeypatch.setattr(proxy_module, "get_settings", lambda: settings)

    async def handler(
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        _head: bytes,
        _body: bytes,
    ) -> None:
        await _start_chunked_response(writer)
        await _write_chunk(writer, first + oversized)
        await _finish_chunks(writer)

    async with _serve_http(handler) as base_url:
        events = [event async for event in _stream(base_url, native_worker, "oversized-event", routed=routed)]

    assert events[0] == first.decode()
    assert len(events) == 2
    failed = _payload(events[1])
    assert failed["type"] == "response.failed"
    error = cast(dict[str, object], cast(dict[str, object], failed["response"])["error"])
    assert error["code"] == "stream_event_too_large"
    assert str(len(oversized)) in cast(str, error["message"])
    assert str(limit) in cast(str, error["message"])


@pytest.mark.asyncio
async def test_native_proxy_reports_public_idle_timeout(
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
) -> None:
    worker_closed_request = asyncio.Event()

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        _head: bytes,
        _body: bytes,
    ) -> None:
        await _start_chunked_response(writer)
        await reader.read()
        worker_closed_request.set()

    async with _serve_http(handler) as base_url:
        with override_stream_timeouts(idle_timeout_seconds=0.1):
            events = await asyncio.wait_for(
                _collect(_stream(base_url, native_worker, "idle-timeout", routed=routed)),
                timeout=3.0,
            )
        await asyncio.wait_for(worker_closed_request.wait(), timeout=2.0)

    assert len(events) == 1
    failed = _payload(events[0])
    error = cast(dict[str, object], cast(dict[str, object], failed["response"])["error"])
    assert error["code"] == "stream_idle_timeout"
    assert error["message"] == "Upstream stream idle timeout"


async def _collect(events: AsyncIterator[str]) -> list[str]:
    return [event async for event in events]


@pytest.mark.asyncio
async def test_partial_body_activity_outlives_native_idle_interval(
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
) -> None:
    event = b'data: {"type":"response.completed","response":{"id":"resp_active"}}\n\n'
    chunks = [event[index : index + 13] for index in range(0, len(event), 13)]
    idle_timeout = 0.2

    async def handler(
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        _head: bytes,
        _body: bytes,
    ) -> None:
        await _start_chunked_response(writer)
        for chunk in chunks:
            await _write_chunk(writer, chunk)
            await asyncio.sleep(0.06)
        await _finish_chunks(writer)

    async with _serve_http(handler) as base_url:
        started_at = asyncio.get_running_loop().time()
        with override_stream_timeouts(idle_timeout_seconds=idle_timeout):
            events = [event async for event in _stream(base_url, native_worker, "active-partials", routed=routed)]
        elapsed = asyncio.get_running_loop().time() - started_at

    assert elapsed > idle_timeout
    assert events == [event.decode()]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled_scope", [False, True])
async def test_cancelling_native_stream_after_partial_event_keeps_peer_request_usable(
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
    cancelled_scope: bool,
) -> None:
    cancelled_partial_sent = asyncio.Event()
    cancelled_connection_closed = asyncio.Event()
    peer_waiting = asyncio.Event()
    release_peer = asyncio.Event()
    first = b'data: {"type":"response.created","response":{"id":"resp_cancel"}}\n\n'
    peer_completed = b'data: {"type":"response.completed","response":{"id":"resp_peer"}}\n\n'

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        _head: bytes,
        body: bytes,
    ) -> None:
        await _start_chunked_response(writer)
        if b"cancel-after-partial" in body:
            await _write_chunk(writer, first)
            await _write_chunk(writer, b'data: {"type":"response.output_text.delta"')
            cancelled_partial_sent.set()
            await reader.read()
            cancelled_connection_closed.set()
            return

        assert b"unrelated-peer" in body
        peer_waiting.set()
        await release_peer.wait()
        await _write_chunk(writer, peer_completed)
        await _finish_chunks(writer)

    async with _serve_http(handler) as base_url:
        cancelled = _stream(base_url, native_worker, "cancel-after-partial", routed=routed)
        assert await asyncio.wait_for(anext(cancelled), timeout=2.0) == first.decode()
        await asyncio.wait_for(cancelled_partial_sent.wait(), timeout=2.0)

        peer_task = asyncio.create_task(
            _collect(_stream(base_url, native_worker, "unrelated-peer", routed=routed)),
            name="native-sse-peer-request",
        )
        try:
            await asyncio.wait_for(peer_waiting.wait(), timeout=2.0)
            helper_process = native_worker._process

            with anyio.CancelScope() as scope:
                if cancelled_scope:
                    scope.cancel()
                await cast(AsyncGenerator[str, None], cancelled).aclose()
            await asyncio.wait_for(cancelled_connection_closed.wait(), timeout=2.0)
            release_peer.set()
            peer_events = await asyncio.wait_for(peer_task, timeout=2.0)
        finally:
            release_peer.set()
            if not peer_task.done():
                peer_task.cancel()
            await asyncio.gather(peer_task, return_exceptions=True)
            await cast(AsyncGenerator[str, None], cancelled).aclose()

    assert peer_events == [peer_completed.decode()]
    assert native_worker._process is helper_process
    assert helper_process is not None and helper_process.returncode is None


@pytest.mark.asyncio
async def test_native_streaming_http_error_body_stays_raw(
    monkeypatch: pytest.MonkeyPatch,
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
) -> None:
    upstream_error = {
        "error": {
            "message": "wire error body",
            "type": "invalid_request_error",
            "code": "wire_error",
        }
    }
    body = json.dumps(upstream_error, separators=(",", ":")).encode()

    def forbidden_python_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an HTTP error body must not enter SSE framing")

    monkeypatch.setattr(proxy_module, "_find_sse_separator", forbidden_python_scan)

    async def handler(
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        _head: bytes,
        _request_body: bytes,
    ) -> None:
        await _start_chunked_response(writer, status="429 Too Many Requests", content_type="application/json")
        await _write_chunk(writer, body[:11])
        await _write_chunk(writer, body[11:])
        await _finish_chunks(writer)

    async with _serve_http(handler) as base_url:
        with pytest.raises(ProxyResponseError) as exc_info:
            _ = [
                event
                async for event in _stream(
                    base_url,
                    native_worker,
                    "raw-http-error",
                    raise_for_status=True,
                    routed=routed,
                )
            ]

    assert exc_info.value.status_code == 429
    assert exc_info.value.payload == upstream_error


@pytest.mark.asyncio
async def test_native_non_streaming_response_body_stays_raw(
    monkeypatch: pytest.MonkeyPatch,
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
) -> None:
    response = {
        "id": "resp_nonstream",
        "object": "response",
        "status": "completed",
        "output": [],
    }
    body = json.dumps(response, separators=(",", ":")).encode()
    requests: list[tuple[bytes, bytes]] = []

    def forbidden_python_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a non-streaming response must not enter SSE framing")

    monkeypatch.setattr(proxy_module, "_find_sse_separator", forbidden_python_scan)

    async def handler(
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        head: bytes,
        request_body: bytes,
    ) -> None:
        requests.append((head, request_body))
        await _start_chunked_response(writer, content_type="application/json")
        await _write_chunk(writer, body[:17])
        await _write_chunk(writer, body[17:])
        await _finish_chunks(writer)

    async with _serve_http(handler) as base_url:
        events = [
            event async for event in _stream(base_url, native_worker, "raw-nonstream", stream=False, routed=routed)
        ]

    assert len(requests) == 1
    assert b"accept: application/json\r\n" in requests[0][0].lower()
    assert json.loads(requests[0][1])["stream"] is False
    assert _payload(events[0]) == {"type": "response.completed", "response": response}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "oversize", "idle", "disconnect"])
async def test_routed_native_selection_stops_after_response_head(
    monkeypatch: pytest.MonkeyPatch,
    native_worker: SubprocessNativeEgressClient,
    outcome: str,
) -> None:
    accepted_heads: list[bytes] = []
    unexpected_replays: list[bytes] = []
    completed = b'data: {"type":"response.completed","response":{"id":"resp_fallback"}}\n\n'
    settings = proxy_module.get_settings().model_copy(update={"max_sse_event_bytes": 128})
    monkeypatch.setattr(proxy_module, "get_settings", lambda: settings)
    session = _UnexpectedPythonSession()
    monkeypatch.setattr(proxy_module, "create_codex_session", lambda: session)

    async def selected_handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, head: bytes, _body: bytes
    ) -> None:
        accepted_heads.append(head)
        await _start_chunked_response(writer)
        if outcome == "completed":
            await _write_chunk(writer, completed)
            await _finish_chunks(writer)
        elif outcome == "oversize":
            await _write_chunk(writer, b"data: " + b"x" * 256)
        elif outcome == "idle":
            await reader.read()
        # Closing an unfinished chunked body produces a post-head body failure.

    async def replay_handler(
        _reader: asyncio.StreamReader, writer: asyncio.StreamWriter, head: bytes, _body: bytes
    ) -> None:
        unexpected_replays.append(head)
        await _start_chunked_response(writer)
        await _write_chunk(writer, completed)
        await _finish_chunks(writer)

    async with _serve_http(selected_handler) as selected_url, _serve_http(replay_handler) as replay_url:
        with socket.socket() as unavailable:
            # Reserve a non-listening port: a deterministic refused connection
            # proves pre-dispatch fallback without relying on external hosts.
            unavailable.bind(("127.0.0.1", 0))
            dead_port = unavailable.getsockname()[1]
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                monkeypatch.setenv(name, f"http://127.0.0.1:{dead_port}")
            monkeypatch.setenv("NO_PROXY", "*")
            monkeypatch.setenv("no_proxy", "*")
            route = ResolvedUpstreamRoute(
                mode="account_bound",
                pool_id="fallback-pool",
                endpoint=ResolvedProxyEndpoint("refused", "http", "127.0.0.1", dead_port),
                fallbacks=(
                    ResolvedProxyEndpoint("selected", "http", "127.0.0.1", urlsplit(selected_url).port or 80),
                    ResolvedProxyEndpoint("no-replay", "http", "127.0.0.1", urlsplit(replay_url).port or 80),
                ),
            )
            trace = proxy_module.UpstreamProxyRouteTrace()
            with override_stream_timeouts(idle_timeout_seconds=0.15):
                events = await asyncio.wait_for(
                    _collect(
                        stream_responses(
                            _request("selected-endpoint"),
                            {},
                            "test-access-token",
                            "test-account",
                            base_url="http://upstream.invalid",
                            route=route,
                            route_trace=trace,
                            session=cast(aiohttp.ClientSession, session),
                            upstream_stream_transport_override="http",
                            suppress_live_usage=True,
                            native_egress_client=native_worker,
                        )
                    ),
                    timeout=3,
                )

    assert len(accepted_heads) == 1
    assert accepted_heads[0].startswith(b"POST http://upstream.invalid/codex/responses ")
    assert not unexpected_replays
    assert trace == proxy_module.UpstreamProxyRouteTrace("account_bound", "fallback-pool", "selected", True)
    assert session.closed
    assert not native_worker._streams
    if outcome == "completed":
        assert events == [completed.decode()]
    else:
        error = cast(dict[str, object], cast(dict[str, object], _payload(events[-1])["response"])["error"])
        assert (
            error["code"]
            == {"oversize": "stream_event_too_large", "idle": "stream_idle_timeout", "disconnect": "upstream_error"}[
                outcome
            ]
        )


@pytest.mark.asyncio
async def test_routed_missing_helper_uses_python_parser_on_resolved_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = SubprocessNativeEgressClient(tmp_path / "missing-native-helper")
    completed = b'data: {"type":"response.completed","response":{"id":"resp_python"}}\n\n'
    hits: list[bytes] = []
    scan_count = 0
    original_scan = proxy_module._find_sse_separator

    def count_scan(buffer: bytes | bytearray, start: int = 0) -> tuple[int, int] | None:
        nonlocal scan_count
        scan_count += 1
        return original_scan(buffer, start)

    monkeypatch.setattr(proxy_module, "_find_sse_separator", count_scan)

    async def handler(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter, head: bytes, _body: bytes) -> None:
        hits.append(head)
        await _start_chunked_response(writer)
        await _write_chunk(writer, completed)
        await _finish_chunks(writer)

    async with _serve_http(handler) as proxy_url, aiohttp.ClientSession(trust_env=False) as session:
        route = ResolvedUpstreamRoute(
            mode="account_bound",
            pool_id="python-fallback",
            endpoint=ResolvedProxyEndpoint("python-proxy", "http", "127.0.0.1", urlsplit(proxy_url).port or 80),
        )
        trace = proxy_module.UpstreamProxyRouteTrace()
        client = CodexClient(session, native_egress_client=missing)
        events = [
            event
            async for event in stream_responses(
                _request("missing-helper"),
                {},
                "test-access-token",
                "test-account",
                base_url="http://upstream.invalid",
                route=route,
                route_trace=trace,
                codex_client=client,
                session=session,
                upstream_stream_transport_override="http",
                suppress_live_usage=True,
            )
        ]
    assert events == [completed.decode()]
    assert scan_count > 0
    assert len(hits) == 1
    assert hits[0].startswith(b"POST http://upstream.invalid/codex/responses ")
    assert trace == proxy_module.UpstreamProxyRouteTrace("account_bound", "python-fallback", "python-proxy", False)
    assert missing._process is None


@pytest.mark.asyncio
async def test_routed_owned_client_finishes_closing_in_cancelled_scope(
    monkeypatch: pytest.MonkeyPatch, native_worker: SubprocessNativeEgressClient
) -> None:
    class DelayedCloseSession(_UnexpectedPythonSession):
        async def close(self) -> None:
            await asyncio.sleep(0)
            await super().close()

    session = DelayedCloseSession()
    monkeypatch.setattr(proxy_module, "create_codex_session", lambda: session)
    first = b'data: {"type":"response.created","response":{"id":"resp_owned"}}\n\n'
    connection_closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, _head: bytes, _body: bytes) -> None:
        await _start_chunked_response(writer)
        await _write_chunk(writer, first)
        await reader.read()
        connection_closed.set()

    async with _serve_http(handler) as base_url:
        stream = _stream(base_url, native_worker, "owned-close", routed=True, own_codex_client=True)
        try:
            assert await anext(stream) == first.decode()
            with anyio.CancelScope() as scope:
                scope.cancel()
                await cast(AsyncGenerator[str, None], stream).aclose()
            await asyncio.wait_for(connection_closed.wait(), timeout=2)
            assert session.closed
            assert not native_worker._streams
        finally:
            await cast(AsyncGenerator[str, None], stream).aclose()


async def _compact(
    base_url: str,
    native_worker: SubprocessNativeEgressClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    routed: bool,
    marker: str = "compact-probe",
    session: aiohttp.ClientSession | None = None,
    own_codex_client: bool = False,
    selected_route: ResolvedUpstreamRoute | None = None,
    route_trace: proxy_module.UpstreamProxyRouteTrace | None = None,
) -> CompactResponsePayload:
    monkeypatch.setattr(
        proxy_module.get_settings(), "upstream_base_url", "http://upstream.invalid" if routed else base_url
    )
    monkeypatch.setattr(proxy_module, "discover_native_egress_client", lambda: native_worker)
    session = session if session is not None else cast(aiohttp.ClientSession, _UnexpectedPythonSession())
    route = selected_route or (
        ResolvedUpstreamRoute(
            mode="account_bound",
            pool_id="compact-probe",
            endpoint=ResolvedProxyEndpoint("compact-proxy", "http", "127.0.0.1", urlsplit(base_url).port or 80),
        )
        if routed
        else None
    )
    return await compact_responses(
        ResponsesCompactRequest(model="gpt-5.4", instructions="Summarize.", input=marker),
        {},
        "test-access-token",
        "test-account",
        session=session,
        route=route,
        codex_client=CodexClient(session, native_egress_client=native_worker)
        if routed and not own_codex_client
        else None,
        route_trace=route_trace,
        allow_direct_egress=not routed,
    )


_COMPACT_EVENTS = (
    b'data: {"type":"response.output_item.done","output_index":0,'
    b'"item":{"type":"compaction","encrypted_content":"compact-secret"}}\r\n\r\n'
    b'data: {"type":"response.completed","response":'
    b'{"object":"response","id":"resp_compact","output":[]}}\n\n'
)


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["text/event-stream", "Text/Event-Stream; charset=utf-8", None, ""])
async def test_native_compact_frames_and_returns_before_body_eof(
    monkeypatch: pytest.MonkeyPatch,
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
    content_type: str | None,
) -> None:
    closed = asyncio.Event()
    requests: list[dict[str, object]] = []

    def forbidden_python_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("compact native SSE must bypass the Python byte scanner")

    monkeypatch.setattr(proxy_module, "_find_sse_separator", forbidden_python_scan)

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, _head: bytes, body: bytes) -> None:
        requests.append(json.loads(body))
        await _start_chunked_response(writer, content_type=content_type)
        for offset in range(0, len(_COMPACT_EVENTS), 17):
            await _write_chunk(writer, _COMPACT_EVENTS[offset : offset + 17])
        await reader.read()
        closed.set()

    async with _serve_http(handler) as base_url:
        response = await asyncio.wait_for(_compact(base_url, native_worker, monkeypatch, routed=routed), timeout=2)
        await asyncio.wait_for(closed.wait(), timeout=2)
    assert response.object == "response.compaction"
    assert response.id == "resp_compact"
    assert response.model_extra is not None
    assert response.model_extra["output"][0]["encrypted_content"] == "compact-secret"
    assert len(requests) == 1
    assert requests[0]["stream"] is True
    assert requests[0]["store"] is False
    assert not native_worker._streams


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["json", "http_error", "terminal", "eof", "oversize", "disconnect"])
async def test_native_compact_preserves_payloads_errors_and_no_replay(
    monkeypatch: pytest.MonkeyPatch,
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
    outcome: str,
) -> None:
    hits: list[bytes] = []
    error = {"error": {"code": "rate_limit_exceeded", "type": "rate_limit_error", "message": "try later"}}
    json_body = {"object": "response.compact", "id": "compact_json", "padding": "x" * 512}
    monkeypatch.setattr(proxy_module.get_settings(), "max_sse_event_bytes", 128)

    async def handler(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter, head: bytes, _body: bytes) -> None:
        hits.append(head)
        await _start_chunked_response(
            writer,
            status="429 Too Many Requests" if outcome == "http_error" else "200 OK",
            content_type="application/json" if outcome in {"json", "http_error"} else "text/event-stream",
        )
        if outcome == "disconnect":
            return  # An incomplete chunked body, not a clean SSE EOF.
        body = {
            "json": json.dumps(json_body).encode(),
            "http_error": json.dumps(error).encode(),
            "terminal": b"data: " + json.dumps({"type": "error", **error}).encode() + b"\n\n",
            "eof": b'data: {"type":"response.created"}\n\n',
            "oversize": b"data: " + b"x" * 150,
        }[outcome]
        await _write_chunk(writer, body)
        await _finish_chunks(writer)

    async with _serve_http(handler) as base_url:
        if outcome == "json":
            response = await _compact(base_url, native_worker, monkeypatch, routed=routed)
            assert response.id == "compact_json"
            assert response.model_extra is not None and response.model_extra["padding"] == json_body["padding"]
        else:
            with pytest.raises(ProxyResponseError) as caught:
                await _compact(base_url, native_worker, monkeypatch, routed=routed)
            exc = caught.value
            assert exc.status_code == (429 if outcome in {"http_error", "terminal"} else 502)
            assert (
                exc.payload["error"]["code"]
                == {
                    "http_error": "rate_limit_exceeded",
                    "terminal": "rate_limit_exceeded",
                    "eof": "upstream_error",
                    "oversize": "stream_event_too_large",
                    "disconnect": "upstream_unavailable",
                }[outcome]
            )
            assert not exc.retryable_same_contract
            if outcome in {"http_error", "terminal"}:
                assert exc.payload["error"] == error["error"]
            if outcome == "disconnect":
                assert exc.failure_phase == "body_read"
    assert len(hits) == 1
    assert not native_worker._streams


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["idle", "total", "active"])
async def test_native_compact_preserves_idle_and_total_deadlines(
    monkeypatch: pytest.MonkeyPatch,
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
    mode: str,
) -> None:
    settings = proxy_module.get_settings()
    monkeypatch.setattr(settings, "upstream_compact_timeout_seconds", 0.2 if mode == "total" else None)
    monkeypatch.setattr(settings, "stream_idle_timeout_seconds", 0.15)
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, _head: bytes, _body: bytes) -> None:
        await _start_chunked_response(writer)
        if mode == "idle":
            await reader.read()
            closed.set()
            return
        for offset in range(0, len(_COMPACT_EVENTS), 30):
            await _write_chunk(writer, _COMPACT_EVENTS[offset : offset + 30])
            await asyncio.sleep(0.04)
        await reader.read()
        closed.set()

    async with _serve_http(handler) as base_url:
        started = asyncio.get_running_loop().time()
        if mode == "active":
            response = await asyncio.wait_for(_compact(base_url, native_worker, monkeypatch, routed=routed), timeout=2)
            assert response.id == "resp_compact"
            assert asyncio.get_running_loop().time() - started > 0.2
            await asyncio.wait_for(closed.wait(), timeout=2)
        else:
            with pytest.raises(ProxyResponseError) as caught:
                await asyncio.wait_for(_compact(base_url, native_worker, monkeypatch, routed=routed), timeout=2)
            assert caught.value.payload["error"]["code"] == (
                "stream_idle_timeout" if mode == "idle" else "upstream_unavailable"
            )
            assert not caught.value.retryable_same_contract
    assert not native_worker._streams


@pytest.mark.asyncio
async def test_native_compact_cancellation_closes_owned_resources_and_keeps_peer(
    monkeypatch: pytest.MonkeyPatch,
    native_worker: SubprocessNativeEgressClient,
    routed: bool,
) -> None:
    partial = asyncio.Event()
    closed = asyncio.Event()
    peer_ready = asyncio.Event()
    release_peer = asyncio.Event()
    owned_sessions: list[_UnexpectedPythonSession] = []

    class DelayedCloseSession(_UnexpectedPythonSession):
        async def close(self) -> None:
            await asyncio.sleep(0)
            await super().close()

    def create_session() -> DelayedCloseSession:
        session = DelayedCloseSession()
        owned_sessions.append(session)
        return session

    monkeypatch.setattr(proxy_module, "create_codex_session", create_session)
    monkeypatch.setattr(
        proxy_module, "CodexClient", lambda session: CodexClient(session, native_egress_client=native_worker)
    )

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, _head: bytes, body: bytes) -> None:
        await _start_chunked_response(writer)
        if b"cancel-compact" in body:
            await _write_chunk(writer, b'data: {"type":"response.created"')
            partial.set()
            await reader.read()
            closed.set()
        else:
            peer_ready.set()
            await release_peer.wait()
            await _write_chunk(writer, _COMPACT_EVENTS)
            await _finish_chunks(writer)

    async with _serve_http(handler) as base_url:
        scope_ready: asyncio.Future[anyio.CancelScope] = asyncio.get_running_loop().create_future()

        async def cancelled_request() -> None:
            with anyio.CancelScope() as scope:
                scope_ready.set_result(scope)
                await _compact(
                    base_url, native_worker, monkeypatch, routed=routed, marker="cancel-compact", own_codex_client=True
                )

        cancelled_task = asyncio.create_task(cancelled_request())
        peer_task = asyncio.create_task(
            _compact(base_url, native_worker, monkeypatch, routed=routed, marker="peer-compact")
        )
        try:
            scope = await scope_ready
            await asyncio.wait_for(partial.wait(), timeout=2)
            await asyncio.wait_for(peer_ready.wait(), timeout=2)
            process = native_worker._process
            scope.cancel()
            await asyncio.wait_for(cancelled_task, timeout=2)
            await asyncio.wait_for(closed.wait(), timeout=2)
            assert all(session.closed for session in owned_sessions)
            release_peer.set()
            response = await asyncio.wait_for(peer_task, timeout=2)
            assert response.id == "resp_compact"
            assert native_worker._process is process
            assert process is not None and process.returncode is None
        finally:
            release_peer.set()
            for task in (cancelled_task, peer_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(cancelled_task, peer_task, return_exceptions=True)
    assert not native_worker._streams


@pytest.mark.asyncio
@pytest.mark.parametrize("http_error", [False, True])
async def test_compact_missing_helper_preserves_python_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, routed: bool, http_error: bool
) -> None:
    missing = SubprocessNativeEgressClient(tmp_path / "missing-compact-helper")
    hits: list[bytes] = []
    scans = 0
    scan = proxy_module._find_sse_separator

    def count_scan(buffer: bytes | bytearray, start: int = 0) -> tuple[int, int] | None:
        nonlocal scans
        scans += 1
        return scan(buffer, start)

    monkeypatch.setattr(proxy_module, "_find_sse_separator", count_scan)

    async def handler(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter, head: bytes, _body: bytes) -> None:
        hits.append(head)
        await _start_chunked_response(
            writer,
            status="400 Bad Request" if http_error else "200 OK",
            content_type="text/plain" if http_error else "text/event-stream",
        )
        await _write_chunk(
            writer,
            b'{"error":{"code":"invalid_request_error","message":"compact input invalid"}}'
            if http_error
            else _COMPACT_EVENTS,
        )
        await _finish_chunks(writer)

    async with _serve_http(handler) as base_url, aiohttp.ClientSession(trust_env=False) as session:
        if http_error:
            with pytest.raises(ProxyResponseError) as caught:
                await _compact(base_url, missing, monkeypatch, routed=routed, session=session)
            assert caught.value.status_code == 400
            assert caught.value.payload["error"]["code"] == "invalid_request_error"
        else:
            response = await _compact(base_url, missing, monkeypatch, routed=routed, session=session)
            assert response.id == "resp_compact"
        assert not session.closed  # caller owns this session
    assert len(hits) == 1
    assert hits[0].startswith(b"POST http://upstream.invalid/" if routed else b"POST /codex/responses ")
    assert (scans > 0) is not http_error
    assert missing._process is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "disconnect"])
async def test_native_compact_routed_fallback_keeps_metadata_and_never_replays_accepted_post(
    monkeypatch: pytest.MonkeyPatch, native_worker: SubprocessNativeEgressClient, outcome: str
) -> None:
    accepted: list[bytes] = []
    replays: list[bytes] = []

    async def selected(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter, head: bytes, _body: bytes) -> None:
        accepted.append(head)
        await _start_chunked_response(writer)
        if outcome == "complete":
            await _write_chunk(writer, _COMPACT_EVENTS)
            await _finish_chunks(writer)

    async def replay(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter, head: bytes, _body: bytes) -> None:
        replays.append(head)
        await _start_chunked_response(writer)
        await _write_chunk(writer, _COMPACT_EVENTS)
        await _finish_chunks(writer)

    async with _serve_http(selected) as selected_url, _serve_http(replay) as replay_url:
        with socket.socket() as refused:
            refused.bind(("127.0.0.1", 0))  # reserved port without a listener
            route = ResolvedUpstreamRoute(
                mode="account_bound",
                pool_id="compact-fallback",
                endpoint=ResolvedProxyEndpoint("refused", "http", "127.0.0.1", refused.getsockname()[1]),
                fallbacks=(
                    ResolvedProxyEndpoint("selected", "http", "127.0.0.1", urlsplit(selected_url).port or 80),
                    ResolvedProxyEndpoint("replay", "http", "127.0.0.1", urlsplit(replay_url).port or 80),
                ),
            )
            trace = proxy_module.UpstreamProxyRouteTrace()
            request = _compact(
                selected_url, native_worker, monkeypatch, routed=True, selected_route=route, route_trace=trace
            )
            if outcome == "complete":
                assert (await request).id == "resp_compact"
            else:
                with pytest.raises(ProxyResponseError) as caught:
                    await request
                assert caught.value.failure_phase == "body_read"
                assert not caught.value.retryable_same_contract
    assert len(accepted) == 1
    assert not replays
    assert trace == proxy_module.UpstreamProxyRouteTrace("account_bound", "compact-fallback", "selected", True)
    assert not native_worker._streams
