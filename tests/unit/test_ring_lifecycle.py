from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.utils.periodic import PeriodicPhaseEvent
from app.modules.proxy import ring_lifecycle as ring_lifecycle_module
from app.modules.proxy.ring_lifecycle import (
    BridgePeriodicShutdownResult,
    BridgePeriodicStopResult,
    BridgeRingPeriodicLifecycle,
    stop_bridge_periodic_work,
)
from app.modules.proxy.ring_membership import RingMembershipService

pytestmark = pytest.mark.unit


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_phase", ["durable_ownership", "idle_sweep", "cap_partition"])
async def test_blocked_maintenance_does_not_delay_heartbeat_or_other_phases(blocked_phase: str) -> None:
    blocked_started = asyncio.Event()
    release_blocked = asyncio.Event()
    calls = {"heartbeat": 0, "durable_ownership": 0, "idle_sweep": 0, "cap_partition": 0}
    active = 0
    max_active = 0

    async def heartbeat() -> None:
        calls["heartbeat"] += 1

    def maintenance(phase: str):
        async def run() -> None:
            nonlocal active, max_active
            calls[phase] += 1
            if phase != blocked_phase:
                return
            active += 1
            max_active = max(max_active, active)
            blocked_started.set()
            try:
                await release_blocked.wait()
            finally:
                active -= 1

        return run

    lifecycle = BridgeRingPeriodicLifecycle(
        heartbeat=heartbeat,
        durable_ownership=maintenance("durable_ownership"),
        idle_sweep=maintenance("idle_sweep"),
        cap_partition=maintenance("cap_partition"),
        interval_seconds=0.01,
        heartbeat_deadline_seconds=0.02,
        maintenance_deadline_seconds=0.01,
        restart_delay_seconds=0.01,
    )
    lifecycle.start()
    try:
        await asyncio.wait_for(blocked_started.wait(), timeout=1)
        await _wait_until(
            lambda: (
                calls["heartbeat"] >= 3
                and all(calls[phase] >= 2 for phase in calls if phase not in {"heartbeat", blocked_phase})
            )
        )
        assert calls[blocked_phase] == 1
        assert max_active == 1
    finally:
        release_blocked.set()
        result = await lifecycle.stop(timeout_seconds=1)

    assert result.heartbeat_stopped is True
    assert result.all_stopped is True


@pytest.mark.asyncio
async def test_each_periodic_phase_runs_in_a_distinct_owned_task() -> None:
    phase_tasks: dict[str, asyncio.Task[None]] = {}

    def operation(phase: str):
        async def run() -> None:
            task = asyncio.current_task()
            assert task is not None
            phase_tasks.setdefault(phase, task)

        return run

    lifecycle = BridgeRingPeriodicLifecycle(
        heartbeat=operation("heartbeat"),
        durable_ownership=operation("durable_ownership"),
        idle_sweep=operation("idle_sweep"),
        cap_partition=operation("cap_partition"),
        interval_seconds=0.01,
        heartbeat_deadline_seconds=0.05,
        maintenance_deadline_seconds=0.05,
        restart_delay_seconds=0.01,
    )
    lifecycle.start()
    try:
        await _wait_until(lambda: len(phase_tasks) == 4)
    finally:
        result = await lifecycle.stop(timeout_seconds=1)

    assert result.all_stopped is True
    assert len(set(phase_tasks.values())) == 4
    assert {task.get_name() for task in phase_tasks.values()} == {
        "periodic-heartbeat-attempt",
        "periodic-durable_ownership-attempt",
        "periodic-idle_sweep-attempt",
        "periodic-cap_partition-attempt",
    }


@pytest.mark.asyncio
async def test_cap_partition_maintenance_surfaces_retained_membership_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main

    list_active = AsyncMock(return_value=["pod-a"])
    service = cast(RingMembershipService, AsyncMock(list_active=list_active))
    refresh = AsyncMock(return_value=False)
    monkeypatch.setattr(main, "refresh_cap_partition", refresh)

    with pytest.raises(RuntimeError, match="membership read failed"):
        await main.run_cap_partition_maintenance(service, "pod-a")

    refresh.assert_awaited_once_with(list_active, "pod-a")


