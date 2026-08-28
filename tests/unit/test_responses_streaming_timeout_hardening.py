from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator

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
