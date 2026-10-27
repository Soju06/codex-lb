from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from types import AsyncGeneratorType
from typing import Any, cast

import pytest
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

import app.core.utils.sse as sse_module
from app.core.utils.sse import SSE_KEEPALIVE_FRAME, inject_sse_keepalives

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("after_heartbeat", [False, True])
async def test_keepalive_propagates_source_timeout(after_heartbeat: bool) -> None:
    """Propagate source timeouts both immediately and between heartbeat reads."""
    release = asyncio.Event()
    failed = asyncio.Event()
    closed = asyncio.Event()
    failure = TimeoutError("provider budget expired")

    async def source() -> AsyncIterator[str]:
        """Deliver output, then fail with the original provider timeout."""
        try:
            yield "data: first\n\n"
            if after_heartbeat:
                await release.wait()
            raise failure
        finally:
            failed.set()
            closed.set()

    stream = inject_sse_keepalives(source(), 0.01 if after_heartbeat else 30)
    try:
        assert await anext(stream) == "data: first\n\n"
        if after_heartbeat:
            assert await anext(stream) == SSE_KEEPALIVE_FRAME
            release.set()
            await failed.wait()
        with pytest.raises(TimeoutError) as caught:
            await anext(stream)
        assert caught.value is failure
        assert closed.is_set()
    finally:
        await cast(AsyncGenerator[str, None], stream).aclose()


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_streaming_response_does_not_flood_heartbeats_after_source_timeout(spec_version: str) -> None:
    """Stop the ASGI response instead of substituting heartbeats for failure."""
    bodies: list[bytes] = []
    closed = asyncio.Event()

    async def source() -> AsyncIterator[str]:
        """Close after the first output when the provider times out."""
        try:
            yield "data: first\n\n"
            raise TimeoutError("provider budget expired")
        finally:
            closed.set()

    async def send(message: Any) -> None:
        """Record response bodies and bound any erroneous heartbeat flood."""
        if message["type"] == "http.response.body":
            bodies.append(message["body"])
            # A finite assertion catches the pre-fix busy loop without hanging
            # this test's event loop or depending on an in-loop timeout.
            assert len(bodies) <= 3, "source timeout became an endless heartbeat stream"

    async def receive() -> Any:
        """Keep the client connected until the response finishes."""
        await asyncio.Event().wait()

    response = StreamingResponse(inject_sse_keepalives(source(), 30), media_type="text/event-stream")
    # ASGI 2.4 interprets OSError (including TimeoutError) as a disconnect.
    expected = ClientDisconnect if spec_version == "2.4" else TimeoutError
    with pytest.raises(expected):
        await response({"type": "http", "asgi": {"spec_version": spec_version}}, receive, send)
    assert bodies == [b"data: first\n\n"]
    assert closed.is_set()


@pytest.mark.parametrize("failure_type", [TimeoutError, RuntimeError, None, StopAsyncIteration])
async def test_completed_source_outcome_wins_idle_expiry(
    monkeypatch: pytest.MonkeyPatch, failure_type: type[Exception] | None
) -> None:
    """Honor settled chunks, exhaustion and original failures before heartbeats."""
    timers: list[asyncio.Timeout] = []
    real_timeout = asyncio.timeout
    closed = asyncio.Event()
    heartbeats: list[None] = []
    failure = (
        failure_type("provider failed at idle deadline")
        if failure_type is not None and failure_type is not StopAsyncIteration
        else None
    )
    release = asyncio.Event()
    real_wait = sse_module.wait_on_shared_future

    async def deliver_cancellation_after_settlement(pending: asyncio.Future[str]) -> str:
        """Settle the source before delivering the real idle cancellation."""
        try:
            return await real_wait(pending)
        except asyncio.CancelledError:
            assert timers[-1].expired()
            release.set()
            if failure_type is None:
                assert await real_wait(pending) == "data: settled\n\n"
            else:
                with pytest.raises(failure_type) as caught:
                    await real_wait(pending)
                if failure is not None:
                    assert caught.value is failure
            assert pending.done()
            raise

    def capture_timeout(delay: float | None) -> asyncio.Timeout:
        """Expose the real idle timer so expiry needs no wall-clock race."""
        timer = real_timeout(delay)
        timers.append(timer)
        return timer

    def on_keepalive() -> None:
        """Record any substitute heartbeat callback."""
        heartbeats.append(None)

    async def source() -> AsyncIterator[str]:
        """Queue expiry before source settlement, both before the waiter resumes."""
        try:
            yield "data: first\n\n"
            timers[-1].reschedule(asyncio.get_running_loop().time() - 1)
            await release.wait()
            if failure_type is StopAsyncIteration:
                return
            if failure is not None:
                raise failure
            yield "data: settled\n\n"
        finally:
            closed.set()

    monkeypatch.setattr(asyncio, "timeout", capture_timeout)
    monkeypatch.setattr(sse_module, "wait_on_shared_future", deliver_cancellation_after_settlement)
    stream = inject_sse_keepalives(source(), 30, on_keepalive=on_keepalive)
    generator = cast(AsyncGenerator[str, None], stream)
    try:
        assert await anext(stream) == "data: first\n\n"
        if failure_type is None:
            assert await anext(stream) == "data: settled\n\n"
            assert heartbeats == []
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
        else:
            with pytest.raises(failure_type) as caught:
                await anext(stream)
            if failure is not None:
                assert caught.value is failure
        assert heartbeats == []
        assert closed.is_set()
        assert isinstance(generator, AsyncGeneratorType)
        assert generator.ag_frame is None
    finally:
        await generator.aclose()