@pytest.mark.asyncio
async def test_periodic_lifecycle_records_bounded_metrics_and_structured_recovery_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeGauge:
        def __init__(self) -> None:
            self.values: list[float] = []

        def set(self, value: float) -> None:
            self.values.append(value)

    class FakeCounter:
        def __init__(self) -> None:
            self.values: dict[tuple[str, str] | None, int] = {}

        def labels(self, *, phase: str, outcome: str) -> FakeCounterChild:
            return FakeCounterChild(self, (phase, outcome))

        def inc(self) -> None:
            self.values[None] = self.values.get(None, 0) + 1

    class FakeCounterChild:
        def __init__(self, counter: FakeCounter, key: tuple[str, str]) -> None:
            self._counter = counter
            self._key = key

        def inc(self) -> None:
            self._counter.values[self._key] = self._counter.values.get(self._key, 0) + 1

    gauge = FakeGauge()
    heartbeat_failures = FakeCounter()
    maintenance = FakeCounter()
    monkeypatch.setattr(
        ring_lifecycle_module.prometheus_metrics,
        "bridge_ring_heartbeat_last_success_timestamp_seconds",
        gauge,
    )
    monkeypatch.setattr(
        ring_lifecycle_module.prometheus_metrics,
        "bridge_ring_heartbeat_failures_total",
        heartbeat_failures,
    )
    monkeypatch.setattr(
        ring_lifecycle_module.prometheus_metrics,
        "bridge_ring_maintenance_total",
        maintenance,
    )
    caplog.set_level(logging.INFO, logger=ring_lifecycle_module.__name__)
    heartbeat_calls = 0

    async def heartbeat() -> None:
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        if heartbeat_calls == 1:
            raise RuntimeError("heartbeat failed")

    async def durable_ownership() -> None:
        raise ValueError("reconcile failed")

    async def noop() -> None:
        return None

    lifecycle = BridgeRingPeriodicLifecycle(
        heartbeat=heartbeat,
        durable_ownership=durable_ownership,
        idle_sweep=noop,
        cap_partition=noop,
        interval_seconds=0.01,
        heartbeat_deadline_seconds=0.05,
        maintenance_deadline_seconds=0.05,
        restart_delay_seconds=0.01,
    )
    lifecycle.start()
    try:
        await _wait_until(lambda: heartbeat_calls >= 2)
        await _wait_until(lambda: maintenance.values.get(("durable_ownership", "failure"), 0) >= 1)
    finally:
        assert (await lifecycle.stop(timeout_seconds=1)).all_stopped

    assert len(gauge.values) >= 2
    assert heartbeat_failures.values[None] == 1
    assert set(key for key in maintenance.values if key is not None) <= {
        ("durable_ownership", "success"),
        ("durable_ownership", "failure"),
        ("durable_ownership", "timeout"),
        ("idle_sweep", "success"),
        ("idle_sweep", "failure"),
        ("idle_sweep", "timeout"),
        ("cap_partition", "success"),
        ("cap_partition", "failure"),
        ("cap_partition", "timeout"),
    }
    heartbeat_failure_record = next(
        record
        for record in caplog.records
        if record.message == "Bridge ring heartbeat attempt did not complete successfully"
    )
    assert getattr(heartbeat_failure_record, "phase") == "heartbeat"
    assert getattr(heartbeat_failure_record, "outcome") == "failure"
    assert getattr(heartbeat_failure_record, "consecutive_failures") == 1
    recovery_record = next(record for record in caplog.records if record.message == "Bridge ring heartbeat recovered")
    assert getattr(recovery_record, "phase") == "heartbeat"
    assert getattr(recovery_record, "consecutive_failures") == 1

    escaped = RuntimeError("worker escaped")
    lifecycle._record_supervisor_exit("heartbeat", escaped, 1.0)
    supervisor_record = next(
        record for record in caplog.records if record.message == "Bridge periodic worker exited unexpectedly"
    )
    assert getattr(supervisor_record, "phase") == "heartbeat"
    assert getattr(supervisor_record, "outcome") == "failure"
    assert getattr(supervisor_record, "error_type") == "RuntimeError"
    assert getattr(supervisor_record, "restart_delay_seconds") == 1.0

    lifecycle._record_maintenance_event(
        PeriodicPhaseEvent(
            phase="durable_ownership",
            outcome="timeout",
            elapsed_seconds=30.0,
            consecutive_failures=2,
        )
    )
    assert maintenance.values[("durable_ownership", "timeout")] == 1
    timeout_record = next(
        record
        for record in caplog.records
        if record.message == "Bridge ring maintenance did not complete successfully"
        and getattr(record, "outcome", None) == "timeout"
    )
    assert getattr(timeout_record, "phase") == "durable_ownership"
    assert getattr(timeout_record, "elapsed_seconds") == 30.0
    assert getattr(timeout_record, "consecutive_failures") == 2


