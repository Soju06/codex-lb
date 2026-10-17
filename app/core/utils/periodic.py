"""Supervised ownership for one non-overlapping periodic asynchronous phase."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from app.core.utils.shared_future import _await_task_deferring_cancellation

logger = logging.getLogger(__name__)

PeriodicOutcome = Literal["success", "failure", "timeout"]


@dataclass(frozen=True, slots=True)
class PeriodicPhaseEvent:
    """One bounded, low-cardinality phase observation."""

    phase: str
    outcome: PeriodicOutcome
    elapsed_seconds: float
    consecutive_failures: int
    late: bool = False
    error_type: str | None = None


class SupervisedPeriodicPhase:
    """Run one periodic operation without overlap and own it through shutdown."""

    def __init__(
        self,
        *,
        phase: str,
        operation: Callable[[], Coroutine[Any, Any, None]],
        interval_seconds: float,
        deadline_seconds: float,
        restart_delay_seconds: float,
        initial_delay_seconds: float | None = None,
        on_event: Callable[[PeriodicPhaseEvent], None],
        on_supervisor_exit: Callable[[str, BaseException, float], None],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if restart_delay_seconds < 0:
            raise ValueError("restart_delay_seconds must be nonnegative")
        if initial_delay_seconds is not None and initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must be nonnegative")
        self.phase = phase
        self._operation = operation
        self._interval_seconds = interval_seconds
        self._deadline_seconds = deadline_seconds
        self._restart_delay_seconds = restart_delay_seconds
        self._initial_delay_seconds = interval_seconds if initial_delay_seconds is None else initial_delay_seconds
        self._on_event = on_event
        self._on_supervisor_exit = on_supervisor_exit
        self._clock = clock
        self._sleep = sleep
        self._supervisor_task: asyncio.Task[None] | None = None
        self._phase_task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._supervisor_task is not None and not self._supervisor_task.done()

    @property
    def phase_task(self) -> asyncio.Task[None] | None:
        """Return the strongly owned current child for lifecycle diagnostics."""

        return self._phase_task

    def start(self) -> None:
        if self.running:
            raise RuntimeError(f"periodic phase {self.phase!r} is already running")
        self._stopping = False
        self._supervisor_task = asyncio.create_task(
            self._run_supervised(),
            name=f"periodic-{self.phase}-supervisor",
        )

    async def stop(self, *, timeout_seconds: float) -> bool:
        """Cancel and drain this phase within ``timeout_seconds``.

        The object retains strong references when a non-cooperative task exceeds
        the bound. Callers can then avoid stale-marking or closing shared
        resources while that owner is still active.
        """

        stop_task = asyncio.create_task(
            self._stop_within(timeout_seconds=max(timeout_seconds, 0.0)),
            name=f"periodic-{self.phase}-stop",
        )
        stopped, cancellation = await _await_task_deferring_cancellation(stop_task)
        if cancellation is not None:
            raise cancellation
        return stopped

    async def _stop_within(self, timeout_seconds: float) -> bool:
        self._stopping = True
        owned = {task for task in (self._supervisor_task, self._phase_task) if task is not None}
        pending_at_start = {task for task in owned if not task.done()}
        for task in pending_at_start:
            task.cancel()
        if pending_at_start:
            await asyncio.wait(pending_at_start, timeout=timeout_seconds)
        for task in owned:
            if task.done() and not task.cancelled():
                task.exception()
        return all(task.done() for task in owned)

    async def _run_supervised(self) -> None:
        while True:
            try:
                await self._run_cycles()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = exc
            else:
                error = RuntimeError("periodic worker exited without cancellation")
            if self._stopping:
                return
            try:
                self._on_supervisor_exit(self.phase, error, self._restart_delay_seconds)
            except Exception:
                logger.exception("Periodic supervisor-exit observer failed phase=%s", self.phase)
            await self._sleep(self._restart_delay_seconds)

    async def _run_cycles(self) -> None:
        next_due = self._clock() + self._initial_delay_seconds
        consecutive_failures = 0
        while True:
            await self._sleep(max(next_due - self._clock(), 0.0))
            started_at = self._clock()
            phase_task = asyncio.create_task(
                self._operation(),
                name=f"periodic-{self.phase}-attempt",
            )
            self._phase_task = phase_task
            timed_out = False
            try:
                done, _ = await asyncio.wait({phase_task}, timeout=self._deadline_seconds)
                if phase_task not in done:
                    timed_out = True
                    consecutive_failures += 1
                    self._emit(
                        outcome="timeout",
                        started_at=started_at,
                        consecutive_failures=consecutive_failures,
                    )
                    # Continue observing the one owned child without awaiting
                    # its result. ``asyncio.wait`` neither propagates child
                    # failure nor transfers supervisor cancellation to it; the
                    # finally block below remains the sole cancellation owner.
                    await asyncio.wait({phase_task})
                if phase_task.cancelled():
                    if self._stopping:
                        raise asyncio.CancelledError
                    if not timed_out:
                        consecutive_failures += 1
                    self._emit(
                        outcome="failure",
                        started_at=started_at,
                        consecutive_failures=consecutive_failures,
                        late=timed_out,
                        error_type="CancelledError",
                    )
                else:
                    error = phase_task.exception()
                    if error is None:
                        self._emit(
                            outcome="success",
                            started_at=started_at,
                            consecutive_failures=consecutive_failures,
                            late=timed_out,
                        )
                        consecutive_failures = 0
                    else:
                        if not timed_out:
                            consecutive_failures += 1
                        self._emit(
                            outcome="failure",
                            started_at=started_at,
                            consecutive_failures=consecutive_failures,
                            late=timed_out,
                            error_type=type(error).__name__,
                        )
            finally:
                if not phase_task.done():
                    phase_task.cancel()
                    await asyncio.gather(phase_task, return_exceptions=True)
                elif not phase_task.cancelled():
                    # Cancellation can land after the wait completed but before
                    # classification. Always consume the owned exception.
                    phase_task.exception()
                self._phase_task = None

            next_due += self._interval_seconds
            now = self._clock()
            if next_due <= now:
                missed_intervals = int((now - next_due) // self._interval_seconds) + 1
                next_due += missed_intervals * self._interval_seconds

    def _emit(
        self,
        *,
        outcome: PeriodicOutcome,
        started_at: float,
        consecutive_failures: int,
        late: bool = False,
        error_type: str | None = None,
    ) -> None:
        event = PeriodicPhaseEvent(
            phase=self.phase,
            outcome=outcome,
            elapsed_seconds=max(self._clock() - started_at, 0.0),
            consecutive_failures=consecutive_failures,
            late=late,
            error_type=error_type,
        )
        try:
            self._on_event(event)
        except Exception:
            logger.exception(
                "Periodic phase observer failed phase=%s outcome=%s",
                self.phase,
                outcome,
            )
