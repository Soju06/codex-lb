from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.resilience.overload import local_overload_error
from app.core.utils.sse import SSE_KEEPALIVE_FRAME
from app.modules.proxy import api as proxy_api

pytestmark = pytest.mark.unit


async def _one_event_stream() -> AsyncIterator[str]:
    yield 'event: response.created\ndata: {"type":"response.created"}\n\n'


async def _delayed_429_stream() -> AsyncIterator[str]:
    # The first item only resolves after the startup probe has already timed
    # out, then the upstream raises a 429 -- mirroring the response-create
    # admission gate denying admission after the probe window elapsed.
    await asyncio.sleep(0.05)
    raise ProxyResponseError(429, local_overload_error("admission gate timed out", code="global_admission_timeout"))
    yield ""  # pragma: no cover - present only so this is an async generator


async def _blocked_stream(started: asyncio.Event, closed: asyncio.Event) -> AsyncIterator[str]:
    try:
        started.set()
        await asyncio.Event().wait()
        yield ""  # pragma: no cover - cancellation is the expected exit
    finally:
        closed.set()


async def _one_then_blocked_stream(closed: asyncio.Event) -> AsyncIterator[str]:
    try:
        yield 'event: response.created\ndata: {"type":"response.created"}\n\n'
        await asyncio.Event().wait()
    finally:
        closed.set()


@pytest.mark.asyncio
async def test_initial_sse_heartbeat_precedes_openai_contract_event() -> None:
    stream = proxy_api._prepend_initial_sse_heartbeat(
        _one_event_stream(),
        SSE_KEEPALIVE_FRAME,
        request_id="req_test",
        route_family="responses",
    )

    first = await anext(stream)
    second = await anext(stream)

    assert first == SSE_KEEPALIVE_FRAME
    assert "response.created" in second


@pytest.mark.asyncio
async def test_startup_probe_timeout_then_upstream_error_is_not_logged() -> None:
    loop = asyncio.get_running_loop()
    captured: list[str] = []
    loop.set_exception_handler(lambda _loop, context: captured.append(str(context.get("message", ""))))
    try:
        # The probe times out before the first item arrives, so it hands the
        # still-running task to the streamed response.
        stream, startup_error = await proxy_api._probe_stream_startup_error(
            _delayed_429_stream(),
            timeout_seconds=0.01,
        )
        assert startup_error is None

        # Consuming the handed-off stream surfaces the upstream 429 to the
        # caller -- and must not also emit an "exception in shielded future"
        # diagnostic from the timed-out probe task.
        with pytest.raises(ProxyResponseError):
            async for _ in stream:
                pass

        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(None)

    assert not any("shielded future" in m for m in captured), captured


@pytest.mark.asyncio
async def test_abandoned_startup_probe_task_does_not_warn() -> None:
    loop = asyncio.get_running_loop()
    captured: list[str] = []
    loop.set_exception_handler(lambda _loop, context: captured.append(str(context.get("message", ""))))
    try:
        stream, startup_error = await proxy_api._probe_stream_startup_error(
            _delayed_429_stream(),
            timeout_seconds=0.01,
        )
        assert startup_error is None

        # Drop the wrapping generator without iterating it, as happens when the
        # request is torn down while still waiting on the admission gate. The
        # detached probe task then finishes with its 429 and must not log an
        # "exception was never retrieved" warning when it is collected.
        del stream
        await asyncio.sleep(0.1)
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(None)

    leaked = [m for m in captured if "never retrieved" in m.lower() or "shielded future" in m]
    assert not leaked, f"probe task leaked an unretrieved exception: {captured}"


@pytest.mark.asyncio
async def test_startup_handoff_close_cancels_probe_and_closes_source_before_first_item() -> None:
    started = asyncio.Event()
    closed = asyncio.Event()
    stream, startup_error = await proxy_api._probe_stream_startup_error(
        _blocked_stream(started, closed),
        timeout_seconds=0.01,
    )
    assert startup_error is None
    await asyncio.wait_for(started.wait(), timeout=1)

    handoff = cast(proxy_api._FirstStreamHandoff, stream)
    await handoff.aclose()
    await asyncio.wait_for(closed.wait(), timeout=1)
    await handoff.aclose()