@pytest.mark.asyncio
async def test_periodic_lifecycle_shutdown_cancels_and_drains_every_phase() -> None:
    started = {phase: asyncio.Event() for phase in ("heartbeat", "durable_ownership", "idle_sweep", "cap_partition")}
    cancelled = {phase: asyncio.Event() for phase in started}

    def operation(phase: str):
        async def run() -> None:
            started[phase].set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled[phase].set()
                raise

        return run

    lifecycle = BridgeRingPeriodicLifecycle(
        heartbeat=operation("heartbeat"),
        durable_ownership=operation("durable_ownership"),
        idle_sweep=operation("idle_sweep"),
        cap_partition=operation("cap_partition"),
        interval_seconds=0.01,
        heartbeat_deadline_seconds=1,
        maintenance_deadline_seconds=1,
        restart_delay_seconds=0.01,
    )
    lifecycle.start()
    await asyncio.gather(*(event.wait() for event in started.values()))

    result = await lifecycle.stop(timeout_seconds=1)

    assert result.all_stopped is True
    assert all(event.is_set() for event in cancelled.values())
    assert all(phase.phase_task is None for phase in lifecycle.phases)


@pytest.mark.asyncio
async def test_stop_bridge_periodic_work_cancels_registration_before_periodic_owners() -> None:
    registration_started = asyncio.Event()
    registration_cancelled = asyncio.Event()
    stop_called = asyncio.Event()

    async def registration() -> None:
        registration_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            registration_cancelled.set()
            raise

    class FakeLifecycle:
        async def stop(self, *, timeout_seconds: float) -> BridgePeriodicStopResult:
            assert registration_cancelled.is_set()
            assert timeout_seconds == 1
            stop_called.set()
            return BridgePeriodicStopResult(heartbeat_stopped=True, all_stopped=True)

    registration_task = asyncio.create_task(registration())
    await asyncio.wait_for(registration_started.wait(), timeout=1)

    result = await stop_bridge_periodic_work(
        registration_task,
        cast(BridgeRingPeriodicLifecycle, FakeLifecycle()),
        timeout_seconds=1,
    )

    assert result.registration_stopped is True
    assert result.heartbeat_stopped is True
    assert result.all_stopped is True
    assert stop_called.is_set()


@pytest.mark.asyncio
async def test_stop_bridge_periodic_work_reports_noncooperative_registration() -> None:
    registration_started = asyncio.Event()
    release_registration = asyncio.Event()

    async def registration() -> None:
        registration_started.set()
        while not release_registration.is_set():
            try:
                await release_registration.wait()
            except asyncio.CancelledError:
                task = asyncio.current_task()
                assert task is not None
                task.uncancel()

    registration_task = asyncio.create_task(registration())
    await asyncio.wait_for(registration_started.wait(), timeout=1)

    result = await stop_bridge_periodic_work(
        registration_task,
        None,
        timeout_seconds=0.01,
    )
    assert result.registration_stopped is False
    assert result.all_stopped is False

    release_registration.set()
    await asyncio.wait_for(registration_task, timeout=1)


@pytest.mark.asyncio
async def test_owned_lifespan_shutdown_completes_under_anyio_level_cancellation() -> None:
    import app.main as main

    cleanup_complete = asyncio.Event()

    async def cleanup() -> None:
        await anyio.sleep(0)
        cleanup_complete.set()

    with anyio.CancelScope() as scope:
        scope.cancel()
        cancellation = await main._run_owned_lifespan_shutdown(cleanup)

    assert cleanup_complete.is_set()
    assert cancellation is None


