from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Collection
from typing import TypeVar

_TaskResultT = TypeVar("_TaskResultT")

DRAIN_DEADLINE_HEADER = "x-codex-lb-drain-deadline-monotonic"

_draining: bool = False
_drain_deadline: float | None = None
_drain_started: bool = False
_shutdown_committed: bool = False
_shutdown_deadline_candidates: list[float] = []
_server_start_pending: bool = False
_lifespan_completed: bool = False
_bridge_drain_active: bool = False
_in_flight: int = 0
_control_plane_task_admission_open: bool = True
_post_drain_cleanup_timeout_seconds: float | None = None


def _effective_drain_deadline() -> float | None:
    """Return the earliest deadline observed by reversible or committed drain."""

    candidates = _shutdown_deadline_candidates
    if _drain_deadline is None:
        return min(candidates) if candidates else None
    if not candidates:
        return _drain_deadline
    return min(_drain_deadline, *candidates)


def reset() -> None:
    global _draining, _drain_deadline, _drain_started, _shutdown_committed
    global _shutdown_deadline_candidates
    global _server_start_pending, _lifespan_completed
    global _bridge_drain_active, _in_flight, _control_plane_task_admission_open
    global _post_drain_cleanup_timeout_seconds
    _draining = False
    _drain_deadline = None
    _drain_started = False
    _shutdown_committed = False
    _shutdown_deadline_candidates = []
    _server_start_pending = False
    _lifespan_completed = False
    _bridge_drain_active = False
    _in_flight = 0
    _control_plane_task_admission_open = True
    _post_drain_cleanup_timeout_seconds = None


def prepare_server_start() -> None:
    """Reset before signal capture and mark the matching lifespan as prepared."""

    global _server_start_pending
    reset()
    _server_start_pending = True


def prepare_lifespan_start() -> None:
    """Reset only a completed embedded lifecycle, never a server signal latch."""

    global _server_start_pending
    if _server_start_pending:
        _server_start_pending = False
        return
    if _lifespan_completed:
        reset()


def mark_lifespan_completed() -> None:
    """Allow a later embedded lifespan in this process to start cleanly."""

    global _server_start_pending, _lifespan_completed
    _server_start_pending = False
    _lifespan_completed = True


def close_control_plane_task_admission() -> None:
    global _control_plane_task_admission_open
    _control_plane_task_admission_open = False


def is_control_plane_task_admission_open() -> bool:
    return _control_plane_task_admission_open


def set_draining(val: bool = True) -> None:
    global _draining
    if val:
        _draining = True
        return
    stop_drain()


def _new_drain_deadline_candidate(
    timeout_seconds: float,
    *,
    deadline_monotonic: float | None,
) -> float:
    requested_deadline: float | None = None
    if deadline_monotonic is not None:
        requested_deadline = float(deadline_monotonic)
        if not math.isfinite(requested_deadline):
            raise ValueError("drain deadline must be finite")

    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    if requested_deadline is not None:
        deadline = min(deadline, requested_deadline)
    return deadline


def begin_drain(
    timeout_seconds: float,
    *,
    deadline_monotonic: float | None = None,
) -> float:
    """Start one bounded process drain without extending an existing deadline."""

    global _draining, _drain_deadline, _drain_started, _bridge_drain_active
    global _shutdown_deadline_candidates
    # This single monotonic snapshot is the function's linearization point.
    # If a signal commits after observing False, this invocation still
    # publishes its own candidate and the effective deadline takes the minimum.
    if _drain_started and deadline_monotonic is None:
        _draining = True
        _bridge_drain_active = True
        deadline = _effective_drain_deadline()
        assert deadline is not None
        return deadline
    deadline = _new_drain_deadline_candidate(
        timeout_seconds,
        deadline_monotonic=deadline_monotonic,
    )
    if _drain_deadline is None or deadline < _drain_deadline:
        _drain_deadline = deadline
    _draining = True
    _bridge_drain_active = True
    _drain_started = True
    # A synchronous signal handler can commit shutdown between any Python
    # bytecodes above. The handler's committed deadline and this call's
    # reversible deadline are therefore independent monotonic candidates; the
    # effective deadline is always their minimum.
    deadline = _effective_drain_deadline()
    assert deadline is not None
    if _shutdown_committed:
        # A signal may have committed before this invocation published its
        # reversible candidate. Promote that candidate through one atomic list
        # append so a later stop continuation cannot erase it.
        _shutdown_deadline_candidates.append(deadline)
    return deadline


