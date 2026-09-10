from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.core.config.settings import get_settings
from app.db.models import HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2, HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
from app.modules.proxy.durable_bridge_repository import DurableBridgeOperationEventInput

logger = logging.getLogger("app.modules.proxy.http_bridge_event_batcher")


@dataclass(frozen=True, slots=True)
class _PendingOperationEvent:
    operation_id: str
    session_id: str
    instance_id: str
    owner_epoch: int
    event_text: str


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
        self._dropped_operations: set[str] = set()
        self._closing_operations: set[str] = set()
        self._pending_count = 0
        self._pending_bytes = 0
        self._lock = asyncio.Lock()
        # SQLite already serializes writers; this also prevents a background
        # flush racing a terminal drain and final marker for one operation.
        self._flush_lock = asyncio.Lock()
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
    ) -> None:
        self._ensure_task()
        pending = _PendingOperationEvent(
            operation_id=operation_id,
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            event_text=event_text,
        )
        async with self._lock:
            self._contexts.setdefault(operation_id, pending)
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
        self._wake.set()
        if terminal:
            await self.flush_operation(operation_id=operation_id)

    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="http-bridge-operation-event-flusher")

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
        async with self._flush_lock:
            batch = await self._take_batch(operation_id)
            if not batch:
                return
            async with self._lock:
                if operation_id in self._dropped_operations:
                    return
            try:
                events = [
                    DurableBridgeOperationEventInput(
                        operation_id=item.operation_id,
                        session_id=item.session_id,
                        instance_id=item.instance_id,
                        owner_epoch=item.owner_epoch,
                        event_text=item.event_text,
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

    async def flush_operation(self, *, operation_id: str) -> None:
        await self.flush_pending_operation(operation_id=operation_id)
        async with self._lock:
            dropped = operation_id in self._dropped_operations
            context = self._contexts.get(operation_id)
            self._closing_operations.discard(operation_id)
            self._contexts.pop(operation_id, None)
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
        async with self._lock:
            self._contexts.setdefault(
                operation_id,
                _PendingOperationEvent(
                    operation_id=operation_id,
                    session_id=session_id,
                    instance_id=instance_id,
                    owner_epoch=owner_epoch,
                    event_text=event_text,
                ),
            )
            self._closing_operations.add(operation_id)
        await self.flush_pending_operation(operation_id=operation_id)
        async with self._lock:
            context = self._contexts.get(operation_id)
            dropped = operation_id in self._dropped_operations
        if context is None:
            return TerminalOperationEventAppendResult(persisted=False)
        if dropped:
            async with self._lock:
                self._closing_operations.discard(operation_id)
                self._contexts.pop(operation_id, None)
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
                self._closing_operations.discard(operation_id)
                self._contexts.pop(operation_id, None)
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
            if not has_pending:
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
                pending = self._pending.pop(operation_id, [])
                self._pending_count -= len(pending)
                self._pending_bytes -= sum(len(item.event_text.encode("utf-8")) for item in pending)
                self._contexts.pop(operation_id, None)
                self._closing_operations.discard(operation_id)
                self._dropped_operations.discard(operation_id)

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