@pytest.mark.asyncio
async def test_owned_lifespan_shutdown_defers_direct_task_cancellation() -> None:
    import app.main as main

    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_complete = asyncio.Event()

    async def cleanup() -> None:
        cleanup_started.set()
        await release_cleanup.wait()
        cleanup_complete.set()

    owner = asyncio.create_task(main._run_owned_lifespan_shutdown(cleanup))
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    owner.cancel()
    release_cleanup.set()

    cancellation = await asyncio.wait_for(owner, timeout=1)
    assert cleanup_complete.is_set()
    assert isinstance(cancellation, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_shutdown_marks_ring_stale_only_after_periodic_owners_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main

    order: list[str] = []

    async def stop_periodic(*args, **kwargs) -> BridgePeriodicShutdownResult:
        order.append("stop_periodic")
        return BridgePeriodicShutdownResult(
            registration_stopped=True,
            heartbeat_stopped=True,
            all_stopped=True,
        )

    async def mark_stale(*args, **kwargs) -> None:
        assert order == ["stop_periodic"]
        order.append("mark_stale")

    monkeypatch.setattr(main, "stop_bridge_periodic_work", stop_periodic)
    ring_service = cast(RingMembershipService, AsyncMock(mark_stale=mark_stale))

    marked, cancellation = await main._shutdown_bridge_ring_membership(
        registration_task=None,
        periodic_lifecycle=None,
        ring_service=ring_service,
        instance_id="pod-a",
    )

    assert marked is True
    assert cancellation is None
    assert order == ["stop_periodic", "mark_stale"]


@pytest.mark.asyncio
async def test_shutdown_defers_cancellation_until_after_stale_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main

    stop_started = asyncio.Event()
    release_stop = asyncio.Event()
    stale_marked = asyncio.Event()

    async def stop_periodic(*args, **kwargs) -> BridgePeriodicShutdownResult:
        stop_started.set()
        await release_stop.wait()
        return BridgePeriodicShutdownResult(
            registration_stopped=True,
            heartbeat_stopped=True,
            all_stopped=True,
        )

    async def mark_stale(*args, **kwargs) -> None:
        stale_marked.set()

    monkeypatch.setattr(main, "stop_bridge_periodic_work", stop_periodic)
    ring_service = cast(RingMembershipService, AsyncMock(mark_stale=mark_stale))
    shutdown_task = asyncio.create_task(
        main._shutdown_bridge_ring_membership(
            registration_task=None,
            periodic_lifecycle=None,
            ring_service=ring_service,
            instance_id="pod-a",
        )
    )
    await asyncio.wait_for(stop_started.wait(), timeout=1)

    shutdown_task.cancel()
    release_stop.set()
    marked, cancellation = await asyncio.wait_for(shutdown_task, timeout=1)

    assert marked is True
    assert stale_marked.is_set()
    assert isinstance(cancellation, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_shutdown_skips_stale_mark_when_periodic_owner_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main

    monkeypatch.setattr(
        main,
        "stop_bridge_periodic_work",
        AsyncMock(
            return_value=BridgePeriodicShutdownResult(
                registration_stopped=True,
                heartbeat_stopped=False,
                all_stopped=False,
            )
        ),
    )
    ring_service = AsyncMock()

    marked, cancellation = await main._shutdown_bridge_ring_membership(
        registration_task=None,
        periodic_lifecycle=None,
        ring_service=ring_service,
        instance_id="pod-a",
    )

    assert marked is False
    assert cancellation is None
    ring_service.mark_stale.assert_not_awaited()


@pytest.mark.asyncio
async def test_periodic_lifecycle_reports_heartbeat_that_cannot_stop() -> None:
    heartbeat_started = asyncio.Event()
    release_heartbeat = asyncio.Event()

    async def heartbeat() -> None:
        heartbeat_started.set()
        while not release_heartbeat.is_set():
            try:
                await release_heartbeat.wait()
            except asyncio.CancelledError:
                task = asyncio.current_task()
                assert task is not None
                task.uncancel()

    noop = AsyncMock()
    lifecycle = BridgeRingPeriodicLifecycle(
        heartbeat=heartbeat,
        durable_ownership=noop,
        idle_sweep=noop,
        cap_partition=noop,
        interval_seconds=0.01,
        heartbeat_deadline_seconds=0.01,
        maintenance_deadline_seconds=0.05,
        restart_delay_seconds=0.01,
    )
    lifecycle.start()
    await asyncio.wait_for(heartbeat_started.wait(), timeout=1)

    result = await lifecycle.stop(timeout_seconds=0.01)
    assert result.heartbeat_stopped is False
    assert result.all_stopped is False

    release_heartbeat.set()
    heartbeat_phase = next(phase for phase in lifecycle.phases if phase.phase == "heartbeat")
    owned = heartbeat_phase.phase_task
    assert owned is not None
    await asyncio.wait_for(owned, timeout=1)