@pytest.mark.asyncio
async def test_startup_handoff_close_closes_source_after_first_item_wins_race() -> None:
    closed = asyncio.Event()
    stream, startup_error = await proxy_api._probe_stream_startup_error(
        _one_then_blocked_stream(closed),
        timeout_seconds=1,
    )
    assert startup_error is None

    await cast(proxy_api._FirstStreamHandoff, stream).aclose()
    await asyncio.wait_for(closed.wait(), timeout=1)


@pytest.mark.asyncio
async def test_streaming_response_closes_unstarted_source_owner_on_disconnect() -> None:
    closed = asyncio.Event()

    async def empty_body() -> AsyncIterator[str]:
        if False:
            yield ""

    class _Owner(AsyncIterator[str]):
        def __aiter__(self) -> _Owner:
            return self

        async def __anext__(self) -> str:
            raise StopAsyncIteration

        async def aclose(self) -> None:
            closed.set()

    response = proxy_api._SourceClosingStreamingResponse(
        empty_body(),
        source_owner=_Owner(),
        media_type="text/event-stream",
    )
    receive_calls = 0

    async def receive() -> dict[str, str]:
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.disconnect"}

    async def send(_message: dict[str, object]) -> None:
        return None

    await response(
        {"type": "http", "asgi": {"version": "3.0"}, "method": "GET", "path": "/"},
        receive,
        send,
    )

    assert receive_calls >= 1
    assert closed.is_set()


@pytest.mark.asyncio
async def test_streaming_response_bounds_blocked_source_close(monkeypatch: pytest.MonkeyPatch) -> None:
    close_started = asyncio.Event()
    close_cancelled = asyncio.Event()

    async def empty_body() -> AsyncIterator[str]:
        if False:
            yield ""

    class _Owner(AsyncIterator[str]):
        def __aiter__(self) -> _Owner:
            return self

        async def __anext__(self) -> str:
            raise StopAsyncIteration

        async def aclose(self) -> None:
            close_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                close_cancelled.set()
                raise

    monkeypatch.setattr(proxy_api, "_STREAM_SOURCE_CLOSE_TIMEOUT_SECONDS", 0.01)
    response = proxy_api._SourceClosingStreamingResponse(
        empty_body(),
        source_owner=_Owner(),
        media_type="text/event-stream",
    )

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(_message: dict[str, object]) -> None:
        return None

    await asyncio.wait_for(
        response(
            {"type": "http", "asgi": {"version": "3.0"}, "method": "GET", "path": "/"},
            receive,
            send,
        ),
        timeout=0.5,
    )
    assert close_started.is_set()
    await asyncio.sleep(0)
    assert close_cancelled.is_set()
    assert not proxy_api._STREAM_SOURCE_CLEANUP_TASKS


@pytest.mark.asyncio
async def test_streaming_response_preserves_cancellation_when_source_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancelled_response(
        _self: object,
        _scope: object,
        _receive: object,
        _send: object,
    ) -> None:
        raise asyncio.CancelledError

    async def empty_body() -> AsyncIterator[str]:
        if False:
            yield ""

    class _Owner(AsyncIterator[str]):
        def __aiter__(self) -> _Owner:
            return self

        async def __anext__(self) -> str:
            raise StopAsyncIteration

        async def aclose(self) -> None:
            raise RuntimeError("source close failed")

    monkeypatch.setattr(proxy_api.StreamingResponse, "__call__", cancelled_response)
    response = proxy_api._SourceClosingStreamingResponse(
        empty_body(),
        source_owner=_Owner(),
        media_type="text/event-stream",
    )

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await response({}, AsyncMock(), AsyncMock())
    assert isinstance(exc_info.value.__cause__, RuntimeError)
