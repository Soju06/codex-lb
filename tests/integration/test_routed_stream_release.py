from __future__ import annotations

import asyncio
import gc
import socket
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from aiohttp import web

import app.core.clients.codex as codex_module
import app.core.clients.proxy as proxy_module
from app.core.clients.proxy import stream_responses
from app.core.openai.requests import ResponsesRequest
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.integration

_SSE_CREATED = (
    b'data: {"type":"response.created","response":{"id":"resp_1","object":"response","status":"in_progress"}}\n\n'
)
_SSE_COMPLETED = (
    b'data: {"type":"response.completed","response":{"id":"resp_1","object":"response","status":"completed"}}\n\n'
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _HoldingUpstream:
    """aiohttp.web app standing in for an HTTP forward proxy that keeps the tunnel open after the SSE frames."""

    def __init__(self, frames: bytes) -> None:
        self.frames = frames
        self.port = 0
        self._runner: web.AppRunner | None = None

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(self.frames)
        try:
            await asyncio.sleep(3600)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return response

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, shutdown_timeout=0.1)
        await self._runner.setup()
        self.port = _free_port()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


@pytest.fixture
def aiohttp_routed_egress(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Force the aiohttp fallback transport and capture every raw upstream response it produces."""

    monkeypatch.setattr(codex_module, "discover_native_egress_client", lambda: None)
    captured: list[Any] = []

    class _RecordingCodexClient(codex_module.CodexClient):
        async def request_with_route_metadata(self, *args: Any, **kwargs: Any) -> codex_module.CodexRequestResult:
            result = await super().request_with_route_metadata(*args, **kwargs)
            captured.append(result.response)
            return result

    monkeypatch.setattr(proxy_module, "CodexClient", _RecordingCodexClient)
    return captured


@pytest.fixture
async def unclosed_connection_events() -> AsyncIterator[list[dict[str, Any]]]:
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    events: list[dict[str, Any]] = []
    # Flush garbage from earlier tests so only this test's teardown is measured.
    gc.collect()

    def _record(active_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if context.get("message") == "Unclosed connection":
            events.append(context)
            return
        if previous is not None:
            previous(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(_record)
    try:
        yield events
    finally:
        loop.set_exception_handler(previous)


async def _collect_routed_stream(port: int) -> list[str]:
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "127.0.0.1", port),
    )
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)
    return [
        event
        async for event in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            base_url="http://upstream.invalid/backend-api",
            raise_for_status=True,
            session=cast(Any, object()),
            upstream_stream_transport_override="http",
            route=route,
            allow_direct_egress=False,
        )
    ]


async def _assert_released(captured: list[Any], unclosed: list[dict[str, Any]]) -> None:
    assert len(captured) == 1
    response = captured[0]
    assert response.closed is True
    assert response.connection is None
    gc.collect()
    await asyncio.sleep(0)
    gc.collect()
    assert unclosed == []


async def test_routed_stream_terminal_event_releases_aiohttp_connection(
    aiohttp_routed_egress: list[Any],
    unclosed_connection_events: list[dict[str, Any]],
) -> None:
    upstream = _HoldingUpstream(_SSE_CREATED + _SSE_COMPLETED)
    await upstream.start()
    try:
        events = await _collect_routed_stream(upstream.port)
    finally:
        await upstream.stop()

    # Forwarded bytes are the upstream frames verbatim; release runs after the last one.
    assert "".join(events).encode() == _SSE_CREATED + _SSE_COMPLETED
    await _assert_released(aiohttp_routed_egress, unclosed_connection_events)


async def test_routed_stream_idle_timeout_releases_aiohttp_connection(
    aiohttp_routed_egress: list[Any],
    unclosed_connection_events: list[dict[str, Any]],
) -> None:
    upstream = _HoldingUpstream(_SSE_CREATED)
    await upstream.start()
    try:
        with proxy_module.override_stream_timeouts(idle_timeout_seconds=0.05):
            events = await _collect_routed_stream(upstream.port)
    finally:
        await upstream.stop()

    assert events[0].encode() == _SSE_CREATED
    assert len(events) == 2
    assert '"stream_idle_timeout"' in events[1]
    await _assert_released(aiohttp_routed_egress, unclosed_connection_events)
