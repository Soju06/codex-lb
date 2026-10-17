"""Model-source transport hardening against real aiohttp stub upstreams (#2123 WP-C1 P2).

The unit suite proves the phase classification with fakes; this module drives
the production ``aiohttp`` client, the dedicated model-source connector and
real sockets, with only the *deadline clock* virtualised (design v3 §8.2, I12).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import aiohttp
import pytest
from aiohttp import web

import app.core.clients.http as http_module
from app.core.clients.http import HttpClient
from app.db.models import ModelSource
from app.modules.model_sources import forwarding as forwarding_module
from app.modules.model_sources.forwarding import (
    SOURCE_FIRST_FRAME_DEADLINE_SECONDS,
    SOURCE_HEADER_DEADLINE_SECONDS,
    ModelSourceForwardingError,
)
from tests.integration.model_source_helpers import stub_source_upstreams
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.integration

_FORWARDING_LOGGER = "app.modules.model_sources.forwarding"
_STALLED_OPENS = 50


@pytest.fixture
async def source_upstream():
    async with stub_source_upstreams() as start:
        yield start


@pytest.fixture
async def http_client() -> AsyncIterator[HttpClient]:
    await http_module.close_http_client()
    client = await http_module.init_http_client()
    try:
        yield client
    finally:
        await http_module.close_http_client()


@dataclass
class _SilentTcpUpstream:
    """Accepts TCP connections, reads the request head, and never answers."""

    base_url: str
    received: int = 0
    eof_seen: int = 0
    _woken: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait_received(self, count: int, *, timeout: float = 5.0) -> None:
        async with asyncio.timeout(timeout):
            while self.received < count:
                self._woken.clear()
                await self._woken.wait()

    async def wait_eof(self, count: int, *, timeout: float = 5.0) -> None:
        async with asyncio.timeout(timeout):
            while self.eof_seen < count:
                self._woken.clear()
                await self._woken.wait()


@asynccontextmanager
async def _silent_tcp_upstream() -> AsyncIterator[_SilentTcpUpstream]:
    stub: _SilentTcpUpstream | None = None

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert stub is not None
        try:
            await reader.readuntil(b"\r\n\r\n")
            stub.received += 1
            stub._woken.set()
            # Stay silent until the client gives up and closes the connection.
            while await reader.read(65536):
                pass
            stub.eof_seen += 1
            stub._woken.set()
        except (asyncio.IncompleteReadError, ConnectionError):
            stub.eof_seen += 1
            stub._woken.set()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, backlog=_STALLED_OPENS * 2)
    port = server.sockets[0].getsockname()[1]
    stub = _SilentTcpUpstream(base_url=f"http://127.0.0.1:{port}/v1")
    try:
        yield stub
    finally:
        server.close()
        await asyncio.wait_for(server.wait_closed(), timeout=5)


def _source(base_url: str, *, source_id: str = "src_deadlines", timeout_seconds: float | None = None) -> ModelSource:
    return ModelSource(
        id=source_id,
        name=source_id,
        kind="openai_compatible",
        base_url=base_url,
        api_key_encrypted=None,
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=True,
        timeout_seconds=timeout_seconds,
    )


def _virtual() -> tuple[VirtualClock, VirtualScheduler]:
    clock = VirtualClock()
    return clock, VirtualScheduler(clock)


def _error_code(error: ModelSourceForwardingError) -> object:
    payload = error.payload["error"]
    assert isinstance(payload, dict)
    return payload["code"]


def _sse(event: dict[str, object]) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


def _acquired(client: HttpClient, *, model_source: bool) -> int:
    session = client.model_source_session if model_source else client.session
    assert session is not None
    connector = session.connector
    assert connector is not None
    return len(connector._acquired)


@pytest.mark.asyncio
async def test_header_deadline_fires_at_twenty_virtual_seconds_on_a_silent_source(http_client: HttpClient) -> None:
    clock, scheduler = _virtual()
    async with _silent_tcp_upstream() as stub:
        task = scheduler.create_task(
            forwarding_module.stream_responses(
                _source(stub.base_url), {"model": "m", "stream": True}, scheduler=scheduler, clock=clock
            )
        )
        await stub.wait_received(1)
        assert _acquired(http_client, model_source=True) == 1
        assert _acquired(http_client, model_source=False) == 0

        await scheduler.advance(SOURCE_HEADER_DEADLINE_SECONDS - 0.5)
        assert not task.done()

        await scheduler.advance(0.5)
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)
        with pytest.raises(ModelSourceForwardingError) as excinfo:
            task.result()

        error = excinfo.value
        assert error.status_code == 504
        assert error.timeout_phase == "header"
        assert _error_code(error) == "model_source_timeout"
        # The abandoned connection is closed, not parked in the pool.
        await stub.wait_eof(1)
        assert _acquired(http_client, model_source=True) == 0
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_first_frame_deadline_fires_at_thirty_virtual_seconds_and_closes_the_connection(
    http_client: HttpClient, source_upstream
) -> None:
    prepared = asyncio.Event()
    client_gone = asyncio.Event()

    async def headers_then_silence(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        prepared.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            client_gone.set()
            raise
        return response

    base_url = await source_upstream(headers_then_silence, handler_cancellation=True, shutdown_timeout=1.0)
    clock, scheduler = _virtual()
    task = scheduler.create_task(
        forwarding_module.stream_responses(
            _source(base_url), {"model": "m", "stream": True}, scheduler=scheduler, clock=clock
        )
    )
    await asyncio.wait_for(prepared.wait(), timeout=5)
    # aiohttp needs real loop turns to parse the status line off the socket.
    for _ in range(50):
        await asyncio.sleep(0.01)

    await scheduler.advance(SOURCE_FIRST_FRAME_DEADLINE_SECONDS - 0.5)
    assert not task.done()

    await scheduler.advance(0.5)
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)
    with pytest.raises(ModelSourceForwardingError) as excinfo:
        task.result()

    error = excinfo.value
    assert error.status_code == 504
    assert error.timeout_phase == "first_frame"
    assert _error_code(error) == "model_source_timeout"
    await asyncio.wait_for(client_gone.wait(), timeout=5)
    assert _acquired(http_client, model_source=True) == 0
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_chat_stream_slow_prompt_processing_outlives_the_responses_deadlines(
    http_client: HttpClient, source_upstream
) -> None:
    """A chat-completions source that sends its headers and first token only after prompt processing is not cut
    at the Responses header / first-frame deadlines (design v3 §2: chat completions are out of the hardening): the
    open returns at the headers, as on ``main``, and the body reads the first token under the total budget."""

    release_headers = asyncio.Event()
    release_token = asyncio.Event()
    prepared = asyncio.Event()
    token = b'data: {"id":"chatcmpl_slow","choices":[{"index":0,"delta":{"content":"hello"}}]}\n\n'
    final = (
        b'data: {"id":"chatcmpl_slow","choices":[],"usage":{"prompt_tokens":4,"completion_tokens":1}}\n\n'
        b"data: [DONE]\n\n"
    )

    async def slow_prompt_processing(request: web.Request) -> web.StreamResponse:
        await release_headers.wait()
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        prepared.set()
        await release_token.wait()
        await response.write(token)
        await response.write(final)
        await response.write_eof()
        return response

    base_url = await source_upstream(slow_prompt_processing)
    clock, scheduler = _virtual()
    task = scheduler.create_task(
        forwarding_module.stream_chat_completion(
            _source(base_url), {"model": "m", "stream": True}, scheduler=scheduler, clock=clock
        )
    )
    for _ in range(20):
        await asyncio.sleep(0.01)
    await scheduler.advance(SOURCE_HEADER_DEADLINE_SECONDS + 5)
    assert not task.done()

    release_headers.set()
    await asyncio.wait_for(prepared.wait(), timeout=5)
    # The open completes at the headers: nothing of the body has been read yet.
    stream = await asyncio.wait_for(task, timeout=5)
    assert stream.upstream_status_code == 200
    assert stream.usage_holder.first_frame_at is None
    assert _acquired(http_client, model_source=True) == 1

    first_read = asyncio.ensure_future(anext(stream.body))
    for _ in range(50):
        await asyncio.sleep(0.01)
    await scheduler.advance(SOURCE_FIRST_FRAME_DEADLINE_SECONDS + 5)
    assert not first_read.done()

    release_token.set()
    # One socket read may carry the token and the tail the stub wrote right behind it.
    first = await asyncio.wait_for(first_read, timeout=5)
    assert first.startswith(token)
    assert stream.usage_holder.first_frame_at is not None
    delivered = first + b"".join([chunk async for chunk in stream.body])

    assert delivered == token + final
    assert stream.usage_holder.usage is not None
    assert stream.usage_holder.usage.input_tokens == 4
    assert _acquired(http_client, model_source=True) == 0
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_chat_stream_client_leaving_during_prompt_processing_closes_the_source_connection(
    http_client: HttpClient, source_upstream
) -> None:
    """Cancelling the body's first read (a client that left while the source still processes the prompt) closes the
    source connection and returns the pooled lease at once instead of holding both until the first token."""

    prepared = asyncio.Event()
    client_gone = asyncio.Event()

    async def headers_then_prompt_processing(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        prepared.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            client_gone.set()
            raise
        return response

    base_url = await source_upstream(headers_then_prompt_processing, handler_cancellation=True, shutdown_timeout=1.0)
    stream = await asyncio.wait_for(
        forwarding_module.stream_chat_completion(_source(base_url), {"model": "m", "stream": True}), timeout=5
    )
    await asyncio.wait_for(prepared.wait(), timeout=5)
    assert _acquired(http_client, model_source=True) == 1

    first_read = asyncio.ensure_future(anext(stream.body))
    for _ in range(20):
        await asyncio.sleep(0.01)
    assert not first_read.done()
    first_read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_read

    await asyncio.wait_for(client_gone.wait(), timeout=5)
    assert _acquired(http_client, model_source=True) == 0
    assert stream.usage_holder.first_frame_at is None


@pytest.mark.asyncio
async def test_stalled_source_opens_never_touch_the_chatgpt_connector(http_client: HttpClient) -> None:
    clock, scheduler = _virtual()
    chatgpt_connector = http_client.session.connector
    assert chatgpt_connector is not None
    async with _silent_tcp_upstream() as stub:
        tasks = [
            scheduler.create_task(
                forwarding_module.stream_responses(
                    _source(stub.base_url, source_id=f"src_stall_{index}"),
                    {"model": "m", "stream": True},
                    scheduler=scheduler,
                    clock=clock,
                )
            )
            for index in range(_STALLED_OPENS)
        ]
        await stub.wait_received(_STALLED_OPENS, timeout=15)

        assert _acquired(http_client, model_source=True) == _STALLED_OPENS
        assert _acquired(http_client, model_source=False) == 0
        assert len(chatgpt_connector._conns) == 0
        assert len(chatgpt_connector._acquired_per_host) == 0

        await scheduler.advance(SOURCE_HEADER_DEADLINE_SECONDS)
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)

        assert all(isinstance(result, ModelSourceForwardingError) for result in results)
        assert {result.timeout_phase for result in results if isinstance(result, ModelSourceForwardingError)} == {
            "header"
        }
        await stub.wait_eof(_STALLED_OPENS, timeout=15)
        assert _acquired(http_client, model_source=True) == 0
        assert _acquired(http_client, model_source=False) == 0
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_saturated_source_pool_waits_for_a_slot_instead_of_failing_as_unreachable(
    source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dedicated pool at its per-host limit must queue for a free connection, not fail fast as
    ``model_source_unreachable``: aiohttp's ``connect`` timeout also bounds the wait for a pooled
    connection, so the source client arms only ``sock_connect`` (TCP establishment). Regression for the
    connect-vs-pool-wait delta -- otherwise a source with >``limit_per_host`` concurrent streams (exactly
    what an exhausted pool funnelling to one designated source produces) fails at the 10 s connect deadline
    with a misleading unreachable verdict, while ``max_concurrency`` unset promises 'unlimited' (codex review P2).
    """

    hold = asyncio.Event()

    async def created_then_hold(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(_sse({"type": "response.created", "response": {"id": "resp_slot"}}))
        try:
            await hold.wait()
        except asyncio.CancelledError:
            raise
        await response.write_eof()
        return response

    base_url = await source_upstream(created_then_hold, handler_cancellation=True, shutdown_timeout=1.0)
    # A short connect deadline keeps the test fast: on the buggy shape the pool
    # wait would be cut at this deadline as a ``ConnectionTimeoutError``.
    monkeypatch.setattr(forwarding_module, "SOURCE_CONNECT_DEADLINE_SECONDS", 0.3)
    connector = aiohttp.TCPConnector(limit_per_host=1)
    session = aiohttp.ClientSession(connector=connector)

    @asynccontextmanager
    async def lease() -> AsyncIterator[aiohttp.ClientSession]:
        yield session

    monkeypatch.setattr(forwarding_module, "lease_model_source_session", lease)
    source = _source(base_url, source_id="src_pool_wait")
    first = None
    second: asyncio.Task[object] | None = None
    try:
        # The first open already read its first chunk, so it holds the one slot.
        first = await forwarding_module.stream_responses(source, {"model": "m", "stream": True})
        second = asyncio.create_task(forwarding_module.stream_responses(source, {"model": "m", "stream": True}))
        await asyncio.sleep(0.3 * 4)
        assert not second.done(), "a saturated pool wait was cut short as a connect timeout (fail-fast unreachable)"

        # Freeing the slot lets the queued open acquire a connection and proceed:
        # it was genuinely waiting, not hung.
        await first.aclose()
        first = None
        second_stream = await asyncio.wait_for(second, timeout=10)
        second = None
        assert await asyncio.wait_for(anext(second_stream.body), timeout=5) == _sse(
            {"type": "response.created", "response": {"id": "resp_slot"}}
        )
        await second_stream.aclose()
    finally:
        hold.set()
        if second is not None:
            second.cancel()
            with pytest.raises((asyncio.CancelledError, ModelSourceForwardingError, aiohttp.ClientError)):
                await second
        if first is not None:
            await first.aclose()
        await session.close()


@pytest.mark.asyncio
async def test_idle_cap_applies_to_the_body_read_through_the_real_socket(
    http_client: HttpClient, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_idle"}})

    async def created_then_silence(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(created)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        return response

    base_url = await source_upstream(created_then_silence, handler_cancellation=True, shutdown_timeout=1.0)
    # The cap is ``min(configured, 300)``; a sub-second configured window keeps
    # the real socket timeout observable within the test budget.
    monkeypatch.setattr(forwarding_module, "get_settings", lambda: _settings_with_idle(0.3))

    stream = await forwarding_module.stream_responses(_source(base_url), {"model": "m", "stream": True})
    delivered: list[bytes] = []
    with pytest.raises(ModelSourceForwardingError) as excinfo:
        async with asyncio.timeout(5):
            async for chunk in stream.body:
                delivered.append(chunk)

    assert delivered == [created]
    assert excinfo.value.status_code == 504
    assert excinfo.value.timeout_phase == "idle"
    assert _error_code(excinfo.value) == "model_source_idle_timeout"
    assert stream.usage_holder.response_id == "resp_idle"
    assert stream.usage_holder.first_frame_at is not None
    for _ in range(20):
        if _acquired(http_client, model_source=True) == 0:
            break
        await asyncio.sleep(0.05)
    assert _acquired(http_client, model_source=True) == 0


def _settings_with_idle(seconds: float) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(stream_idle_timeout_seconds=seconds)


@pytest.mark.asyncio
async def test_empty_2xx_stream_is_rejected_before_any_client_byte(http_client: HttpClient, source_upstream) -> None:
    async def empty(_request: web.Request) -> web.StreamResponse:
        return web.Response(status=200, body=b"", content_type="text/event-stream")

    base_url = await source_upstream(empty)

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_responses(_source(base_url), {"model": "m", "stream": True})

    assert excinfo.value.status_code == 502
    assert excinfo.value.upstream_status_code == 200
    assert _error_code(excinfo.value) == "invalid_upstream_response"


@pytest.mark.asyncio
async def test_source_429_passes_through_with_retry_after(http_client: HttpClient, source_upstream) -> None:
    payload = {"error": {"message": "slow down", "type": "rate_limit_error", "code": "rate_limit_exceeded"}}

    async def limited(_request: web.Request) -> web.StreamResponse:
        return web.json_response(payload, status=429, headers={"Retry-After": "7"})

    base_url = await source_upstream(limited)

    with pytest.raises(ModelSourceForwardingError) as streamed:
        await forwarding_module.stream_responses(_source(base_url), {"model": "m", "stream": True})
    with pytest.raises(ModelSourceForwardingError) as forwarded:
        await forwarding_module.forward_responses(_source(base_url), {"model": "m"})

    for error in (streamed.value, forwarded.value):
        assert error.status_code == 429
        assert error.upstream_status_code == 429
        assert error.retry_after == "7"
        assert error.payload == payload


@pytest.mark.asyncio
async def test_source_404_passes_through_without_retry_after(http_client: HttpClient, source_upstream) -> None:
    payload = {"error": {"message": "unknown model", "type": "invalid_request_error", "code": "model_not_found"}}

    async def missing(_request: web.Request) -> web.StreamResponse:
        return web.json_response(payload, status=404)

    base_url = await source_upstream(missing)

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_responses(_source(base_url), {"model": "m", "stream": True})

    assert excinfo.value.status_code == 404
    assert excinfo.value.retry_after is None
    assert excinfo.value.payload == payload


@pytest.mark.asyncio
async def test_source_401_is_recoded_for_responses_and_kept_for_chat(
    http_client: HttpClient, source_upstream, caplog: pytest.LogCaptureFixture
) -> None:
    masked_key = "sk-live-****************ken"
    payload = {
        "error": {
            "message": f"Incorrect API key provided: {masked_key}",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    }

    async def unauthorized(_request: web.Request) -> web.StreamResponse:
        return web.json_response(payload, status=401)

    base_url = await source_upstream(unauthorized)
    caplog.set_level(logging.INFO, logger=_FORWARDING_LOGGER)

    with pytest.raises(ModelSourceForwardingError) as streamed:
        await forwarding_module.stream_responses(_source(base_url, source_id="src_401"), {"model": "m", "stream": True})
    with pytest.raises(ModelSourceForwardingError) as forwarded:
        await forwarding_module.forward_responses(_source(base_url, source_id="src_401"), {"model": "m"})
    with pytest.raises(ModelSourceForwardingError) as chat:
        await forwarding_module.stream_chat_completion(
            _source(base_url, source_id="src_401"), {"model": "m", "stream": True}
        )

    for error in (streamed.value, forwarded.value):
        assert error.status_code == 502
        assert error.upstream_status_code == 401
        assert _error_code(error) == "model_source_credentials_error"
        assert masked_key not in json.dumps(error.payload)
    assert chat.value.status_code == 401
    assert chat.value.payload == payload
    assert all(masked_key not in record.getMessage() for record in caplog.records)
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert all("src_401" in record.getMessage() and "401" in record.getMessage() for record in warnings)


@pytest.mark.asyncio
async def test_non_stream_forward_survives_a_slow_body_within_the_total_budget(
    http_client: HttpClient, source_upstream
) -> None:
    body = {"id": "resp_slow", "object": "response", "output": [], "usage": {"input_tokens": 1, "output_tokens": 1}}

    async def slow(_request: web.Request) -> web.StreamResponse:
        await asyncio.sleep(0.4)
        return web.json_response(body)

    base_url = await source_upstream(slow)

    result = await forwarding_module.forward_responses(_source(base_url, timeout_seconds=30), {"model": "m"})

    assert result.payload == body
    assert result.usage is not None
    assert result.upstream_status_code == 200


@pytest.mark.asyncio
async def test_stream_yields_the_first_frame_and_settles_usage_through_the_real_socket(
    http_client: HttpClient, source_upstream
) -> None:
    frames = [
        _sse({"type": "response.created", "response": {"id": "resp_ok"}}),
        _sse({"type": "response.output_item.added", "item": {"type": "message"}}),
        _sse({"type": "response.output_text.delta", "delta": "hello"}),
        _sse(
            {
                "type": "response.completed",
                "response": {"id": "resp_ok", "usage": {"input_tokens": 2, "output_tokens": 1}},
            }
        ),
    ]

    async def stream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for frame in frames:
            await response.write(frame)
            await asyncio.sleep(0.01)
        await response.write_eof()
        return response

    base_url = await source_upstream(stream)
    hooked: list[bool] = []

    async def hook(holder) -> None:
        hooked.append(holder.first_output_item_seen)

    opened = await forwarding_module.stream_responses(
        _source(base_url), {"model": "m", "stream": True}, on_first_content=hook
    )
    delivered = b"".join([chunk async for chunk in opened.body])

    assert delivered == b"".join(frames)
    assert hooked == [True]
    assert opened.usage_holder.usage is not None
    assert opened.usage_holder.usage.input_tokens == 2
    assert opened.usage_holder.terminal_kind == "completed"
    assert opened.usage_holder.delta_chars == len("hello")
    assert _acquired(http_client, model_source=True) == 0
