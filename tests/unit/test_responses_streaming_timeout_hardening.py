from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator

import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.resilience.overload import local_overload_error
from app.core.utils.sse import SSE_KEEPALIVE_FRAME
from app.modules.proxy import api as proxy_api
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_virtual_wait_for_prefers_result_when_timeout_completes_same_tick() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    semaphore = asyncio.Semaphore(0)

    waiter = scheduler.create_task(scheduler.wait_for(semaphore.acquire(), timeout=0.1))

    async def release_at_deadline() -> None:
        await scheduler.sleep(0.1)
        semaphore.release()

    scheduler.create_task(release_at_deadline())
    await scheduler.drain()
    await scheduler.advance(0.1)

    assert await waiter is True
    assert semaphore.locked()
    await scheduler.cancel_owned_tasks()


async def _one_event_stream() -> AsyncIterator[str]:
    yield 'event: response.created\ndata: {"type":"response.created"}\n\n'


async def _delayed_429_stream(scheduler: VirtualScheduler) -> AsyncIterator[str]:
    # The first item only resolves after the startup probe has already timed
    # out, then the upstream raises a 429 -- mirroring the response-create
    # admission gate denying admission after the probe window elapsed.
    await scheduler.sleep(0.05)
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
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    loop = asyncio.get_running_loop()
    captured: list[str] = []
    loop.set_exception_handler(lambda _loop, context: captured.append(str(context.get("message", ""))))
    try:
        # The probe times out before the first item arrives, so it hands the
        # still-running task to the streamed response.
        probe_task = scheduler.create_task(
            proxy_api._probe_stream_startup_error(
                _delayed_429_stream(scheduler),
                timeout_seconds=0.01,
                scheduler=scheduler,
            )
        )
        await scheduler.drain()
        await scheduler.advance(0.01)
        stream, startup_error = await probe_task
        assert startup_error is None

        # Consuming the handed-off stream surfaces the upstream 429 to the
        # caller -- and must not also emit an "exception in shielded future"
        # diagnostic from the timed-out probe task.
        with pytest.raises(ProxyResponseError):
            await scheduler.advance(0.04)
            async for _ in stream:
                pass

        await scheduler.drain()
        gc.collect()
        await scheduler.drain()
    finally:
        loop.set_exception_handler(None)

    assert not any("shielded future" in m for m in captured), captured


@pytest.mark.asyncio
async def test_abandoned_startup_probe_task_does_not_warn() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    loop = asyncio.get_running_loop()
    captured: list[str] = []
    loop.set_exception_handler(lambda _loop, context: captured.append(str(context.get("message", ""))))
    try:
        probe_task = scheduler.create_task(
            proxy_api._probe_stream_startup_error(
                _delayed_429_stream(scheduler),
                timeout_seconds=0.01,
                scheduler=scheduler,
            )
        )
        await scheduler.drain()
        await scheduler.advance(0.01)
        stream, startup_error = await probe_task
        assert startup_error is None

        # Drop the wrapping generator without iterating it, as happens when the
        # request is torn down while still waiting on the admission gate. The
        # detached probe task then finishes with its 429 and must not log an
        # "exception was never retrieved" warning when it is collected.
        del stream
        await scheduler.advance(0.1)
        gc.collect()
        await scheduler.drain()
    finally:
        loop.set_exception_handler(None)

    leaked = [m for m in captured if "never retrieved" in m.lower() or "shielded future" in m]
    assert not leaked, f"probe task leaked an unretrieved exception: {captured}"


@pytest.mark.asyncio
async def test_capacity_ready_probe_timeout_uses_virtual_scheduler() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = scheduler.create_task(scheduler.sleep(1.0, result="response.created"))
    capacity_ready_event = proxy_api._CapacityStartupReadyEvent(clock=clock)
    capacity_ready_event.set()
    assert capacity_ready_event.set_at == clock.monotonic()
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.05,
            capacity_wait_event=asyncio.Event(),
            capacity_ready_event=capacity_ready_event,
            scheduler=scheduler,
            clock=clock,
        )
    )

    await scheduler.drain()
    assert probe_task.done() is False
    await scheduler.advance(0.05)

    assert await probe_task is False
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_capacity_signal_discovery_timeout_uses_virtual_scheduler() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = scheduler.create_task(scheduler.sleep(1.0, result="response.created"))
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.01,
            capacity_wait_event=asyncio.Event(),
            scheduler=scheduler,
        )
    )

    await scheduler.drain()
    await scheduler.advance(0.01)
    assert probe_task.done() is False
    await scheduler.advance(0.05)

    assert await probe_task is False
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_capacity_recovery_wait_ends_at_signal_timeout() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = scheduler.create_task(scheduler.sleep(1.0, result="response.created"))
    capacity_wait_event = asyncio.Event()
    capacity_wait_event.set()
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.01,
            capacity_wait_event=capacity_wait_event,
            scheduler=scheduler,
            clock=clock,
        )
    )

    await scheduler.drain()
    await scheduler.advance(0.01)
    assert probe_task.done() is False
    await scheduler.advance(proxy_api._CAPACITY_WAIT_MARKER_GRACE_SECONDS)

    assert await probe_task is False
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_capacity_recovery_ready_preserves_first_item_probe() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = scheduler.create_task(scheduler.sleep(0.03, result="response.created"))
    capacity_wait_event = asyncio.Event()
    capacity_wait_event.set()
    capacity_ready_event = proxy_api._CapacityStartupReadyEvent(clock=clock)

    async def mark_capacity_ready() -> None:
        await scheduler.sleep(0.02)
        capacity_ready_event.set()

    scheduler.create_task(mark_capacity_ready())
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.01,
            capacity_wait_event=capacity_wait_event,
            capacity_ready_event=capacity_ready_event,
            scheduler=scheduler,
            clock=clock,
        )
    )

    await scheduler.drain()
    await scheduler.advance(0.01)
    assert probe_task.done() is False
    await scheduler.advance(0.01)
    assert capacity_ready_event.set_at == clock.monotonic()
    assert capacity_wait_event.is_set() is False
    await scheduler.advance(0.01)

    assert await probe_task is True
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_capacity_recovery_ready_starts_post_ready_timeout() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = scheduler.create_task(scheduler.sleep(3.0, result="response.created"))
    capacity_wait_event = asyncio.Event()
    capacity_wait_event.set()
    capacity_ready_event = proxy_api._CapacityStartupReadyEvent(clock=clock)

    async def mark_capacity_ready() -> None:
        await scheduler.sleep(0.02)
        capacity_ready_event.set()

    scheduler.create_task(mark_capacity_ready())
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.01,
            capacity_wait_event=capacity_wait_event,
            capacity_ready_event=capacity_ready_event,
            scheduler=scheduler,
            clock=clock,
        )
    )

    await scheduler.drain()
    await scheduler.advance(0.01)
    await scheduler.advance(0.01)
    assert capacity_wait_event.is_set() is False
    await scheduler.advance(0.01)

    assert await probe_task is False
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_capacity_probe_immediate_completion_keeps_real_scheduler_default() -> None:
    async def first_item() -> str:
        return "response.created"

    first_task = asyncio.create_task(first_item())

    assert (
        await proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.05,
            capacity_wait_event=asyncio.Event(),
        )
        is True
    )