def commit_shutdown(
    timeout_seconds: float,
    *,
    deadline_monotonic: float | None = None,
) -> float:
    """Permanently close admission and publish a committed deadline candidate."""

    global _draining, _drain_started, _shutdown_committed
    global _shutdown_deadline_candidates
    global _bridge_drain_active
    if _shutdown_committed and deadline_monotonic is None:
        deadline = _effective_drain_deadline()
        assert deadline is not None
        return deadline

    # A deadline-bearing preStop request may arrive after SIGTERM even though
    # its deadline was anchored before that signal. Let that explicit earlier
    # candidate tighten committed shutdown; headerless later signals only reuse
    # the first committed value through the fast path above.
    deadline = _new_drain_deadline_candidate(
        timeout_seconds,
        deadline_monotonic=deadline_monotonic,
    )
    # Each invocation that crossed the fast path publishes through one atomic
    # append. A signal can re-enter between any surrounding bytecodes, but an
    # append-only minimum cannot overwrite an earlier candidate with a later
    # deadline.
    _shutdown_deadline_candidates.append(deadline)
    # Promote every already-published reversible deadline into committed
    # storage. Otherwise a signal interleaving with stop_drain() could return
    # the earlier effective deadline here, let stop_drain() erase its reversible
    # owner, and then expose this later committed candidate.
    if _drain_deadline is not None:
        _shutdown_deadline_candidates.append(_drain_deadline)
    _shutdown_committed = True
    _draining = True
    _bridge_drain_active = True
    _drain_started = True
    effective_deadline = _effective_drain_deadline()
    assert effective_deadline is not None
    return effective_deadline


def is_shutdown_committed() -> bool:
    return _shutdown_committed


def stop_drain() -> bool:
    """Stop a reversible operator drain unless process shutdown has committed."""

    global _draining, _drain_deadline, _drain_started, _bridge_drain_active
    if _shutdown_committed:
        return False
    # Clear the admission token first so a synchronous signal never observes
    # "started" with the deadline already erased.
    _drain_started = False
    _draining = False
    _drain_deadline = None
    _bridge_drain_active = False
    # A synchronous signal may have committed shutdown between the first
    # check and the reversible state clears above. Committed state is stored
    # separately; restore the token/deadline before returning to preserve the
    # begin_drain entry invariant.
    if _shutdown_committed:
        assert _shutdown_deadline_candidates
        _drain_started = True
    return not _shutdown_committed


def remaining_drain_timeout_seconds() -> float | None:
    deadline = _effective_drain_deadline()
    if deadline is None:
        return None
    return max(deadline - time.monotonic(), 0.0)


def set_post_drain_cleanup_timeout_seconds(timeout_seconds: float) -> None:
    """Publish the fixed cleanup reserve belonging to the server shutdown."""

    global _post_drain_cleanup_timeout_seconds
    _post_drain_cleanup_timeout_seconds = max(float(timeout_seconds), 0.0)


def remaining_post_drain_cleanup_timeout_seconds() -> float | None:
    """Return the unused portion of the shared drain-plus-reserve deadline."""

    deadline = _effective_drain_deadline()
    reserve = _post_drain_cleanup_timeout_seconds
    if deadline is None or reserve is None:
        return None
    return max(deadline + reserve - time.monotonic(), 0.0)


def is_draining() -> bool:
    return _draining or _shutdown_committed


def set_bridge_drain_active(val: bool = True) -> None:
    global _bridge_drain_active
    _bridge_drain_active = val


def is_bridge_drain_active() -> bool:
    return _bridge_drain_active or _shutdown_committed


def increment_in_flight() -> None:
    global _in_flight
    _in_flight += 1


def decrement_in_flight() -> None:
    global _in_flight
    _in_flight = max(0, _in_flight - 1)


def get_in_flight() -> int:
    return _in_flight


async def wait_for_in_flight_drain(
    timeout_seconds: float,
    poll_interval_seconds: float = 0.1,
    should_abort: Callable[[], bool] | None = None,
) -> bool:
    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    shared_deadline = _effective_drain_deadline()
    if shared_deadline is not None:
        deadline = min(deadline, shared_deadline)
    while get_in_flight() > 0:
        if should_abort is not None and should_abort():
            return False
        shared_deadline = _effective_drain_deadline()
        if shared_deadline is not None:
            # A deadline-bearing preStop request can overtake SIGTERM after
            # this wait has begun. Re-read the shared candidate so that late
            # publication can only tighten, never extend, the active wait.
            deadline = min(deadline, shared_deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(poll_interval_seconds, remaining))
    return get_in_flight() == 0


async def wait_for_tasks_to_drain(
    tasks: Collection[asyncio.Task[_TaskResultT]],
    timeout_seconds: float,
) -> set[asyncio.Task[_TaskResultT]]:
    """Wait until a live task collection is stable or its deadline expires."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        pending = {task for task in tasks if not task.done()}
        if not pending:
            # Let done callbacks run before declaring a live registry empty.
            await asyncio.sleep(0)
            pending = {task for task in tasks if not task.done()}
            if not pending:
                return set()

        remaining = deadline - loop.time()
        if remaining <= 0:
            return pending

        _, still_pending = await asyncio.wait(pending, timeout=remaining)
        if still_pending:
            return {task for task in tasks if not task.done()}
