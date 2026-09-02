from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any

from app.core.config.settings import get_settings
from app.db.models import HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2, HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
from app.modules.proxy.durable_bridge_repository import DurableBridgeOperationEventInput

logger = logging.getLogger("app.modules.proxy.http_bridge_event_batcher")

_GENERATION_FENCE_RETENTION_SECONDS = 300.0


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
    def from_settings(cls, durable_bridge: Any, settings: Any | None = None) -> "HttpBridgeOperationEventBatcher":
        """Build the event spooler from the operator-facing settings surface."""
        settings = settings or get_settings()
        return cls(
            durable_bridge,
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
    ) -> None:
        if spool_format not in {HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1, HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2}:
            raise ValueError("unsupported durable bridge operation spool format")
        self._durable_bridge = durable_bridge
        self._max_bytes = max_bytes
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_seconds
        self._max_pending_events = max_pending_events
        self._max_pending_bytes = max_pending_bytes
        self._spool_format = spool_format
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
        self._pending_count = 0
        self._pending_bytes = 0
        self._lock = asyncio.Lock()
        # Serialize enqueue/rebind state transitions with the durable append
        # for the same operation, without blocking unrelated operations.
        self._operation_locks: dict[str, asyncio.Lock] = {}
        # SQLite already serializes writers; this also prevents a background
        # flush racing a terminal drain and final marker for one operation.
        self._flush_lock = asyncio.Lock()
        self._generation_cleanup_tasks: dict[str, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

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
        self._ensure_task()
        recovery_dispatch_count = max(0, int(recovery_dispatch_count))
        pending = _PendingOperationEvent(
            operation_id=operation_id,
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            event_text=event_text,
            recovery_dispatch_count=recovery_dispatch_count,
        )
        operation_lock = self._operation_locks.setdefault(operation_id, asyncio.Lock())
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

    async def _enqueue_pending(
        self,
        pending: _PendingOperationEvent,
        *,
        event_text: str,
        terminal: bool,
        recovery_dispatch_count: int,
    ) -> bool:
        operation_id = pending.operation_id
        async with self._flush_lock:
            async with self._lock:
                current_generation = self._operation_generations.get(operation_id, 0)
                if recovery_dispatch_count < current_generation:
                    # A late event from the interrupted attempt must not be
                    # appended after a recovery rebind has claimed the operation.
                    return False
                self._cancel_generation_cleanup_locked(operation_id)
                if recovery_dispatch_count > current_generation:
                    self._operation_generations[operation_id] = recovery_dispatch_count
                current_context = self._contexts.get(operation_id)
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
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="http-bridge-operation-event-flusher")

    def _cancel_generation_cleanup_locked(self, operation_id: str) -> None:
        task = self._generation_cleanup_tasks.pop(operation_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _schedule_generation_cleanup_locked(self, operation_id: str, generation: int) -> None:
        """Bound a restored generation fence to the abandoned request lifetime.

        A rollback must keep fencing late events for a short window, but an
        operation that never resumes must not leave an entry in the process
        forever. The cleanup task only removes the exact generation it was
        scheduled for, so a newer enqueue/rebind cannot be unfenced by stale
        timer work.
        """
        self._cancel_generation_cleanup_locked(operation_id)

        async def cleanup() -> None:
            try:
                await asyncio.sleep(_GENERATION_FENCE_RETENTION_SECONDS)
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

        self._generation_cleanup_tasks[operation_id] = asyncio.create_task(
            cleanup(), name=f"http-bridge-generation-cleanup-{operation_id}"
        )

    async def _run(self) -> None:
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
        async with self._lock:
            return [operation_id for operation_id in self._pending if operation_id not in self._closing_operations]

    async def _take_batch(self, operation_id: str) -> list[_PendingOperationEvent]:
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
        operation_lock = self._operation_locks.setdefault(operation_id, asyncio.Lock())
        await operation_lock.acquire()
        async with self._flush_lock:
            batch = await self._take_batch(operation_id)
        if not batch:
            operation_lock.release()
            return
        # Re-check generation and owner context after taking the batch. A
        # concurrent recovery may have rebound the operation while the batch
        # was being removed from the in-memory queue; stale events must not be
        # appended under the replacement owner.
        async with self._lock:
            if operation_id in self._dropped_operations:
                operation_lock.release()
                return
            current_generation = self._operation_generations.get(operation_id, 0)
            current_context = self._contexts.get(operation_id)
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
                operation_lock.release()
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
                persisted = await self._durable_bridge.append_operation_event_chunk(
                    events=events,
                    max_bytes=self._max_bytes,
                )
            else:
                persisted = await self._durable_bridge.append_operation_events(
                    events=events,
                    max_bytes=self._max_bytes,
                )
            if not persisted:
                async with self._lock:
                    self._dropped_operations.add(operation_id)
                    dropped = self._pending.pop(operation_id, [])
                    self._pending_count -= len(dropped)
                    self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in dropped)
        except Exception:
            async with self._lock:
                self._dropped_operations.add(operation_id)
                dropped = self._pending.pop(operation_id, [])
                self._pending_count -= len(dropped)
                self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in dropped)
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
                    completion_event = self._flush_completion_events.get(operation_id)
                    if completion_event is not None:
                        completion_event.set()
                else:
                    self._inflight_flushes[operation_id] = remaining
            operation_lock.release()

    async def flush_operation(
        self,
        *,
        operation_id: str,
        expected_recovery_dispatch_count: int | None = None,
    ) -> None:
        await self.flush_pending_operation(operation_id=operation_id)
        async with self._lock:
            context = self._contexts.get(operation_id)
            expected_generation = (
                max(0, int(expected_recovery_dispatch_count))
                if expected_recovery_dispatch_count is not None
                else (context.recovery_dispatch_count if context is not None else 0)
            )
            if self._operation_generations.get(operation_id, 0) != expected_generation:
                return
            if context is not None and context.recovery_dispatch_count != expected_generation:
                return
            dropped = operation_id in self._dropped_operations
            self._closing_operations.discard(operation_id)
            self._contexts.pop(operation_id, None)
            self._operation_generations.pop(operation_id, None)
            self._dropped_operations.discard(operation_id)
        if dropped or context is None:
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
        expected_recovery_dispatch_count: int = 0,
        response_id: str | None = None,
    ) -> TerminalOperationEventAppendResult:
        """Drain queued events and atomically append the terminal outcome."""
        expected_recovery_dispatch_count = max(0, int(expected_recovery_dispatch_count))
        async with self._flush_lock:
            async with self._lock:
                current_generation = self._operation_generations.get(operation_id, 0)
                if expected_recovery_dispatch_count < current_generation:
                    # A terminal event from a superseded upstream attempt must not
                    # settle the replacement operation.
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
        await self.flush_pending_operation(operation_id=operation_id)
        async with self._lock:
            current_generation = self._operation_generations.get(operation_id, 0)
            context = self._contexts.get(operation_id)
            dropped = operation_id in self._dropped_operations
        if current_generation != expected_recovery_dispatch_count:
            return TerminalOperationEventAppendResult(persisted=False)
        if context is None:
            return TerminalOperationEventAppendResult(persisted=False)
        if dropped:
            async with self._lock:
                if self._operation_generations.get(operation_id, 0) == expected_recovery_dispatch_count:
                    self._closing_operations.discard(operation_id)
                    self._contexts.pop(operation_id, None)
                    self._operation_generations.pop(operation_id, None)
                    self._dropped_operations.discard(operation_id)
            return TerminalOperationEventAppendResult(persisted=False, settlement_required=True)
        try:
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
                )
            terminal_persisted = bool(persisted and not dropped)
            return TerminalOperationEventAppendResult(
                persisted=terminal_persisted,
                settlement_required=not terminal_persisted,
            )
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
                if self._operation_generations.get(operation_id, 0) == expected_recovery_dispatch_count:
                    self._closing_operations.discard(operation_id)
                    self._contexts.pop(operation_id, None)
                    self._operation_generations.pop(operation_id, None)
                    self._dropped_operations.discard(operation_id)

    async def settle_terminal_event(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        state: str,
        expected_response_id: str | None,
        expected_recovery_dispatch_count: int = 0,
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

    async def fence_operation(self, *, operation_id: str, recovery_dispatch_count: int) -> None:
        """Drop queued events from an attempt after its operation is rebound.

        The flush lock makes the drain atomic with respect to the background
        writer. The durable append path also checks the generation, covering
        the small window between the database rebind and this in-memory drain.
        """
        recovery_dispatch_count = max(0, int(recovery_dispatch_count))
        async with self._flush_lock:
            async with self._lock:
                current_generation = self._operation_generations.get(operation_id, 0)
                if recovery_dispatch_count <= current_generation:
                    return
                self._cancel_generation_cleanup_locked(operation_id)
                self._operation_generations[operation_id] = recovery_dispatch_count
                pending = self._pending.pop(operation_id, [])
                self._pending_count -= len(pending)
                self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in pending)
                self._closing_operations.discard(operation_id)
                self._dropped_operations.discard(operation_id)

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

    async def close(self) -> None:
        cleanup_tasks = list(self._generation_cleanup_tasks.values())
        self._generation_cleanup_tasks.clear()
        for cleanup_task in cleanup_tasks:
            cleanup_task.cancel()
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
