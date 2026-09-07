"""Scheduler ownership of request-log persistence spawns and rewrite retries.

``_RequestLogMixin`` detaches the request-log INSERT and the images model
rewrite as tasks; the rewrite then waits on this request's pending insert
and backs off while the row is missing. All of that runs through the owner's
scheduler and clock so a simulation owns the tasks and drives the 30 s
backstop without wall time. ``drain_persistence_tasks`` (shutdown) stays on
the loop clock by design.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.modules.proxy import service as proxy_service
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


class _RecordingVirtualScheduler(VirtualScheduler):
    def __init__(self, clock: VirtualClock) -> None:
        super().__init__(clock)
        self.task_names: list[str | None] = []
        self.sleeps: list[float] = []
        self.wait_for_timeouts: list[float | None] = []

    def create_task(self, coroutine: Any, *, name: str | None = None) -> asyncio.Task[Any]:
        self.task_names.append(name)
        return super().create_task(coroutine, name=name)

    async def sleep(self, delay: float, result: Any = None) -> Any:
        self.sleeps.append(delay)
        return await super().sleep(delay, result=result)

    async def wait_for(self, awaitable: Awaitable[Any], timeout: float | None) -> Any:
        self.wait_for_timeouts.append(timeout)
        return await super().wait_for(awaitable, timeout)


class _RequestLogsRepo:
    def __init__(self, rowcounts: list[int] | None = None) -> None:
        self.rows: list[dict[str, object]] = []
        self.update_calls: list[tuple[str, str]] = []
        self.rowcounts = rowcounts if rowcounts is not None else [1]
        self.insert_release: asyncio.Event | None = None

    async def add_log(self, **kwargs: object) -> None:
        if self.insert_release is not None:
            await self.insert_release.wait()
        self.rows.append(dict(kwargs))

    async def update_model_for_request(self, request_id: str, model: str) -> int:
        self.update_calls.append((request_id, model))
        if len(self.rowcounts) > 1:
            return self.rowcounts.pop(0)
        return self.rowcounts[0]


class _RepoContext:
    def __init__(self, repos: Any) -> None:
        self._repos = repos

    async def __aenter__(self) -> Any:
        return self._repos

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _service(scheduler: _RecordingVirtualScheduler, request_logs: _RequestLogsRepo) -> proxy_service.ProxyService:
    repos = SimpleNamespace(request_logs=request_logs)
    return proxy_service.ProxyService(
        cast(Any, lambda: _RepoContext(repos)),
        clock=scheduler.clock,
        scheduler=scheduler,
    )


async def _write_log(service: proxy_service.ProxyService, request_id: str) -> None:
    await service._write_request_log(
        account_id="acc-log",
        api_key=None,
        request_id=request_id,
        model="gpt-5.5",
        latency_ms=12,
        status="success",
    )


@pytest.mark.asyncio
async def test_write_request_log_spawns_scheduler_owned_persistence_task() -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock())
    request_logs = _RequestLogsRepo()
    service = _service(scheduler, request_logs)

    await _write_log(service, "req-log")

    assert scheduler.task_names == ["proxy-request-log-req-log"]
    (task,) = scheduler.owned_tasks
    assert task in service._request_log_tasks
    assert request_logs.rows == []
    await scheduler.drain()
    assert [row["request_id"] for row in request_logs.rows] == ["req-log"]
    assert not service._request_log_tasks
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_rewrite_request_log_model_retries_on_virtual_time() -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock())
    request_logs = _RequestLogsRepo(rowcounts=[0, 0, 1])
    service = _service(scheduler, request_logs)

    await service.rewrite_request_log_model("req-rewrite", "gpt-image-1")

    assert scheduler.task_names == ["proxy-request-log-rewrite-req-rewrite"]
    (task,) = scheduler.owned_tasks
    assert task in service._request_log_tasks
    await scheduler.drain()
    assert request_logs.update_calls == [("req-rewrite", "gpt-image-1")]
    assert scheduler.sleeps == [0.05]
    assert not task.done()

    await scheduler.advance(0.05)
    assert len(request_logs.update_calls) == 2
    assert scheduler.sleeps == [0.05, 0.1]
    assert not task.done()

    await scheduler.advance(0.1)

    assert len(request_logs.update_calls) == 3
    assert task.done()
    assert not service._request_log_tasks
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_rewrite_request_log_model_gives_up_at_virtual_deadline(caplog: pytest.LogCaptureFixture) -> None:
    """Thirty virtual seconds, not thirty real ones, bound the rewrite backstop."""

    scheduler = _RecordingVirtualScheduler(VirtualClock(monotonic_value=500.0))
    request_logs = _RequestLogsRepo(rowcounts=[0])
    service = _service(scheduler, request_logs)

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.service"):
        await service.rewrite_request_log_model("req-missing", "gpt-image-1")
        (task,) = scheduler.owned_tasks
        await scheduler.advance(29.0)
        assert not task.done()
        # The deadline is observed after the backoff sleep that straddles it,
        # so the rewrite ends within one maximum backoff past the 30 s budget.
        await scheduler.advance(1.8)

    assert task.done()
    assert 530.0 <= scheduler.clock.monotonic() <= 531.8
    assert len(request_logs.update_calls) > 1
    assert all(delay <= 0.8 for delay in scheduler.sleeps)
    assert any("never appeared" in record.getMessage() for record in caplog.records)
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_rewrite_request_log_model_waits_for_pending_insert_through_scheduler() -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock())
    request_logs = _RequestLogsRepo(rowcounts=[1])
    request_logs.insert_release = asyncio.Event()
    service = _service(scheduler, request_logs)

    await _write_log(service, "req-pending")
    await service.rewrite_request_log_model("req-pending", "gpt-image-1")
    await scheduler.drain()

    insert_task, rewrite_task = (
        next(task for task in scheduler.owned_tasks if task.get_name() == name)
        for name in ("proxy-request-log-req-pending", "proxy-request-log-rewrite-req-pending")
    )
    assert not insert_task.done()
    assert not rewrite_task.done()
    # The rewrite waits on the insert through the scheduler, bounded by the
    # remaining virtual budget, and has not probed the row yet.
    assert scheduler.wait_for_timeouts == [pytest.approx(30.0)]
    assert request_logs.update_calls == []

    request_logs.insert_release.set()
    await scheduler.drain()

    assert insert_task.done()
    assert rewrite_task.done()
    assert request_logs.update_calls == [("req-pending", "gpt-image-1")]
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_rewrite_request_log_model_insert_wait_timeout_shields_the_insert() -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock())
    request_logs = _RequestLogsRepo(rowcounts=[0])
    request_logs.insert_release = asyncio.Event()
    service = _service(scheduler, request_logs)

    await _write_log(service, "req-stuck")
    await service.rewrite_request_log_model("req-stuck", "gpt-image-1")
    await scheduler.drain()
    insert_task = next(task for task in scheduler.owned_tasks if task.get_name() == "proxy-request-log-req-stuck")
    rewrite_task = next(
        task for task in scheduler.owned_tasks if task.get_name() == "proxy-request-log-rewrite-req-stuck"
    )

    await scheduler.advance(30.0)

    # The bounded wait timed out, the row probe ran once, the deadline ended
    # the rewrite, and the shielded insert is still alive and uncancelled.
    assert rewrite_task.done()
    assert request_logs.update_calls == [("req-stuck", "gpt-image-1")]
    assert not insert_task.done()
    assert not insert_task.cancelled()
    request_logs.insert_release.set()
    await scheduler.drain()
    assert insert_task.done()
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_stream_preflight_error_latency_uses_owner_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock(monotonic_value=12.5))
    request_logs = _RequestLogsRepo()
    service = _service(scheduler, request_logs)
    # A wall clock far ahead of the virtual one: a leaked ``time.monotonic``
    # read would report an absurd latency.
    monkeypatch.setattr(time, "monotonic", lambda: 1_000_000.0)

    await service._write_stream_preflight_error(
        account_id="acc-preflight",
        api_key=None,
        request_id="req-preflight",
        model="gpt-5.5",
        start=10.0,
        error_code="upstream_unavailable",
        error_message="unavailable",
        reasoning_effort=None,
        service_tier=None,
    )
    await scheduler.drain()

    (row,) = request_logs.rows
    assert row["latency_ms"] == 2500
    assert row["status"] == "error"
