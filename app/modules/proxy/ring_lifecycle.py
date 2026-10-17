"""Independent supervised periodic work for bridge-ring replicas."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Final, Literal

from app.core.metrics import prometheus as prometheus_metrics
from app.core.utils.periodic import PeriodicPhaseEvent, SupervisedPeriodicPhase
from app.modules.proxy.ring_membership import RING_HEARTBEAT_INTERVAL_SECONDS

logger = logging.getLogger(__name__)

HEARTBEAT_PHASE: Final = "heartbeat"
MAINTENANCE_PHASES: Final = ("durable_ownership", "idle_sweep", "cap_partition")
HEARTBEAT_DEADLINE_SECONDS: Final = 5.0
MAINTENANCE_DEADLINE_SECONDS: Final = 30.0
SUPERVISOR_RESTART_DELAY_SECONDS: Final = 1.0

BridgeMaintenancePhase = Literal["durable_ownership", "idle_sweep", "cap_partition"]


@dataclass(frozen=True, slots=True)
class BridgePeriodicStopResult:
    heartbeat_stopped: bool
    all_stopped: bool


@dataclass(frozen=True, slots=True)
class BridgePeriodicShutdownResult:
    registration_stopped: bool
    heartbeat_stopped: bool
    all_stopped: bool


async def stop_bridge_periodic_work(
    registration_task: asyncio.Task[None] | None,
    lifecycle: BridgeRingPeriodicLifecycle | None,
    *,
    timeout_seconds: float,
) -> BridgePeriodicShutdownResult:
    """Cancel registration and periodic owners within one shutdown bound."""

    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    if registration_task is not None and not registration_task.done():
        registration_task.cancel()

    # Stop renewal immediately, rather than waiting out a blocked registration
    # before cancellation reaches the periodic owners.
    periodic_result = BridgePeriodicStopResult(heartbeat_stopped=True, all_stopped=True)
    if lifecycle is not None:
        periodic_result = await lifecycle.stop(timeout_seconds=max(deadline - time.monotonic(), 0.0))

    if registration_task is not None and not registration_task.done():
        await asyncio.wait({registration_task}, timeout=max(deadline - time.monotonic(), 0.0))
    registration_stopped = registration_task is None or registration_task.done()
    if registration_task is not None and registration_task.done() and not registration_task.cancelled():
        registration_task.exception()

    return BridgePeriodicShutdownResult(
        registration_stopped=registration_stopped,
        heartbeat_stopped=periodic_result.heartbeat_stopped,
        all_stopped=registration_stopped and periodic_result.all_stopped,
    )


class BridgeRingPeriodicLifecycle:
    """Own the four independent periodic phases for one registered replica."""

    def __init__(
        self,
        *,
        heartbeat: Callable[[], Coroutine[Any, Any, None]],
        durable_ownership: Callable[[], Coroutine[Any, Any, None]],
        idle_sweep: Callable[[], Coroutine[Any, Any, None]],
        cap_partition: Callable[[], Coroutine[Any, Any, None]],
        interval_seconds: float = RING_HEARTBEAT_INTERVAL_SECONDS,
        heartbeat_deadline_seconds: float = HEARTBEAT_DEADLINE_SECONDS,
        maintenance_deadline_seconds: float = MAINTENANCE_DEADLINE_SECONDS,
        restart_delay_seconds: float = SUPERVISOR_RESTART_DELAY_SECONDS,
    ) -> None:
        operations: tuple[tuple[str, Callable[[], Coroutine[Any, Any, None]], float, float], ...] = (
            (HEARTBEAT_PHASE, heartbeat, heartbeat_deadline_seconds, interval_seconds),
            ("durable_ownership", durable_ownership, maintenance_deadline_seconds, interval_seconds),
            ("idle_sweep", idle_sweep, maintenance_deadline_seconds, interval_seconds),
            ("cap_partition", cap_partition, maintenance_deadline_seconds, 0.0),
        )
        self._phases = {
            phase: SupervisedPeriodicPhase(
                phase=phase,
                operation=operation,
                interval_seconds=interval_seconds,
                deadline_seconds=deadline,
                restart_delay_seconds=restart_delay_seconds,
                initial_delay_seconds=initial_delay,
                on_event=self._record_event,
                on_supervisor_exit=self._record_supervisor_exit,
            )
            for phase, operation, deadline, initial_delay in operations
        }
        self._last_heartbeat_success_monotonic: float | None = None
        self._stop_tasks: set[asyncio.Task[bool]] = set()

    @property
    def phases(self) -> tuple[SupervisedPeriodicPhase, ...]:
        return tuple(self._phases.values())

    def start(self) -> None:
        self._record_heartbeat_success()
        for phase in self._phases.values():
            phase.start()

    async def stop(self, *, timeout_seconds: float) -> BridgePeriodicStopResult:
        timeout_seconds = max(timeout_seconds, 0.0)
        stop_tasks = {
            name: asyncio.create_task(
                phase.stop(timeout_seconds=timeout_seconds),
                name=f"bridge-periodic-{name}-stop",
            )
            for name, phase in self._phases.items()
        }
        self._stop_tasks.update(stop_tasks.values())
        for task in stop_tasks.values():
            task.add_done_callback(self._stop_tasks.discard)

        done, _ = await asyncio.wait(set(stop_tasks.values()), timeout=timeout_seconds)
        results: dict[str, bool] = {}
        for name, task in stop_tasks.items():
            if task not in done or task.cancelled():
                results[name] = False
                continue
            error = task.exception()
            results[name] = error is None and task.result()

        return BridgePeriodicStopResult(
            heartbeat_stopped=results.get(HEARTBEAT_PHASE, False),
            all_stopped=all(results.get(name, False) for name in self._phases),
        )

    def _record_event(self, event: PeriodicPhaseEvent) -> None:
        if event.phase == HEARTBEAT_PHASE:
            self._record_heartbeat_event(event)
            return
        self._record_maintenance_event(event)

    def _record_heartbeat_event(self, event: PeriodicPhaseEvent) -> None:
        if event.outcome == "success":
            self._record_heartbeat_success()
            if event.consecutive_failures > 0:
                logger.info(
                    "Bridge ring heartbeat recovered",
                    extra={
                        "phase": event.phase,
                        "outcome": event.outcome,
                        "elapsed_seconds": event.elapsed_seconds,
                        "consecutive_failures": event.consecutive_failures,
                        "late": event.late,
                    },
                )
            return

        if not event.late and prometheus_metrics.bridge_ring_heartbeat_failures_total is not None:
            prometheus_metrics.bridge_ring_heartbeat_failures_total.inc()
        age_seconds = None
        if self._last_heartbeat_success_monotonic is not None:
            age_seconds = max(time.monotonic() - self._last_heartbeat_success_monotonic, 0.0)
        logger.warning(
            "Bridge ring heartbeat attempt did not complete successfully",
            extra={
                "phase": event.phase,
                "outcome": event.outcome,
                "elapsed_seconds": event.elapsed_seconds,
                "heartbeat_age_seconds": age_seconds,
                "consecutive_failures": event.consecutive_failures,
                "late": event.late,
                "error_type": event.error_type,
            },
        )

    def _record_heartbeat_success(self) -> None:
        self._last_heartbeat_success_monotonic = time.monotonic()
        metric = prometheus_metrics.bridge_ring_heartbeat_last_success_timestamp_seconds
        if metric is not None:
            metric.set(time.time())

    @staticmethod
    def _record_maintenance_event(event: PeriodicPhaseEvent) -> None:
        phase = event.phase
        if phase not in MAINTENANCE_PHASES:
            logger.error("Unexpected bridge maintenance phase phase=%s", phase)
            return
        metric = prometheus_metrics.bridge_ring_maintenance_total
        if metric is not None:
            metric.labels(phase=phase, outcome=event.outcome).inc()
        if event.outcome != "success":
            logger.warning(
                "Bridge ring maintenance did not complete successfully",
                extra={
                    "phase": phase,
                    "outcome": event.outcome,
                    "elapsed_seconds": event.elapsed_seconds,
                    "consecutive_failures": event.consecutive_failures,
                    "late": event.late,
                    "error_type": event.error_type,
                },
            )
        elif event.late:
            logger.info(
                "Bridge ring maintenance completed after its diagnostic deadline",
                extra={
                    "phase": phase,
                    "outcome": event.outcome,
                    "elapsed_seconds": event.elapsed_seconds,
                    "consecutive_failures": event.consecutive_failures,
                    "late": True,
                },
            )

    @staticmethod
    def _record_supervisor_exit(phase: str, error: BaseException, restart_delay_seconds: float) -> None:
        logger.warning(
            "Bridge periodic worker exited unexpectedly",
            extra={
                "phase": phase,
                "outcome": "failure",
                "error_type": type(error).__name__,
                "restart_delay_seconds": restart_delay_seconds,
            },
            exc_info=(type(error), error, error.__traceback__),
        )
