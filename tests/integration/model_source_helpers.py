"""Shared fixtures and helpers for model-source routing integration tests.

Extracted from ``test_model_source_routing.py`` so the transport-hardening and
source-dispatch suites (#2123 WP-C1) can drive the same stub upstream and
dashboard helpers without importing a test module.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from aiohttp import web

_UpstreamHandler: TypeAlias = Callable[[web.Request], Awaitable[web.StreamResponse]]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _create_model_source(
    async_client,
    *,
    name: str,
    model: str,
    base_url: str,
    input_per_1m: float | None = None,
    cached_input_per_1m: float | None = None,
    output_per_1m: float | None = None,
    audio_per_minute: float | None = None,
    raw_metadata_json: str | None = None,
    supports_responses: bool = False,
    supports_streaming: bool = True,
    supports_audio_transcriptions: bool = False,
    supports_embeddings: bool = False,
) -> str:
    model_entry: dict[str, object] = {
        "model": model,
        "displayName": model,
        "contextWindow": 8192,
        "maxOutputTokens": 1024,
        "supportsStreaming": supports_streaming,
        "supportsTools": True,
    }
    if raw_metadata_json is not None:
        model_entry["rawMetadataJson"] = raw_metadata_json
    if input_per_1m is not None:
        model_entry["inputPer1M"] = input_per_1m
    if cached_input_per_1m is not None:
        model_entry["cachedInputPer1M"] = cached_input_per_1m
    if output_per_1m is not None:
        model_entry["outputPer1M"] = output_per_1m
    if audio_per_minute is not None:
        model_entry["audioPerMinute"] = audio_per_minute
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": name,
            "baseUrl": base_url,
            "apiKey": f"token-{name}",
            "supportsChatCompletions": True,
            "supportsResponses": supports_responses,
            "supportsAudioTranscriptions": supports_audio_transcriptions,
            "supportsEmbeddings": supports_embeddings,
            "models": [model_entry],
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


async def _enable_api_key_auth(async_client) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert response.status_code == 200


@asynccontextmanager
async def stub_source_upstreams() -> AsyncIterator[Callable[..., Awaitable[str]]]:
    """Run stub OpenAI-compatible upstreams for one test; yields ``start(handler) -> base_url``.

    Test modules wrap this in a local ``source_upstream`` fixture (a fixture
    imported across modules trips ruff's F811 on every test parameter).
    ``start`` accepts ``handler_cancellation`` / ``shutdown_timeout`` for
    handlers that deliberately stall until the client leaves, so teardown does
    not wait out aiohttp's 60 s default drain.
    """

    runners: list[web.AppRunner] = []

    async def start(
        handler: _UpstreamHandler,
        *,
        handler_cancellation: bool = False,
        shutdown_timeout: float = 60.0,
    ) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        runner = web.AppRunner(app, handler_cancellation=handler_cancellation, shutdown_timeout=shutdown_timeout)
        await runner.setup()
        port = _free_port()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        runners.append(runner)
        return f"http://127.0.0.1:{port}/v1"

    try:
        yield start
    finally:
        for runner in runners:
            await runner.cleanup()


@dataclass(slots=True)
class _AsgiStream:
    """Drive the ASGI app for one request while owning ``receive``/``send``.

    ``disconnect()`` queues the ``http.disconnect`` message a departing client
    produces, so a route's reaction to a real client departure can be asserted
    without an HTTP client in between. With ``stall_response_start`` the
    ``http.response.start`` send records the status and headers and then never
    completes -- the client's socket is gone before the first write lands -- so
    the only way out is the cancellation a queued ``http.disconnect`` triggers
    and the response body is never iterated (the pre-body window).
    """

    app: Any
    path: str
    headers: dict[str, str]
    body: bytes
    stall_response_start: bool = False
    status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    chunks: list[bytes] = field(default_factory=list)
    _disconnect: asyncio.Event = field(default_factory=asyncio.Event)
    _chunk_arrived: asyncio.Event = field(default_factory=asyncio.Event)
    _started: asyncio.Event = field(default_factory=asyncio.Event)
    _body_sent: bool = False

    def disconnect(self) -> None:
        self._disconnect.set()

    def received(self) -> bytes:
        return b"".join(self.chunks)

    async def wait_for_response_start(self, *, timeout: float = 10.0) -> None:
        """Block until the route sent ``http.response.start`` (status and headers)."""

        try:
            await asyncio.wait_for(self._started.wait(), timeout=timeout)
        except TimeoutError:
            raise AssertionError("the response status and headers were not sent") from None

    async def wait_for_text(self, needle: str, *, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while needle.encode() not in self.received():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"{needle!r} not received; got {self.received()!r}")
            self._chunk_arrived.clear()
            try:
                await asyncio.wait_for(self._chunk_arrived.wait(), timeout=remaining)
            except TimeoutError:
                raise AssertionError(f"{needle!r} not received; got {self.received()!r}") from None

    async def _receive(self) -> dict[str, Any]:
        if not self._body_sent:
            self._body_sent = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.response_headers = {key.decode().lower(): value.decode() for key, value in message.get("headers", [])}
            self._started.set()
            if self.stall_response_start:
                await asyncio.Event().wait()
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                self.chunks.append(bytes(body))
                self._chunk_arrived.set()

    async def run(self) -> None:
        raw_headers = [(key.lower().encode(), value.encode()) for key, value in self.headers.items()]
        raw_headers.append((b"host", b"testserver"))
        raw_headers.append((b"content-type", b"application/json"))
        raw_headers.append((b"content-length", str(len(self.body)).encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": ("127.0.0.1", 41000),
            "server": ("testserver", 80),
        }
        await self.app(scope, self._receive, self._send)
