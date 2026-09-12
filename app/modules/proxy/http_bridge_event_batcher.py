from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any

from app.core import shutdown as shutdown_state
from app.core.clock import REAL_SCHEDULER, Scheduler, clock_for
from app.core.config.settings import get_settings
from app.core.utils.shared_future import _await_result_deferring_cancellation, _await_task_deferring_cancellation
from app.db.models import HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2, HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
from app.modules.proxy.durable_bridge_repository import DurableBridgeOperationEventInput

logger = logging.getLogger("app.modules.proxy.http_bridge_event_batcher")

_GENERATION_FENCE_RETENTION_SECONDS = 300.0
_TERMINAL_APPEND_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class _PendingOperationEvent:
    operation_id: str
    session_id: str
    instance_id: str
    owner_epoch: int
    event_text: str
    recovery_dispatch_count: int


@dataclass(frozen=True, slots=True)
class TerminalOperationEventAppendResult:
    persisted: bool
    settlement_required: bool = False

    def __bool__(self) -> bool:
        """Preserve boolean append-result compatibility by exposing whether terminal data was persisted."""
        return self.persisted


class HttpBridgeOperationEventBatcher:
    """Best-effort in-memory event buffer for the HTTP bridge.

    Normal stream handling only appends to memory. A short-lived flusher
    commits groups of events in one transaction. A terminal event drains its
    operation synchronously once, so a completed operation is marked
    replayable only after all queued events were persisted. A process crash or
    queue overflow therefore loses optional transcript data, never upstream
    work safety.
    """

    @classmethod
    def from_settings(
        cls, durable_bridge: Any, settings: Any | None = None, *, scheduler: Scheduler = REAL_SCHEDULER
    ) -> "HttpBridgeOperationEventBatcher":
        """Build the event spooler from the operator-facing settings surface."""
        settings = settings or get_settings()
        return cls(
            durable_bridge,
            scheduler=scheduler,
            max_bytes=int(
                getattr(settings, "http_responses_session_bridge_operation_event_spool_max_bytes", 2 * 1024 * 1024)
            ),
            batch_size=int(getattr(settings, "http_responses_session_bridge_operation_event_spool_batch_size", 32)),
            flush_interval_seconds=float(
                getattr(settings, "http_responses_session_bridge_operation_event_spool_flush_interval_seconds", 0.1)
            ),
            max_pending_events=int(
                getattr(settings, "http_responses_session_bridge_operation_event_spool_max_pending_events", 2048)
            ),
            max_pending_bytes=int(
                getattr(
                    settings, "http_responses_session_bridge_operation_event_spool_max_pending_bytes", 32 * 1024 * 1024
                )
            ),
            spool_format=str(
                getattr(
                    settings,
                    "http_responses_session_bridge_operation_spool_format",
                    HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1,
                )
            ),
        )

    def __init__(
        self,
        durable_bridge: Any,
        *,
        max_bytes: int,
        batch_size: int = 32,
        flush_interval_seconds: float = 0.1,
        max_pending_events: int = 2048,
        max_pending_bytes: int = 32 * 1024 * 1024,
        spool_format: str = HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1,
        scheduler: Scheduler = REAL_SCHEDULER,
        terminal_append_timeout_seconds: float = _TERMINAL_APPEND_TIMEOUT_SECONDS,
    ) -> None:
        """Configure bounded event queues, spool format, scheduler, and per-operation ownership locks."""
        if spool_format not in {HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1, HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2}:
            raise ValueError("unsupported durable bridge operation spool format")
        self._durable_bridge = durable_bridge
        self._scheduler = scheduler
        self._max_bytes = max_bytes
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_seconds
        self._max_pending_events = max_pending_events
        self._max_pending_bytes = max_pending_bytes
        self._spool_format = spool_format
        self._terminal_append_timeout_seconds = terminal_append_timeout_seconds
        self._pending: dict[str, list[_PendingOperationEvent]] = {}
        self._contexts: dict[str, _PendingOperationEvent] = {}
        # A successful recovery rebind advances this in-memory generation.
        # Late events from the interrupted upstream attempt are then dropped
        # instead of being flushed into the replacement operation.
        self._operation_generations: dict[str, int] = {}
        self._dropped_operations: set[str] = set()
        self._closing_operations: set[str] = set()
        self._inflight_flushes: dict[str, int] = {}
        self._flush_completion_events: dict[str, asyncio.Event] = {}
        # Attempt token per operation. A terminal append that outlived its
        # bound still runs its own cleanup when the durable layer finally
        # releases it; by then a later attempt may own the in-memory state for
        # the same operation id, so cleanup is fenced on the attempt that
        # registered it instead of clearing whatever is there now.
        self._operation_attempts: dict[str, int] = {}
        self._attempt_counter = 0
        self._pending_count = 0
        self._pending_bytes = 0
        self._lock = asyncio.Lock()
        # Serialize enqueue/rebind state transitions with the durable append
        # for the same operation, without blocking unrelated operations.
        self._operation_locks: dict[str, asyncio.Lock] = {}
        # Count callers that have fetched an operation lock, including tasks
        # waiting to acquire it.  ``asyncio.Lock.locked()`` does not expose
        # queued waiters, so cleanup must retain the lock until every caller
        # releases its reference.
        self._operation_lock_users: dict[str, int] = {}
        # SQLite already serializes writers; this also prevents a background
        # flush racing a terminal drain and final marker for one operation.
        self._flush_lock = asyncio.Lock()
        self._generation_cleanup_tasks: dict[str, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._terminal_append_tasks: set[asyncio.Task[TerminalOperationEventAppendResult]] = set()
        self._ordinary_append_tasks: set[asyncio.Task[bool]] = set()
        self._terminal_finalize_tasks: set[asyncio.Task[None]] = set()
        # Keep terminal callers visible while they hand off an append result
        # to the finalizer tracker.  Shutdown waits for this handoff so a
        # finalizer cannot be created after the close drain snapshots tasks.
        self._terminal_append_callers: set[asyncio.Task[Any]] = set()
        # Ordinary enqueue callers need the same admission handoff: once close
        # starts, a caller that already passed admission must finish before the
        # flusher is stopped and its queue is drained.
        self._enqueue_callers: set[asyncio.Task[Any]] = set()
        self._closing = False

    async def _operation_lock_for(self, operation_id: str) -> asyncio.Lock:
        """Return an operation lock while synchronizing its map lifetime."""
        async with self._lock:
            operation_lock = self._operation_locks.setdefault(operation_id, asyncio.Lock())
            self._operation_lock_users[operation_id] = self._operation_lock_users.get(operation_id, 0) + 1
            return operation_lock

    async def _release_operation_lock_user(self, operation_id: str) -> None:
        """Drop one lock-map reference and clean up any now-idle state."""
        async with self._lock:
            remaining_users = self._operation_lock_users.get(operation_id, 0) - 1
            if remaining_users > 0:
                self._operation_lock_users[operation_id] = remaining_users
            else:
                self._operation_lock_users.pop(operation_id, None)
            self._cleanup_operation_state_locked(operation_id)

    def _cleanup_operation_state_locked(self, operation_id: str) -> None:
        """Release idle per-operation synchronization objects.

        Callers hold ``_lock``. Removing a lock only while it is unlocked keeps
        callers that already obtained it on the same serialization path; a
        future caller can safely create a fresh lock once no state remains.
        """
        if (
            operation_id in self._pending
            or self._inflight_flushes.get(operation_id, 0) > 0
            or operation_id in self._closing_operations
            or operation_id in self._contexts
            or operation_id in self._operation_generations
            or operation_id in self._generation_cleanup_tasks
            or self._operation_lock_users.get(operation_id, 0) > 0
        ):
            return
        operation_lock = self._operation_locks.get(operation_id)
        if operation_lock is not None and operation_lock.locked():
            return
        self._operation_locks.pop(operation_id, None)
        self._flush_completion_events.pop(operation_id, None)

    async def _cleanup_operation_state(self, operation_id: str) -> None:
        """Release unused synchronization state while holding the shared queue-state lock."""
        async with self._lock:
            self._cleanup_operation_state_locked(operation_id)

    async def enqueue(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        event_text: str,
        terminal: bool = False,
        recovery_dispatch_count: int = 0,
    ) -> None:
        """Admit a fenced event to the bounded queue and synchronously drain terminal submissions."""
        caller = asyncio.current_task()
        async with self._lock:
            if self._closing:
                return
            if caller is not None:
                self._enqueue_callers.add(caller)
            self._ensure_task()
        try:
            return await self._enqueue_impl(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                event_text=event_text,
                terminal=terminal,
                recovery_dispatch_count=recovery_dispatch_count,
            )
        finally:
            if caller is not None:
                async with self._lock:
                    self._enqueue_callers.discard(caller)

    async def _enqueue_impl(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        event_text: str,
        terminal: bool = False,
        recovery_dispatch_count: int = 0,
    ) -> None:
        recovery_dispatch_count = max(0, int(recovery_dispatch_count))
        pending = _PendingOperationEvent(
            operation_id=operation_id,
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            event_text=event_text,
            recovery_dispatch_count=recovery_dispatch_count,
        )
        operation_lock = await self._operation_lock_for(operation_id)
        try:
            accepted = False
            if not terminal:
                # Ordinary events only touch the bounded memory queue. Keep
                # owner/generation changes serialized with durable append, but
                # do not put database latency on the unchanged stream path.
                accepted = await self._enqueue_pending(
                    pending,
                    event_text=event_text,
                    terminal=terminal,
                    recovery_dispatch_count=recovery_dispatch_count,
                    require_current_context=True,
                )
            if not accepted:
                async with operation_lock:
                    accepted = await self._enqueue_pending(
                        pending,
                        event_text=event_text,
                        terminal=terminal,
                        recovery_dispatch_count=recovery_dispatch_count,
                    )
            if not accepted:
                return
            self._wake.set()
            if terminal:
                await self.flush_operation(
                    operation_id=operation_id,
                    expected_recovery_dispatch_count=recovery_dispatch_count,
                )
        finally:
            await self._release_operation_lock_user(operation_id)

    async def _enqueue_pending(
        self,
        pending: _PendingOperationEvent,
        *,
        event_text: str,
        terminal: bool,
        recovery_dispatch_count: int,
        require_current_context: bool = False,
    ) -> bool:
        """Validate owner and generation under the queue lock before accepting bounded event data."""
        operation_id = pending.operation_id
        async with self._flush_lock:
            async with self._lock:
                current_generation = self._operation_generations.get(operation_id, 0)
                current_context = self._contexts.get(operation_id)
                if require_current_context and (
                    current_context is None
                    or recovery_dispatch_count != current_generation
                    or current_context.recovery_dispatch_count != recovery_dispatch_count
                    or current_context.session_id != pending.session_id
                    or current_context.instance_id != pending.instance_id
                    or current_context.owner_epoch != pending.owner_epoch
                    or operation_id in self._closing_operations
                ):
                    return False
                if recovery_dispatch_count < current_generation:
                    # A late event from the interrupted attempt must not be
                    # appended after a recovery rebind has claimed the operation.
                    return False
                self._cancel_generation_cleanup_locked(operation_id)
                if recovery_dispatch_count > current_generation:
                    self._operation_generations[operation_id] = recovery_dispatch_count
                    # A newer recovery generation gets a fresh spool budget;
                    # do not let an overflow marker from the predecessor
                    # suppress its events.
                    self._dropped_operations.discard(operation_id)
                owner_context_changed = (
                    current_context is not None
                    and current_context.recovery_dispatch_count == recovery_dispatch_count
                    and (
                        current_context.session_id != pending.session_id
                        or current_context.instance_id != pending.instance_id
                        or current_context.owner_epoch != pending.owner_epoch
                    )
                )
                if owner_context_changed and pending.owner_epoch <= current_context.owner_epoch:
                    # Owner epochs are monotonic durable fences. A detached
                    # predecessor must not rebind a successor's context.
                    return False
                if owner_context_changed:
                    # All events in one generation must carry the same durable
                    # owner context. Rebind events queued before a handoff so a
                    # mixed-owner batch cannot be rejected by the repository.
                    self._closing_operations.discard(operation_id)
                    queued = self._pending.get(operation_id)
                    if queued:
                        self._pending[operation_id] = [
                            replace(
                                item,
                                session_id=pending.session_id,
                                instance_id=pending.instance_id,
                                owner_epoch=pending.owner_epoch,
                            )
                            if item.recovery_dispatch_count == recovery_dispatch_count
                            else item
                            for item in queued
                        ]
                if (
                    current_context is None
                    or current_context.recovery_dispatch_count < recovery_dispatch_count
                    or owner_context_changed
                ):
                    self._contexts[operation_id] = pending
                if terminal:
                    self._closing_operations.add(operation_id)
                if operation_id not in self._dropped_operations:
                    event_bytes = len(event_text.encode("utf-8"))
                    if (
                        self._pending_count >= self._max_pending_events
                        or self._pending_bytes + event_bytes > self._max_pending_bytes
                    ):
                        self._dropped_operations.add(operation_id)
                        dropped = self._pending.pop(operation_id, [])
                        self._pending_count -= len(dropped)
                        self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in dropped)
                        logger.info(
                            "Dropping HTTP bridge transcript events after queue overflow operation_id=%s",
                            operation_id,
                        )
                    else:
                        self._pending.setdefault(operation_id, []).append(pending)
                        self._pending_count += 1
                        self._pending_bytes += event_bytes
        return True

    def _ensure_task(self) -> None:
        """Start the named background flusher only when no live flusher task is already owned."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="http-bridge-operation-event-flusher")

    def _cancel_generation_cleanup_locked(self, operation_id: str) -> None:
        """Cancel an operation's pending fence-expiry task without cancelling the current task itself."""
        task = self._generation_cleanup_tasks.pop(operation_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _schedule_generation_cleanup_locked(self, operation_id: str, generation: int) -> None:
        """Bound a generation fence to the abandoned request lifetime.

        A rollback must keep fencing late events for a short window, but an
        operation that never resumes must not leave an entry in the process
        forever. The cleanup task only removes the exact generation it was
        scheduled for, so a newer enqueue/rebind cannot be unfenced by stale
        timer work.
        """
        self._cancel_generation_cleanup_locked(operation_id)
        if self._closing:
            return

        async def cleanup() -> None:
            """Expire only the captured idle generation after the retention window, preserving active successors."""
            try:
                await self._scheduler.sleep(_GENERATION_FENCE_RETENTION_SECONDS)
            except asyncio.CancelledError:
                return
            async with self._flush_lock:
                async with self._lock:
                    if (
                        self._operation_generations.get(operation_id) == generation
                        and operation_id not in self._pending
                        and operation_id not in self._contexts
                        and operation_id not in self._closing_operations
                    ):
                        self._operation_generations.pop(operation_id, None)
                    self._generation_cleanup_tasks.pop(operation_id, None)
                    self._cleanup_operation_state_locked(operation_id)

        self._generation_cleanup_tasks[operation_id] = self._scheduler.create_task(
            cleanup(), name=f"http-bridge-generation-cleanup-{operation_id}"
        )

    async def _run(self) -> None:
        """Flush eligible operations when signalled or on the interval, preserving bounded batch writes."""
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._flush_interval_seconds)
            except TimeoutError:
                pass
            self._wake.clear()
            operation_ids = await self._operation_ids_to_flush()
            for operation_id in operation_ids:
                await self._flush_one(operation_id)

    async def _operation_ids_to_flush(self) -> list[str]:
        """Snapshot pending operation IDs that are not already draining through terminal closure."""
        async with self._lock:
            return [operation_id for operation_id in self._pending if operation_id not in self._closing_operations]

    async def _take_batch(self, operation_id: str) -> list[_PendingOperationEvent]:
        """Remove one bounded batch under the queue lock and release its pending event and byte capacity."""
        async with self._lock:
            pending = self._pending.get(operation_id, [])
            batch = pending[: self._batch_size]
            if batch:
                del pending[: len(batch)]
                self._pending_count -= len(batch)
                self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in batch)
            if not pending:
                self._pending.pop(operation_id, None)
            return batch

    async def _flush_one(self, operation_id: str) -> None:
        """Persist one queued batch under flush and ownership locks, fencing failed writes from successors."""
        operation_lock = await self._operation_lock_for(operation_id)
        acquired = False
        batch: list[_PendingOperationEvent] = []
        defer_cancellation = True
        try:
            try:
                await operation_lock.acquire()
                acquired = True
            except BaseException:
                # The caller still owns a lock-map reference even when it is
                # cancelled while waiting for the per-operation lock.
                raise
            async with self._flush_lock:
                batch = await self._take_batch(operation_id)
            if not batch:
                return
            # Re-check generation and owner context after taking the batch. A
            # concurrent recovery may have rebound the operation while the batch
            # was being removed from the in-memory queue; stale events must not be
            # appended under the replacement owner.
            async with self._lock:
                if operation_id in self._dropped_operations:
                    return
                current_generation = self._operation_generations.get(operation_id, 0)
                current_context = self._contexts.get(operation_id)
                # Terminal drains have their own bounded cancellation and
                # settlement path.  Ordinary background/explicit flushes,
                # however, keep the durable writer owned until its outcome is
                # known so a committed batch is never requeued as a duplicate.
                defer_cancellation = operation_id not in self._closing_operations
                batch = [
                    item
                    for item in batch
                    if item.recovery_dispatch_count >= current_generation
                    and (
                        current_context is None
                        or (
                            item.recovery_dispatch_count == current_context.recovery_dispatch_count
                            and item.session_id == current_context.session_id
                            and item.instance_id == current_context.instance_id
                            and item.owner_epoch == current_context.owner_epoch
                        )
                    )
                ]
                if not batch:
                    return
                self._inflight_flushes[operation_id] = self._inflight_flushes.get(operation_id, 0) + 1
                completion_event = self._flush_completion_events.setdefault(operation_id, asyncio.Event())
                completion_event.clear()
            try:
                events = [
                    DurableBridgeOperationEventInput(
                        operation_id=item.operation_id,
                        session_id=item.session_id,
                        instance_id=item.instance_id,
                        owner_epoch=item.owner_epoch,
                        event_text=item.event_text,
                        recovery_dispatch_count=item.recovery_dispatch_count,
                    )
                    for item in batch
                ]
                if self._spool_format == HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2:
                    append = self._durable_bridge.append_operation_event_chunk(
                        events=events,
                        max_bytes=self._max_bytes,
                    )
                else:
                    append = self._durable_bridge.append_operation_events(
                        events=events,
                        max_bytes=self._max_bytes,
                    )
                # Keep the durable write in its own task so cancellation of
                # the flusher cannot interrupt a commit that is already in
                # progress.  Resolve the append outcome before deciding
                # whether the dequeued batch needs to be restored.
                if defer_cancellation:
                    append_task = self._scheduler.create_task(append)
                    self._ordinary_append_tasks.add(append_task)
                    try:
                        persisted, deferred_cancellation = await _await_task_deferring_cancellation(append_task)
                    finally:
                        self._ordinary_append_tasks.discard(append_task)
                else:
                    persisted = await append
                    deferred_cancellation = None
                if not persisted:
                    failed_batch = batch

                    async def drop_failed_batch() -> None:
                        async with self._lock:
                            self._drop_failed_batch_locked(operation_id, failed_batch[0])

                    _, drop_cancellation = await _await_result_deferring_cancellation(
                        drop_failed_batch(),
                        scheduler=self._scheduler,
                    )
                    batch = []
                    if drop_cancellation is not None:
                        raise drop_cancellation
                else:
                    # The durable append completed; do not requeue this batch
                    # if cancellation arrives while final bookkeeping runs.
                    batch = []
                if deferred_cancellation is not None:
                    raise deferred_cancellation
            except asyncio.CancelledError:
                # Consume cancellation long enough to restore the dequeued
                # events and let the inflight bookkeeping in ``finally`` run.
                async with self._lock:
                    self._requeue_batch_locked(operation_id, batch)
                batch = []
                raise
            except Exception:
                failed_batch = batch

                async def drop_failed_batch() -> None:
                    async with self._lock:
                        self._drop_failed_batch_locked(operation_id, failed_batch[0])

                _, drop_cancellation = await _await_result_deferring_cancellation(
                    drop_failed_batch(),
                    scheduler=self._scheduler,
                )
                batch = []
                if drop_cancellation is not None:
                    raise drop_cancellation
                logger.debug(
                    "Dropping failed HTTP bridge transcript event batch operation_id=%s",
                    operation_id,
                    exc_info=True,
                )
            finally:
                async with self._lock:
                    remaining = self._inflight_flushes.get(operation_id, 0) - 1
                    if remaining <= 0:
                        self._inflight_flushes.pop(operation_id, None)
                        completion_event = self._flush_completion_events.pop(operation_id, None)
                        if completion_event is not None:
                            completion_event.set()
                    else:
                        self._inflight_flushes[operation_id] = remaining
        except asyncio.CancelledError:
            # Cancellation can arrive after _take_batch() has released events
            # from the bounded queue but before the durable append completes.
            # Put that batch back so a terminal drain cannot finalize an
            # incomplete spool while silently losing the dequeued events.
            if batch:
                async with self._lock:
                    self._requeue_batch_locked(operation_id, batch)
            raise
        finally:
            if acquired:
                operation_lock.release()
            await self._release_operation_lock_user(operation_id)

    def _requeue_batch_locked(self, operation_id: str, batch: list[_PendingOperationEvent]) -> None:
        """Restore a dequeued batch after cancellation while its operation lock is held."""
        if (
            not batch
            or operation_id in self._dropped_operations
            or (
                operation_id not in self._contexts
                and operation_id not in self._closing_operations
                and operation_id not in self._operation_generations
            )
        ):
            return
        self._pending.setdefault(operation_id, [])[:0] = batch
        self._pending_count += len(batch)
        self._pending_bytes += sum(len(item.event_text.encode("utf-8")) for item in batch)

    def _drop_failed_batch_locked(self, operation_id: str, owner: _PendingOperationEvent) -> None:
        """Drop queued data only if the failed writer still owns the current recovery generation."""
        context = self._contexts.get(operation_id)
        if (
            self._operation_generations.get(operation_id, 0) != owner.recovery_dispatch_count
            or context is None
            or (
                context.recovery_dispatch_count,
                context.session_id,
                context.instance_id,
                context.owner_epoch,
            )
            != (owner.recovery_dispatch_count, owner.session_id, owner.instance_id, owner.owner_epoch)
        ):
            return
        self._dropped_operations.add(operation_id)
        dropped = self._pending.pop(operation_id, [])
        self._pending_count -= len(dropped)
        self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in dropped)

    async def flush_operation(
        self,
        *,
        operation_id: str,
        expected_recovery_dispatch_count: int | None = None,
    ) -> None:
        """Drain an operation and finalize eligible spool data without retiring a newer generation."""
        await self.flush_pending_operation(operation_id=operation_id)
        async with self._lock:
            context = self._contexts.get(operation_id)
            expected_generation = (
                max(0, int(expected_recovery_dispatch_count))
                if expected_recovery_dispatch_count is not None
                else (context.recovery_dispatch_count if context is not None else 0)
            )
            if self._operation_generations.get(operation_id, 0) != expected_generation:
                self._cleanup_operation_state_locked(operation_id)
                return
            if context is not None and context.recovery_dispatch_count != expected_generation:
                self._cleanup_operation_state_locked(operation_id)
                return
            dropped = operation_id in self._dropped_operations
            self._closing_operations.discard(operation_id)
            self._contexts.pop(operation_id, None)
            self._operation_generations.pop(operation_id, None)
            self._dropped_operations.discard(operation_id)
        if dropped or context is None:
            await self._cleanup_operation_state(operation_id)
            return
        # A single final marker is the only synchronous database operation on
        # the terminal path. If it fails, the operation remains ineligible for
        # transcript replay.
        try:
            finalized = await self._durable_bridge.finalize_operation_event_spool(
                operation_id=context.operation_id,
                session_id=context.session_id,
                instance_id=context.instance_id,
                owner_epoch=context.owner_epoch,
            )
            if not finalized:
                logger.debug(
                    "HTTP bridge operation spool finalization was fenced or ineligible operation_id=%s",
                    operation_id,
                )
        except Exception:
            logger.debug(
                "Failed to finalize HTTP bridge operation event spool operation_id=%s",
                operation_id,
                exc_info=True,
            )
        await self._cleanup_operation_state(operation_id)

    async def append_terminal_event(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        event_text: str,
        max_bytes: int,
        state: str,
        expected_recovery_dispatch_count: int | None = None,
        response_id: str | None = None,
    ) -> TerminalOperationEventAppendResult:
        """Track a terminal caller so shutdown can finish finalizer handoff before draining."""
        caller = asyncio.current_task()
        async with self._lock:
            if self._closing:
                return TerminalOperationEventAppendResult(persisted=False, settlement_required=True)
            if caller is not None:
                self._terminal_append_callers.add(caller)
        try:
            return await self._append_terminal_event_impl(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                event_text=event_text,
                max_bytes=max_bytes,
                state=state,
                expected_recovery_dispatch_count=expected_recovery_dispatch_count,
                response_id=response_id,
            )
        finally:
            if caller is not None:
                async with self._lock:
                    self._terminal_append_callers.discard(caller)

    async def _append_terminal_event_impl(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        event_text: str,
        max_bytes: int,
        state: str,
        expected_recovery_dispatch_count: int | None = None,
        response_id: str | None = None,
    ) -> TerminalOperationEventAppendResult:
        """Drain queued events and atomically append the terminal outcome."""
        async with self._flush_lock:
            async with self._lock:
                if expected_recovery_dispatch_count is None:
                    expected_recovery_dispatch_count = self._operation_generations.get(operation_id, 0)
                else:
                    expected_recovery_dispatch_count = max(0, int(expected_recovery_dispatch_count))
                current_generation = self._operation_generations.get(operation_id, 0)
                if expected_recovery_dispatch_count < current_generation:
                    # A terminal event from a superseded upstream attempt must not
                    # settle the replacement operation.
                    self._cleanup_operation_state_locked(operation_id)
                    return TerminalOperationEventAppendResult(persisted=False)
                self._cancel_generation_cleanup_locked(operation_id)
                if expected_recovery_dispatch_count > current_generation:
                    self._operation_generations[operation_id] = expected_recovery_dispatch_count
                current_context = self._contexts.get(operation_id)
                owner_context_changed = (
                    current_context is not None
                    and current_context.recovery_dispatch_count == expected_recovery_dispatch_count
                    and (
                        current_context.session_id != session_id
                        or current_context.instance_id != instance_id
                        or current_context.owner_epoch != owner_epoch
                    )
                )
                if owner_context_changed and owner_epoch <= current_context.owner_epoch:
                    # A terminal event from a detached predecessor must not
                    # steal the successor's owner context.
                    self._cleanup_operation_state_locked(operation_id)
                    return TerminalOperationEventAppendResult(persisted=False)
                if owner_context_changed:
                    queued = self._pending.get(operation_id)
                    if queued:
                        self._pending[operation_id] = [
                            replace(
                                item,
                                session_id=session_id,
                                instance_id=instance_id,
                                owner_epoch=owner_epoch,
                            )
                            if item.recovery_dispatch_count == expected_recovery_dispatch_count
                            else item
                            for item in queued
                        ]
                if (
                    current_context is None
                    or current_context.recovery_dispatch_count < expected_recovery_dispatch_count
                    or owner_context_changed
                ):
                    # A terminal event may arrive before the replacement's first
                    # enqueue. Refresh the owner identity when this generation is
                    # newer so durable append is not fenced by stale context.
                    self._contexts[operation_id] = _PendingOperationEvent(
                        operation_id=operation_id,
                        session_id=session_id,
                        instance_id=instance_id,
                        owner_epoch=owner_epoch,
                        event_text=event_text,
                        recovery_dispatch_count=expected_recovery_dispatch_count,
                    )
                self._closing_operations.add(operation_id)
        async with self._lock:
            # The generation/owner admission above remains the authoritative
            # fence while the bounded terminal append runs in its own task.
            self._attempt_counter += 1
            attempt = self._attempt_counter
            self._operation_attempts[operation_id] = attempt
        append_task = asyncio.create_task(
            self._append_terminal_event_unbounded(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                event_text=event_text,
                max_bytes=max_bytes,
                state=state,
                expected_recovery_dispatch_count=expected_recovery_dispatch_count,
                response_id=response_id,
                attempt=attempt,
            ),
            name=f"http-bridge-terminal-spool-{operation_id}",
        )
        self._terminal_append_tasks.add(append_task)
        append_task.add_done_callback(self._terminal_append_done)
        try:
            done, _ = await asyncio.wait(
                {append_task},
                timeout=max(self._terminal_append_timeout_seconds, 0.0),
            )
        except asyncio.CancelledError:
            append_task.cancel()
            await self._clear_operation(operation_id, attempt=attempt)
            raise
        if append_task in done:
            if append_task.cancelled():
                await self._clear_operation(operation_id, attempt=attempt)
                return TerminalOperationEventAppendResult(
                    persisted=False,
                    settlement_required=True,
                )
            append_result = append_task.result()
            if append_result.persisted:
                self._schedule_terminal_spool_finalization(
                    operation_id=operation_id,
                    session_id=session_id,
                    instance_id=instance_id,
                    owner_epoch=owner_epoch,
                    expected_state=state,
                )
            return append_result

        append_task.cancel()
        await self._clear_operation(operation_id, attempt=attempt)
        logger.info(
            "Timed out persisting HTTP bridge terminal transcript operation_id=%s timeout_seconds=%.1f",
            operation_id,
            self._terminal_append_timeout_seconds,
        )
        return TerminalOperationEventAppendResult(
            persisted=False,
            settlement_required=True,
        )

    async def _append_terminal_event_unbounded(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        event_text: str,
        max_bytes: int,
        state: str,
        expected_recovery_dispatch_count: int | None,
        response_id: str | None,
        attempt: int,
    ) -> TerminalOperationEventAppendResult:
        context: _PendingOperationEvent | None = None
        try:
            await self.flush_pending_operation(operation_id=operation_id)
            async with self._lock:
                context = self._contexts.get(operation_id)
                dropped = operation_id in self._dropped_operations
                current_generation = self._operation_generations.get(operation_id, 0)
            if context is None:
                return TerminalOperationEventAppendResult(persisted=False, settlement_required=True)
            if dropped:
                return TerminalOperationEventAppendResult(persisted=False, settlement_required=True)
            if expected_recovery_dispatch_count is not None and current_generation != expected_recovery_dispatch_count:
                return TerminalOperationEventAppendResult(persisted=False, settlement_required=True)
            if (context.session_id, context.instance_id, context.owner_epoch) != (
                session_id,
                instance_id,
                owner_epoch,
            ):
                # A successor may rebind the same operation while the bounded
                # terminal drain is in flight. Never append this predecessor's
                # terminal outcome under the successor's owner fence.
                return TerminalOperationEventAppendResult(persisted=False)
            if self._spool_format == HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2:
                persisted = await self._durable_bridge.append_terminal_operation_chunk(
                    operation_id=operation_id,
                    session_id=context.session_id,
                    instance_id=context.instance_id,
                    owner_epoch=context.owner_epoch,
                    event_text=event_text,
                    max_bytes=max_bytes,
                    state=state,
                    expected_recovery_dispatch_count=expected_recovery_dispatch_count,
                    response_id=response_id,
                    complete_spool=False,
                )
            else:
                persisted = await self._durable_bridge.append_terminal_operation_event(
                    operation_id=operation_id,
                    session_id=context.session_id,
                    instance_id=context.instance_id,
                    owner_epoch=context.owner_epoch,
                    event_text=event_text,
                    max_bytes=max_bytes,
                    state=state,
                    expected_recovery_dispatch_count=expected_recovery_dispatch_count,
                    response_id=response_id,
                    complete_spool=False,
                )
            terminal_persisted = bool(persisted and not dropped)
            return TerminalOperationEventAppendResult(
                persisted=terminal_persisted,
                settlement_required=not terminal_persisted,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "Failed to append terminal HTTP bridge event operation_id=%s",
                operation_id,
                exc_info=True,
            )
            return TerminalOperationEventAppendResult(
                persisted=False,
                settlement_required=True,
            )
        finally:
            async with self._lock:
                current_context = self._contexts.get(operation_id)
                terminal_owner_still_current = (
                    context is not None
                    and current_context is not None
                    and context.session_id == session_id
                    and context.instance_id == instance_id
                    and context.owner_epoch == owner_epoch
                    and current_context.recovery_dispatch_count == context.recovery_dispatch_count
                    and current_context.session_id == context.session_id
                    and current_context.instance_id == context.instance_id
                    and current_context.owner_epoch == context.owner_epoch
                )
                if (
                    self._operation_attempts.get(operation_id) == attempt
                    and terminal_owner_still_current
                    and (
                        expected_recovery_dispatch_count is None
                        or self._operation_generations.get(operation_id, 0) == expected_recovery_dispatch_count
                    )
                ):
                    self._operation_attempts.pop(operation_id, None)
                    pending = self._pending.pop(operation_id, [])
                    self._pending_count -= len(pending)
                    self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in pending)
                    self._closing_operations.discard(operation_id)
                    self._contexts.pop(operation_id, None)
                    self._operation_generations.pop(operation_id, None)
                    self._dropped_operations.discard(operation_id)
                self._cleanup_operation_state_locked(operation_id)

    async def _clear_operation(self, operation_id: str, *, attempt: int) -> None:
        async with self._lock:
            if self._operation_attempts.get(operation_id) != attempt:
                # A newer terminal attempt already owns this operation's
                # in-memory state (this attempt timed out, was settled, and the
                # durable layer released it only afterwards). Clearing here
                # would strand the newer attempt with no context, so it would
                # give up its transcript and fall back to settlement.
                return
            self._operation_attempts.pop(operation_id, None)
            pending = self._pending.pop(operation_id, [])
            self._pending_count -= len(pending)
            self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in pending)
            self._closing_operations.discard(operation_id)
            self._contexts.pop(operation_id, None)
            self._dropped_operations.discard(operation_id)
            generation = self._operation_generations.get(operation_id, 0)
            if generation:
                self._schedule_generation_cleanup_locked(operation_id, generation)
            self._cleanup_operation_state_locked(operation_id)

    def _terminal_append_done(self, task: asyncio.Task[TerminalOperationEventAppendResult]) -> None:
        self._terminal_append_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.info(
                "Late HTTP bridge terminal transcript task failed task_name=%s",
                task.get_name(),
                exc_info=True,
            )

    def _schedule_terminal_spool_finalization(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        expected_state: str,
    ) -> None:
        finalize_task = asyncio.create_task(
            self._finalize_terminal_spool(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                expected_state=expected_state,
            ),
            name=f"http-bridge-terminal-spool-finalize-{operation_id}",
        )
        # Keep the operation identity alongside the session/epoch fence so
        # post-terminal snapshot work can await exactly this finalizer instead
        # of racing a sibling generation that reuses the same durable session.
        setattr(finalize_task, "_http_bridge_operation_id", operation_id)
        setattr(finalize_task, "_http_bridge_session_id", session_id)
        setattr(finalize_task, "_http_bridge_owner_epoch", owner_epoch)
        self._terminal_finalize_tasks.add(finalize_task)
        finalize_task.add_done_callback(self._terminal_finalize_done)

    async def _finalize_terminal_spool(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        expected_state: str,
    ) -> None:
        try:
            finalized = await self._durable_bridge.finalize_operation_event_spool(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                expected_state=expected_state,
            )
            if not finalized:
                logger.debug("Terminal HTTP bridge spool finalization was fenced operation_id=%s", operation_id)
        except Exception:
            logger.debug(
                "Failed to finalize terminal HTTP bridge spool operation_id=%s",
                operation_id,
                exc_info=True,
            )

    def _terminal_finalize_done(self, task: asyncio.Task[None]) -> None:
        self._terminal_finalize_tasks.discard(task)
        if task.cancelled():
            return
        task.result()

    async def settle_terminal_event(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        state: str,
        expected_response_id: str | None,
        expected_recovery_dispatch_count: int | None = None,
        alternate_expected_response_id: str | None = None,
        response_id: str | None = None,
    ) -> None:
        """Settle a failed terminal append after its SSE block was queued."""
        try:
            settled = await self._durable_bridge.settle_terminal_append_failure(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                state=state,
                expected_response_id=expected_response_id,
                expected_recovery_dispatch_count=expected_recovery_dispatch_count,
                alternate_expected_response_id=alternate_expected_response_id,
                response_id=response_id,
            )
            if not settled:
                logger.warning(
                    "Terminal HTTP bridge operation fallback settlement was fenced operation_id=%s",
                    operation_id,
                )
        except Exception:
            logger.warning(
                "Failed to settle terminal HTTP bridge operation after event append failure operation_id=%s",
                operation_id,
                exc_info=True,
            )

    async def flush_pending_operation(self, *, operation_id: str) -> bool:
        """Drain queued events while retaining the operation context."""
        while True:
            await self._flush_one(operation_id)
            async with self._lock:
                has_pending = bool(self._pending.get(operation_id))
                inflight = self._inflight_flushes.get(operation_id, 0)
                completion_event = self._flush_completion_events.get(operation_id)
            if has_pending:
                continue
            if inflight and completion_event is not None:
                await completion_event.wait()
                continue
            break
        async with self._lock:
            return operation_id not in self._dropped_operations

    async def pending_operation_ids(self) -> set[str]:
        """Return operation IDs still owned by the in-memory spooler.

        Contexts and closing operations are included because a terminal
        settlement may have drained the event queue while its final durable
        write is still in flight.
        """
        async with self._lock:
            return set(self._pending) | set(self._contexts) | set(self._closing_operations)

    async def discard_operation(self, *, operation_id: str) -> None:
        """Drop an abandoned nonterminal context without finalizing its spool."""
        async with self._flush_lock:
            async with self._lock:
                context = self._contexts.get(operation_id)
                current_generation = self._operation_generations.get(operation_id, 0)
                if context is not None and context.recovery_dispatch_count > current_generation:
                    return
                pending = self._pending.pop(operation_id, [])
                self._pending_count -= len(pending)
                self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in pending)
                self._contexts.pop(operation_id, None)
                self._operation_generations.pop(operation_id, None)
                self._closing_operations.discard(operation_id)
                self._dropped_operations.discard(operation_id)
                self._cleanup_operation_state_locked(operation_id)

    async def drain_terminal_finalizers(
        self,
        *,
        session_id: str | None = None,
        owner_epoch: int | None = None,
    ) -> None:
        """Await tracked terminal finalizers, optionally scoped to one owner generation."""
        tasks = tuple(self._terminal_finalize_tasks)
        if session_id is not None:
            tasks = tuple(task for task in tasks if getattr(task, "_http_bridge_session_id", None) == session_id)
        if owner_epoch is not None:
            tasks = tuple(
                task for task in tasks if getattr(task, "_http_bridge_owner_epoch", owner_epoch) == owner_epoch
            )
        await self._drain_terminal_tasks(tasks, kind="finalize", cancel=False)

    async def fence_operation(self, *, operation_id: str, recovery_dispatch_count: int) -> None:
        """Drop queued events from an attempt after its operation is rebound.

        The flush lock makes the drain atomic with respect to the background
        writer. The durable append path also checks the generation, covering
        the small window between the database rebind and this in-memory drain.
        """
        recovery_dispatch_count = max(0, int(recovery_dispatch_count))
        operation_lock = await self._operation_lock_for(operation_id)
        acquired = False
        try:
            await operation_lock.acquire()
            acquired = True
            async with self._flush_lock:
                async with self._lock:
                    current_generation = self._operation_generations.get(operation_id, 0)
                    if recovery_dispatch_count <= current_generation:
                        return
                    self._cancel_generation_cleanup_locked(operation_id)
                    self._operation_generations[operation_id] = recovery_dispatch_count
                    self._schedule_generation_cleanup_locked(operation_id, recovery_dispatch_count)
                    self._contexts.pop(operation_id, None)
                    pending = self._pending.pop(operation_id, [])
                    self._pending_count -= len(pending)
                    self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in pending)
                    self._closing_operations.discard(operation_id)
                    self._dropped_operations.discard(operation_id)
        finally:
            if acquired:
                operation_lock.release()
            await self._release_operation_lock_user(operation_id)

    async def rollback_fence_operation(self, *, operation_id: str, recovery_dispatch_count: int) -> bool:
        """Restore a generation fence after a durable recovery rollback.

        The durable rollback uses the previous generation as a compare-and-set
        guard. Mirror that guard in memory so a concurrent replacement cannot
        be unfenced by stale cleanup from an older recovery attempt. Once the
        rollback succeeds there is no active queued/context state to protect.
        Retain a non-zero restored generation so late events from older
        attempts remain fenced; only generation zero can drop the entry
        without weakening that guard.
        """
        recovery_dispatch_count = max(0, int(recovery_dispatch_count))
        fenced_generation = recovery_dispatch_count + 1
        operation_lock = await self._operation_lock_for(operation_id)
        acquired = False
        try:
            await operation_lock.acquire()
            acquired = True
            async with self._flush_lock:
                async with self._lock:
                    current_generation = self._operation_generations.get(operation_id)
                    if current_generation != fenced_generation:
                        return False
                    pending = self._pending.pop(operation_id, [])
                    self._pending_count -= len(pending)
                    self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in pending)
                    self._contexts.pop(operation_id, None)
                    self._closing_operations.discard(operation_id)
                    self._dropped_operations.discard(operation_id)
                    if recovery_dispatch_count == 0:
                        self._cancel_generation_cleanup_locked(operation_id)
                        self._operation_generations.pop(operation_id, None)
                    else:
                        self._operation_generations[operation_id] = recovery_dispatch_count
                        self._schedule_generation_cleanup_locked(operation_id, recovery_dispatch_count)
                    return True
        finally:
            if acquired:
                operation_lock.release()
            await self._release_operation_lock_user(operation_id)

    async def close(self) -> None:
        """Cancel and await generation-expiry tasks and the background flusher owned by this batcher."""
        async with self._lock:
            self._closing = True
        cleanup_tasks = list(self._generation_cleanup_tasks.values())
        self._generation_cleanup_tasks.clear()
        for cleanup_task in cleanup_tasks:
            cleanup_task.cancel()
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        # Let enqueue callers that crossed the admission fence finish before
        # stopping the flusher.  This prevents a producer that started just
        # before shutdown from publishing work after the close drain snapshot.
        shutdown_remaining = shutdown_state.remaining_drain_timeout_seconds()
        shutdown_deadline = (
            None if shutdown_remaining is None else clock_for(self).monotonic() + max(shutdown_remaining, 0.0)
        )
        while True:
            current = asyncio.current_task()
            async with self._lock:
                active_enqueue_callers = tuple(
                    caller for caller in self._enqueue_callers if caller is not current and not caller.done()
                )
            if not active_enqueue_callers:
                break
            if shutdown_deadline is None:
                await asyncio.sleep(0)
                continue
            remaining = shutdown_deadline - clock_for(self).monotonic()
            if remaining <= 0:
                logger.warning(
                    "HTTP bridge enqueue callers exceeded the shutdown deadline; cancelling count=%d",
                    len(active_enqueue_callers),
                )
                for caller in active_enqueue_callers:
                    caller.cancel()
                break
            _, pending_callers = await self._scheduler.wait(active_enqueue_callers, timeout=remaining)
            if pending_callers:
                logger.warning(
                    "HTTP bridge enqueue callers exceeded the shutdown deadline; cancelling count=%d",
                    len(pending_callers),
                )
                for caller in pending_callers:
                    caller.cancel()
                break
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await self._drain_terminal_tasks((task,), kind="flusher", cancel=False)
            except asyncio.CancelledError:
                pass
        if self._ordinary_append_tasks:
            await self._drain_terminal_tasks(tuple(self._ordinary_append_tasks), kind="ordinary append", cancel=False)
        # Let terminal append callers that crossed the admission fence finish
        # handing their result to the finalizer tracker before cancelling any
        # still-running append task.  Cancelling the inner append first would
        # make the outer caller lose a successful terminal write and could
        # strand a finalizer between the two shutdown snapshots.
        while True:
            current = asyncio.current_task()
            async with self._lock:
                active_callers = tuple(
                    caller for caller in self._terminal_append_callers if caller is not current and not caller.done()
                )
            if not active_callers:
                break
            if shutdown_deadline is None:
                await asyncio.sleep(0)
                continue
            remaining = shutdown_deadline - clock_for(self).monotonic()
            if remaining <= 0:
                logger.warning(
                    "HTTP bridge terminal append callers exceeded the shutdown deadline; draining count=%d",
                    len(active_callers),
                )
                break
            _, pending_callers = await self._scheduler.wait(active_callers, timeout=remaining)
            if pending_callers:
                logger.warning(
                    "HTTP bridge terminal append callers exceeded the shutdown deadline; draining count=%d",
                    len(pending_callers),
                )
                break
        # Any append caller that did not finish before the deadline is allowed
        # to observe its inner append task being cancelled below and return the
        # normal settlement-required result; cancelling the public caller here
        # would leak CancelledError to its owner instead.
        await self._drain_terminal_tasks(tuple(self._terminal_append_tasks), kind="append", cancel=True)
        # A flusher cancellation can restore an ordinary batch after dequeue
        # (for example if the durable task itself is cancelled before it
        # starts).  Drain that queue while the batcher still owns its durable
        # writer; otherwise close() would return with transcript data stranded
        # in memory.
        async with self._lock:
            pending_operation_ids = tuple(self._pending)
        # Drain through a bounded worker pool. Creating one task per pending
        # operation can exhaust the database pool before shutdown progresses.
        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        for operation_id in pending_operation_ids:
            pending_queue.put_nowait(operation_id)

        async def drain_pending_worker() -> None:
            while True:
                try:
                    operation_id = pending_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await self.flush_pending_operation(operation_id=operation_id)
                finally:
                    pending_queue.task_done()

        worker_count = min(8, len(pending_operation_ids))
        pending_flush_tasks = tuple(
            self._scheduler.create_task(drain_pending_worker(), name=f"http-bridge-close-flush-worker-{index}")
            for index in range(worker_count)
        )
        await self._drain_terminal_tasks(pending_flush_tasks, kind="pending", cancel=False)
        # A bounded pending drain can itself have started a durable append just
        # before the overall shutdown deadline.  Apply the same deadline to
        # those owned writer tasks before returning from close().
        if self._ordinary_append_tasks:
            await self._drain_terminal_tasks(tuple(self._ordinary_append_tasks), kind="ordinary append", cancel=False)
        # Yield once so a caller completing its finalizer handoff after the
        # append-task drain is visible before taking the finalizer snapshot.
        await asyncio.sleep(0)
        # A successful terminal append has already made the transcript eligible
        # for replay; cancelling its finalizer during shutdown would leave the
        # durable row permanently marked event_spool_complete=false. Finalizer
        # tasks are therefore drained to completion instead of cancelled.
        await self.drain_terminal_finalizers()

    async def _drain_terminal_tasks(
        self,
        tasks: tuple[asyncio.Task[Any], ...],
        *,
        kind: str,
        cancel: bool,
    ) -> None:
        if not tasks:
            return
        shutdown_remaining = shutdown_state.remaining_drain_timeout_seconds()
        shutdown_deadline = (
            None if shutdown_remaining is None else clock_for(self).monotonic() + max(shutdown_remaining, 0.0)
        )
        if cancel:
            for task in tasks:
                task.cancel()
        timeout = max(self._terminal_append_timeout_seconds, 0.0)
        if shutdown_remaining is not None:
            timeout = min(timeout, max(shutdown_remaining, 0.0))
        _, pending = await self._scheduler.wait(tasks, timeout=timeout)
        if not pending:
            return
        # A cancelled append, or an uncancelled finalizer, can still be inside
        # the durable layer's SQLite writer. Keep owning it to completion
        # instead of returning with a live task that could be destroyed with
        # the event loop. The durable layer bounds its own teardown.
        logger.warning(
            "HTTP bridge terminal %s tasks still pending after close bound; awaiting completion "
            "count=%d timeout_seconds=%.1f task_names=%s",
            kind,
            len(pending),
            self._terminal_append_timeout_seconds,
            sorted(str(getattr(task, "get_name", lambda: "<unknown>")()) for task in pending),
        )
        if shutdown_deadline is None:
            # Standalone callers do not have a process-level deadline; retain
            # the historical ownership guarantee and wait for durable writers.
            await asyncio.gather(*pending, return_exceptions=True)
            return

        remaining = max(shutdown_deadline - clock_for(self).monotonic(), 0.0)
        if remaining:
            _, pending = await self._scheduler.wait(pending, timeout=remaining)
        if not pending:
            return

        # The service-level shutdown path owns the final forced termination
        # decision once the shared deadline expires. Cancel what remains and
        # leave callbacks tracking completion so late durable work cannot be
        # mistaken for a clean drain.
        logger.error(
            "HTTP bridge terminal %s tasks exceeded the overall shutdown deadline; forcing termination count=%d",
            kind,
            len(pending),
        )
        for task in pending:
            task.cancel()
