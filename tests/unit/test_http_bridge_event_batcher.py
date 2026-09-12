from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.modules.proxy.http_bridge_event_batcher import HttpBridgeOperationEventBatcher
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler


class _FakeDurableBridge:
    def __init__(self, *, append_result: bool = True, update_result: bool = True) -> None:
        """Configure append and settlement outcomes while recording row and chunk persistence calls."""
        self.append_result = append_result
        self.update_result = update_result
        self.batches: list[list[str]] = []
        self.chunk_batches: list[list[str]] = []
        self.terminal_rows: list[str] = []
        self.terminal_chunks: list[str] = []
        self.terminal_kwargs: list[dict[str, object]] = []
        # Canonical terminal-spool tests use this descriptive alias; the
        # generation-fence tests retain the shorter historical name.
        self.terminal_append_kwargs = self.terminal_kwargs
        self.finalized: list[str] = []
        self.updated: list[dict[str, object]] = []

    async def append_operation_events(self, *, events, max_bytes: int) -> bool:
        """Record legacy row batches and return the configured append outcome."""
        del max_bytes
        self.batches.append([event.event_text for event in events])
        return self.append_result

    async def append_operation_event_chunk(self, *, events, max_bytes: int) -> bool:
        """Record chunk batches and return the configured append outcome."""
        del max_bytes
        self.chunk_batches.append([event.event_text for event in events])
        return self.append_result

    async def append_terminal_operation_event(self, **kwargs) -> bool:
        """Record a terminal row append and return the configured durable-write result."""
        self.terminal_rows.append(kwargs["event_text"])
        self.terminal_kwargs.append(dict(kwargs))
        return self.append_result

    async def append_terminal_operation_chunk(self, **kwargs) -> bool:
        """Record a terminal chunk append and return the configured durable-write result."""
        self.terminal_chunks.append(kwargs["event_text"])
        self.terminal_kwargs.append(dict(kwargs))
        return self.append_result

    async def finalize_operation_event_spool(self, **kwargs) -> bool:
        """Record spool finalization and report success to the batcher."""
        self.finalized.append(kwargs["operation_id"])
        return True

    async def update_operation(self, **kwargs) -> bool:
        """Capture operation-settlement fields and return the configured update result."""
        self.updated.append(kwargs)
        return self.update_result

    async def settle_terminal_append_failure(self, **kwargs) -> bool:
        """Mark failed terminal capture incomplete before forwarding the settlement update."""
        kwargs["event_spool_complete"] = False
        return await self.update_operation(**kwargs)


class _OwnerConsistentDurableBridge(_FakeDurableBridge):
    """Mirror the repository's mixed-owner batch rejection contract."""

    async def append_operation_events(self, *, events, max_bytes: int) -> bool:
        """Reject batches containing mixed ownership identities before delegating to the recording writer."""
        identities = {(event.session_id, event.instance_id, event.owner_epoch) for event in events}
        if len(identities) > 1:
            raise ValueError("mixed owner context")
        return await super().append_operation_events(events=events, max_bytes=max_bytes)


class _TerminalAppendFailingDurableBridge(_FakeDurableBridge):
    def __init__(self, *, append_result: bool = True, update_result: bool = True) -> None:
        """Configure terminal append failure and a barrier signalling fallback settlement."""
        super().__init__(append_result=append_result, update_result=update_result)
        self.update_called = asyncio.Event()

    async def append_terminal_operation_event(self, **kwargs) -> bool:
        """Raise the injected terminal persistence failure without recording a successful append."""
        del kwargs
        raise RuntimeError("injected terminal append failure")

    async def update_operation(self, **kwargs) -> bool:
        """Record fallback settlement and signal that the terminal failure reached operation update."""
        result = await super().update_operation(**kwargs)
        self.update_called.set()
        return result


class _BlockingTerminalAppendDurableBridge(_FakeDurableBridge):
    def __init__(self) -> None:
        """Install explicit barriers for pausing and releasing terminal persistence."""
        super().__init__()
        self.terminal_started = asyncio.Event()
        self.release_terminal = asyncio.Event()

    async def append_terminal_operation_event(self, **kwargs) -> bool:
        """Record terminal input and block until the test releases the persistence barrier."""
        self.terminal_rows.append(kwargs["event_text"])
        self.terminal_kwargs.append(dict(kwargs))
        self.terminal_started.set()
        await self.release_terminal.wait()
        return True


class _BlockingBatchAppendDurableBridge(_FakeDurableBridge):
    def __init__(self) -> None:
        """Install explicit barriers for controlling in-flight batch writes."""
        super().__init__()
        self.batch_started = asyncio.Event()
        self.release_batch = asyncio.Event()

    async def append_operation_events(self, *, events, max_bytes: int) -> bool:
        """Record a row batch, signal admission, and wait for the test to release the write."""
        result = await super().append_operation_events(events=events, max_bytes=max_bytes)
        self.batch_started.set()
        await self.release_batch.wait()
        return result

    async def append_operation_event_chunk(self, *, events, max_bytes: int) -> bool:
        """Record a chunk batch, signal admission, and wait for the test to release the write."""
        result = await super().append_operation_event_chunk(events=events, max_bytes=max_bytes)
        self.batch_started.set()
        await self.release_batch.wait()
        return result


class _StalledTerminalDurableBridge(_FakeDurableBridge):
    def __init__(self) -> None:
        super().__init__()
        self.append_started = asyncio.Event()
        self.append_cancelled = asyncio.Event()

    async def _stall_terminal_append(self) -> bool:
        self.append_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.append_cancelled.set()
            raise
        raise AssertionError("stalled terminal append unexpectedly resumed")

    async def append_terminal_operation_event(self, **kwargs) -> bool:
        del kwargs
        return await self._stall_terminal_append()

    async def append_terminal_operation_chunk(self, **kwargs) -> bool:
        del kwargs
        return await self._stall_terminal_append()


class _CancellationResistantTerminalDurableBridge(_FakeDurableBridge):
    def __init__(self) -> None:
        super().__init__()
        self.append_started = asyncio.Event()
        self.append_cancelled = asyncio.Event()
        self.release_append = asyncio.Event()

    async def _stall_terminal_append(self) -> bool:
        self.append_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.append_cancelled.set()
            await self.release_append.wait()
            return False
        raise AssertionError("stalled terminal append unexpectedly resumed")

    async def append_terminal_operation_event(self, **kwargs) -> bool:
        del kwargs
        return await self._stall_terminal_append()

    async def append_terminal_operation_chunk(self, **kwargs) -> bool:
        del kwargs
        return await self._stall_terminal_append()


class _LateSuccessfulTerminalDurableBridge(_CancellationResistantTerminalDurableBridge):
    async def _append_late(self, kwargs: dict[str, object]) -> bool:
        self.terminal_append_kwargs.append(dict(kwargs))
        self.append_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.append_cancelled.set()
            await self.release_append.wait()
            return True
        raise AssertionError("stalled terminal append unexpectedly resumed")

    async def append_terminal_operation_event(self, **kwargs) -> bool:
        return await self._append_late(kwargs)

    async def append_terminal_operation_chunk(self, **kwargs) -> bool:
        return await self._append_late(kwargs)


class _StalledDrainDurableBridge(_FakeDurableBridge):
    def __init__(self) -> None:
        super().__init__()
        self.append_started = asyncio.Event()
        self.append_cancelled = asyncio.Event()

    async def _stall_pending_append(self) -> bool:
        self.append_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.append_cancelled.set()
            raise
        raise AssertionError("stalled pending append unexpectedly resumed")

    async def append_operation_events(self, **kwargs) -> bool:
        del kwargs
        return await self._stall_pending_append()

    async def append_operation_event_chunk(self, **kwargs) -> bool:
        del kwargs
        return await self._stall_pending_append()


class _DelayedFailingDrainDurableBridge(_FakeDurableBridge):
    def __init__(self) -> None:
        super().__init__()
        self.append_started = asyncio.Event()
        self.release_append = asyncio.Event()

    async def append_operation_events(self, **kwargs) -> bool:
        del kwargs
        self.append_started.set()
        await self.release_append.wait()
        return False


class _ShieldedStall:
    """Mimic ``close_session()``'s ``_shielded`` teardown: every cancellation is absorbed."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_count = 0

    async def stall(self) -> None:
        self.started.set()
        while True:
            try:
                await self.release.wait()
                return
            except asyncio.CancelledError:
                self.cancel_count += 1
                self.cancelled.set()


class _ShieldedTerminalAppendDurableBridge(_FakeDurableBridge):
    def __init__(self) -> None:
        super().__init__()
        self.append_stall = _ShieldedStall()

    async def append_terminal_operation_event(self, **kwargs) -> bool:
        del kwargs
        await self.append_stall.stall()
        return False

    async def append_terminal_operation_chunk(self, **kwargs) -> bool:
        del kwargs
        await self.append_stall.stall()
        return False


class _ShieldedFinalizeDurableBridge(_FakeDurableBridge):
    def __init__(self) -> None:
        super().__init__()
        self.finalize_stall = _ShieldedStall()

    async def finalize_operation_event_spool(self, **kwargs) -> bool:
        del kwargs
        await self.finalize_stall.stall()
        return True


async def _enqueue(
    batcher: HttpBridgeOperationEventBatcher,
    text: str,
    *,
    terminal: bool = False,
    recovery_dispatch_count: int = 0,
) -> None:
    """Queue a deterministic event using the test's operation and owner identity."""
    await batcher.enqueue(
        operation_id="op-1",
        session_id="session-1",
        instance_id="instance-1",
        owner_epoch=1,
        event_text=text,
        terminal=terminal,
        recovery_dispatch_count=recovery_dispatch_count,
    )


def test_from_settings_defaults_to_rows_and_accepts_chunk_canary() -> None:
    """Settings preserve row spooling by default and enable chunk spooling only explicitly."""
    durable = _FakeDurableBridge()

    default_batcher = HttpBridgeOperationEventBatcher.from_settings(durable, SimpleNamespace())
    chunk_batcher = HttpBridgeOperationEventBatcher.from_settings(
        durable,
        SimpleNamespace(http_responses_session_bridge_operation_spool_format="chunks_v2"),
    )

    assert default_batcher._spool_format == "rows_v1"
    assert chunk_batcher._spool_format == "chunks_v2"


def test_constructor_rejects_unknown_spool_format() -> None:
    """Invalid spool formats fail at construction rather than silently selecting a writer."""
    with pytest.raises(ValueError, match="unsupported"):
        HttpBridgeOperationEventBatcher(
            _FakeDurableBridge(),
            max_bytes=1024,
            spool_format="unknown",
        )


@pytest.mark.asyncio
async def test_batches_without_blocking_and_finalizes_terminal_event() -> None:
    """Event admission stays nonblocking while terminal persistence finalizes the operation."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=0.01,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "one")
        await _enqueue(batcher, "two")
        await _enqueue(batcher, "three", terminal=True)
        assert durable.batches == [["one", "two", "three"]]
        assert durable.finalized == ["op-1"]
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_background_flushes_nonterminal_events_as_one_batch() -> None:
    """The background flusher coalesces queued nonterminal events into one repository write."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=0.01,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "one")
        await _enqueue(batcher, "two")
        for _ in range(20):
            if durable.batches:
                break
            await asyncio.sleep(0.01)
        assert durable.batches == [["one", "two"]]
        assert durable.finalized == []
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_new_recovery_generation_clears_previous_overflow_marker() -> None:
    """A replacement generation must not inherit the predecessor's dropped marker."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=1,
    )
    # Keep the background flusher out of the admission window so the first
    # enqueue cannot drain before the second enqueue exercises overflow.
    batcher._task = asyncio.create_task(asyncio.sleep(60.0))
    try:
        await _enqueue(batcher, "first")
        await _enqueue(batcher, "overflow")
        assert batcher._dropped_operations == {"op-1"}

        await _enqueue(batcher, "replacement", recovery_dispatch_count=1)
        assert batcher._dropped_operations == set()
        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        assert durable.batches == [["replacement"]]
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_chunk_mode_routes_batch_and_terminal_without_legacy_writes() -> None:
    """Chunk mode persists both event batches and terminals without calling legacy row writers."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
        spool_format="chunks_v2",
    )
    try:
        await _enqueue(batcher, "one")
        await _enqueue(batcher, "two")
        result = await batcher.append_terminal_event(
            operation_id="op-1",
            session_id="session-1",
            instance_id="instance-1",
            owner_epoch=1,
            event_text="terminal",
            max_bytes=1024,
            state="completed",
            response_id="resp-1",
        )

        assert result.persisted is True
        assert durable.chunk_batches == [["one", "two"]]
        assert durable.terminal_rows == []
        assert durable.terminal_chunks == ["terminal"]
        assert durable.terminal_append_kwargs[0]["complete_spool"] is False
        for _ in range(10):
            if durable.finalized:
                break
            await asyncio.sleep(0)
        assert durable.finalized == ["op-1"]
        assert durable.batches == []
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_pending_operation_ids_include_context_until_terminal_settlement() -> None:
    """Operation ownership remains discoverable until terminal settlement releases its context."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "one")
        assert await batcher.pending_operation_ids() == {"op-1"}
        await batcher.discard_operation(operation_id="op-1")
        assert await batcher.pending_operation_ids() == set()
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_dropped_batch_requires_fenced_terminal_settlement() -> None:
    """Losing a nonterminal batch still requires ownership-fenced terminal settlement."""
    durable = _FakeDurableBridge(append_result=False)
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=0.01,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "one", recovery_dispatch_count=1)
        for _ in range(20):
            if durable.batches:
                break
            await asyncio.sleep(0.01)
        result = await batcher.append_terminal_event(
            operation_id="op-1",
            session_id="session-1",
            instance_id="instance-1",
            owner_epoch=1,
            event_text="terminal",
            max_bytes=1024,
            state="failed",
            expected_recovery_dispatch_count=1,
        )
        assert result.persisted is False
        assert result.settlement_required is True
        assert durable.finalized == []
        assert durable.updated == []
        assert batcher._contexts == {}
        assert batcher._operation_generations == {}
        assert batcher._dropped_operations == set()
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_append_failure_settles_operation() -> None:
    """A terminal write exception must not bypass operation settlement."""
    durable = _TerminalAppendFailingDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
    )

    result = await batcher.append_terminal_event(
        operation_id="op-1",
        session_id="session-1",
        instance_id="instance-1",
        owner_epoch=7,
        event_text="terminal",
        max_bytes=1024,
        state="failed",
        response_id="resp-1",
    )

    assert result.persisted is False
    assert result.settlement_required is True
    await batcher.settle_terminal_event(
        operation_id="op-1",
        session_id="session-1",
        instance_id="instance-1",
        owner_epoch=7,
        state="failed",
        expected_response_id="resp-upstream-1",
        response_id="resp-1",
    )
    await asyncio.wait_for(durable.update_called.wait(), timeout=1.0)
    assert durable.updated == [
        {
            "operation_id": "op-1",
            "session_id": "session-1",
            "instance_id": "instance-1",
            "owner_epoch": 7,
            "state": "failed",
            "expected_response_id": "resp-upstream-1",
            "expected_recovery_dispatch_count": None,
            "alternate_expected_response_id": None,
            "response_id": "resp-1",
            "event_spool_complete": False,
        }
    ]


@pytest.mark.asyncio
async def test_terminal_append_false_requires_fallback_settlement() -> None:
    """A rejected terminal append requests fallback settlement instead of claiming success."""
    durable = _FakeDurableBridge(append_result=False)
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
    )

    result = await batcher.append_terminal_event(
        operation_id="op-1",
        session_id="session-1",
        instance_id="instance-1",
        owner_epoch=7,
        event_text="terminal",
        max_bytes=1024,
        state="failed",
        response_id="resp-1",
    )

    assert result.persisted is False
    assert result.settlement_required is True


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_stalled_terminal_append_is_bounded_and_requires_settlement(spool_format: str) -> None:
    durable = _StalledTerminalDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        spool_format=spool_format,
        terminal_append_timeout_seconds=0.01,
    )

    result = await asyncio.wait_for(
        batcher.append_terminal_event(
            operation_id="op-1",
            session_id="session-1",
            instance_id="instance-1",
            owner_epoch=7,
            event_text="terminal",
            max_bytes=1024,
            state="completed",
            response_id="resp-1",
        ),
        timeout=1.0,
    )

    assert durable.append_started.is_set()
    await asyncio.wait_for(durable.append_cancelled.wait(), timeout=1.0)
    assert result.persisted is False
    assert result.settlement_required is True
    assert batcher._contexts == {}
    assert batcher._closing_operations == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_cancellation_resistant_terminal_append_does_not_extend_delivery_bound(spool_format: str) -> None:
    durable = _CancellationResistantTerminalDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        spool_format=spool_format,
        terminal_append_timeout_seconds=0.01,
    )
    try:
        result = await asyncio.wait_for(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=7,
                event_text="terminal",
                max_bytes=1024,
                state="completed",
                response_id="resp-1",
            ),
            timeout=0.1,
        )

        assert durable.append_started.is_set()
        await asyncio.wait_for(durable.append_cancelled.wait(), timeout=1.0)
        assert result.persisted is False
        assert result.settlement_required is True
        assert batcher._contexts == {}
        assert batcher._closing_operations == set()
        late_append_tasks = tuple(batcher._terminal_append_tasks)
        assert len(late_append_tasks) == 1
        durable.release_append.set()
        await asyncio.wait_for(late_append_tasks[0], timeout=1.0)
        await asyncio.sleep(0)
        assert batcher._terminal_append_tasks == set()
    finally:
        durable.release_append.set()
        await batcher.close()


@pytest.mark.asyncio
async def test_timed_out_terminal_append_schedules_bounded_generation_cleanup() -> None:
    """A timed-out nonzero generation keeps only a bounded late-event fence."""
    durable = _CancellationResistantTerminalDurableBridge()
    scheduler = VirtualScheduler(VirtualClock())
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=0.01,
        scheduler=scheduler,
    )
    try:
        result = await batcher.append_terminal_event(
            operation_id="op-1",
            session_id="session-1",
            instance_id="instance-1",
            owner_epoch=1,
            event_text="terminal",
            max_bytes=1024,
            state="completed",
            expected_recovery_dispatch_count=1,
        )
        assert result.persisted is False
        assert result.settlement_required is True
        assert batcher._operation_generations == {"op-1": 1}
        assert batcher._generation_cleanup_tasks.keys() == {"op-1"}

        late_tasks = tuple(batcher._terminal_append_tasks)
        durable.release_append.set()
        await asyncio.gather(*late_tasks)
        await scheduler.drain()
        await scheduler.advance(300.0)
        assert batcher._operation_generations == {}
        assert batcher._generation_cleanup_tasks == {}
    finally:
        durable.release_append.set()
        await batcher.close()
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_late_success_cannot_finalize_terminal_spool(spool_format: str) -> None:
    durable = _LateSuccessfulTerminalDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        spool_format=spool_format,
        terminal_append_timeout_seconds=0.01,
    )
    try:
        result = await batcher.append_terminal_event(
            operation_id="op-1",
            session_id="session-1",
            instance_id="instance-1",
            owner_epoch=7,
            event_text="terminal",
            max_bytes=1024,
            state="completed",
            response_id="resp-1",
        )

        assert result.persisted is False
        assert result.settlement_required is True
        durable.release_append.set()
        late_append_task = next(iter(batcher._terminal_append_tasks))
        await asyncio.wait_for(late_append_task, timeout=1.0)
        await asyncio.sleep(0)

        assert durable.terminal_append_kwargs[0]["complete_spool"] is False
        assert durable.finalized == []
    finally:
        durable.release_append.set()
        await batcher.close()


@pytest.mark.asyncio
async def test_context_discarded_during_terminal_drain_requires_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
    )

    async def discard_during_drain(*, operation_id: str) -> bool:
        await batcher.discard_operation(operation_id=operation_id)
        return True

    monkeypatch.setattr(batcher, "flush_pending_operation", discard_during_drain)
    result = await batcher.append_terminal_event(
        operation_id="op-1",
        session_id="session-1",
        instance_id="instance-1",
        owner_epoch=7,
        event_text="terminal",
        max_bytes=1024,
        state="failed",
        response_id="resp-1",
    )

    assert result.persisted is False
    assert result.settlement_required is True
    assert durable.terminal_chunks == []


@pytest.mark.asyncio
async def test_close_turns_cancelled_terminal_append_into_settlement_required() -> None:
    durable = _StalledTerminalDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=60.0,
    )
    append_task = asyncio.create_task(
        batcher.append_terminal_event(
            operation_id="op-1",
            session_id="session-1",
            instance_id="instance-1",
            owner_epoch=7,
            event_text="terminal",
            max_bytes=1024,
            state="completed",
            response_id="resp-1",
        )
    )

    await asyncio.wait_for(durable.append_started.wait(), timeout=1.0)
    await asyncio.wait_for(batcher.close(), timeout=1.0)
    result = await asyncio.wait_for(append_task, timeout=1.0)

    assert result.persisted is False
    assert result.settlement_required is True
    assert batcher._contexts == {}
    assert batcher._closing_operations == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_stalled_pending_drain_is_bounded_and_requires_settlement(spool_format: str) -> None:
    durable = _StalledDrainDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        spool_format=spool_format,
        terminal_append_timeout_seconds=0.01,
    )
    batcher._task = asyncio.create_task(asyncio.sleep(60.0))
    try:
        await _enqueue(batcher, "pending")

        result = await asyncio.wait_for(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=7,
                event_text="terminal",
                max_bytes=1024,
                state="completed",
                response_id="resp-1",
            ),
            timeout=1.0,
        )

        assert durable.append_started.is_set()
        await asyncio.wait_for(durable.append_cancelled.wait(), timeout=1.0)
        assert result.persisted is False
        assert result.settlement_required is True
        assert batcher._pending == {}
        assert batcher._pending_count == 0
        assert batcher._pending_bytes == 0
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_late_background_failure_after_terminal_timeout_does_not_leak_drop_state() -> None:
    durable = _DelayedFailingDrainDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=0.01,
    )
    try:
        await _enqueue(batcher, "pending")
        await asyncio.wait_for(durable.append_started.wait(), timeout=1.0)

        result = await asyncio.wait_for(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=7,
                event_text="terminal",
                max_bytes=1024,
                state="completed",
                response_id="resp-1",
            ),
            timeout=1.0,
        )
        durable.release_append.set()
        await asyncio.wait_for(batcher._flush_lock.acquire(), timeout=1.0)
        batcher._flush_lock.release()

        assert result.settlement_required is True
        assert batcher._contexts == {}
        assert batcher._dropped_operations == set()
    finally:
        durable.release_append.set()
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_append_failure_reports_fenced_settlement(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Terminal failure reports the settlement outcome under the operation's ownership fence."""
    durable = _TerminalAppendFailingDurableBridge(update_result=False)
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
    )

    result = await batcher.append_terminal_event(
        operation_id="op-1",
        session_id="session-1",
        instance_id="stale-instance",
        owner_epoch=6,
        event_text="terminal",
        max_bytes=1024,
        state="failed",
    )

    assert result.persisted is False
    assert result.settlement_required is True
    await batcher.settle_terminal_event(
        operation_id="op-1",
        session_id="session-1",
        instance_id="stale-instance",
        owner_epoch=6,
        state="failed",
        expected_response_id=None,
    )
    await asyncio.wait_for(durable.update_called.wait(), timeout=1.0)
    assert durable.updated[0]["owner_epoch"] == 6
    assert "fallback settlement was fenced operation_id=op-1" in caplog.text


@pytest.mark.asyncio
async def test_discard_operation_releases_partial_nonterminal_context() -> None:
    """Discarding an unfinished operation frees its buffered events and retained context."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "partial")
        await batcher.discard_operation(operation_id="op-1")
        assert batcher._pending == {}
        assert batcher._contexts == {}
        assert batcher._pending_count == 0
        assert batcher._pending_bytes == 0
        assert durable.batches == []
        assert durable.finalized == []
    finally:
        await batcher.close()


async def _wait_for_warning(caplog: pytest.LogCaptureFixture, needle: str) -> logging.LogRecord:
    async with asyncio.timeout(1.0):
        while True:
            for record in caplog.records:
                if record.levelno == logging.WARNING and needle in record.getMessage():
                    return record
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_close_owns_terminal_append_pending_past_bound(caplog: pytest.LogCaptureFixture) -> None:
    durable = _ShieldedTerminalAppendDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=0.01,
    )
    # A failed assertion must not leave the shielded stall absorbing the loop's
    # teardown cancellation forever; always release it.
    try:
        result = await asyncio.wait_for(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=7,
                event_text="terminal",
                max_bytes=1024,
                state="completed",
                response_id="resp-1",
            ),
            timeout=1.0,
        )
        assert result.persisted is False
        assert result.settlement_required is True
        await asyncio.wait_for(durable.append_stall.cancelled.wait(), timeout=1.0)
        late_tasks = tuple(batcher._terminal_append_tasks)
        assert len(late_tasks) == 1
        late_task = late_tasks[0]
        assert not late_task.done()

        with caplog.at_level(logging.WARNING, logger="app.modules.proxy.http_bridge_event_batcher"):
            close_task = asyncio.create_task(batcher.close())
            record = await _wait_for_warning(caplog, "http-bridge-terminal-spool-op-1")
            # The bound elapsed with the shielded write still pending: close()
            # must keep owning the task instead of returning and dropping it.
            assert "terminal append tasks still pending after close bound" in record.getMessage()
            assert "count=1" in record.getMessage()
            assert not close_task.done()
            assert not late_task.done()
            assert late_task in batcher._terminal_append_tasks
            assert durable.append_stall.cancel_count >= 1

            durable.append_stall.release.set()
            await asyncio.wait_for(close_task, timeout=1.0)

        assert late_task.done()
        assert not late_task.cancelled()
        assert batcher._terminal_append_tasks == set()
        assert batcher._contexts == {}
        assert batcher._closing_operations == set()
    finally:
        durable.append_stall.release.set()


@pytest.mark.asyncio
async def test_close_owns_terminal_finalize_pending_past_bound(caplog: pytest.LogCaptureFixture) -> None:
    durable = _ShieldedFinalizeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=0.01,
    )
    try:
        result = await asyncio.wait_for(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=7,
                event_text="terminal",
                max_bytes=1024,
                state="completed",
                response_id="resp-1",
            ),
            timeout=1.0,
        )
        assert result.persisted is True
        await asyncio.wait_for(durable.finalize_stall.started.wait(), timeout=1.0)
        finalize_tasks = tuple(batcher._terminal_finalize_tasks)
        assert len(finalize_tasks) == 1
        finalize_task = finalize_tasks[0]

        with caplog.at_level(logging.WARNING, logger="app.modules.proxy.http_bridge_event_batcher"):
            close_task = asyncio.create_task(batcher.close())
            record = await _wait_for_warning(caplog, "http-bridge-terminal-spool-finalize-op-1")
            assert "terminal finalize tasks still pending after close bound" in record.getMessage()
            assert not close_task.done()
            assert not finalize_task.done()
            assert finalize_task in batcher._terminal_finalize_tasks
            assert durable.finalize_stall.cancel_count == 0

            durable.finalize_stall.release.set()
            await asyncio.wait_for(close_task, timeout=1.0)

        assert finalize_task.done()
        assert not finalize_task.cancelled()
        assert batcher._terminal_finalize_tasks == set()
    finally:
        durable.finalize_stall.release.set()


@pytest.mark.asyncio
async def test_terminal_append_caller_stays_tracked_until_finalizer_handoff() -> None:
    """Shutdown tracking covers the caller window between append completion and finalizer scheduling."""
    durable = _BlockingTerminalAppendDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=1.0,
    )
    try:
        append_task = asyncio.create_task(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=1,
                event_text="terminal",
                max_bytes=1024,
                state="completed",
            )
        )
        await asyncio.wait_for(durable.terminal_started.wait(), timeout=1.0)
        assert len(batcher._terminal_append_callers) == 1
        durable.release_terminal.set()
        result = await asyncio.wait_for(append_task, timeout=1.0)
        assert result.persisted is True
        assert batcher._terminal_append_callers == set()
        await batcher.close()
    finally:
        durable.release_terminal.set()
        await batcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("flush_before_fence", [False, True])
async def test_fence_operation_drops_late_events_from_interrupted_generation(flush_before_fence: bool) -> None:
    """Recovery fencing prevents late events from the interrupted generation being persisted."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "old-before-rebind")
        if flush_before_fence:
            await batcher.flush_pending_operation(operation_id="op-1")
        batcher._closing_operations.add("op-1")
        await batcher.fence_operation(operation_id="op-1", recovery_dispatch_count=1)
        assert "op-1" not in batcher._closing_operations
        await _enqueue(batcher, "old-after-rebind")
        await _enqueue(batcher, "replacement", recovery_dispatch_count=1)
        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        assert durable.batches in ([["replacement"]], [["old-before-rebind"], ["replacement"]])
        if flush_before_fence:
            assert durable.batches == [["old-before-rebind"], ["replacement"]]
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_enqueue_refreshes_context_when_owner_identity_changes() -> None:
    """A new owner refreshes the persistence context used for subsequent queued events."""
    durable = _OwnerConsistentDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "original", recovery_dispatch_count=1)
        await batcher.enqueue(
            operation_id="op-1",
            session_id="replacement-session",
            instance_id="replacement-instance",
            owner_epoch=9,
            event_text="replacement",
            recovery_dispatch_count=1,
        )

        context = batcher._contexts["op-1"]
        assert context.session_id == "replacement-session"
        assert context.instance_id == "replacement-instance"
        assert context.owner_epoch == 9
        queued = batcher._pending.get("op-1", [])
        assert all(
            (item.session_id, item.instance_id, item.owner_epoch) == ("replacement-session", "replacement-instance", 9)
            for item in queued
        )
        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        assert [event for batch in durable.batches for event in batch] == ["original", "replacement"]
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_enqueue_ignores_stale_owner_refresh_for_same_generation() -> None:
    """Same-generation stale ownership cannot overwrite the active event context."""
    durable = _OwnerConsistentDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await batcher.enqueue(
            operation_id="op-1",
            session_id="successor-session",
            instance_id="successor-instance",
            owner_epoch=9,
            event_text="successor",
            recovery_dispatch_count=1,
        )
        await batcher.enqueue(
            operation_id="op-1",
            session_id="predecessor-session",
            instance_id="predecessor-instance",
            owner_epoch=8,
            event_text="stale-predecessor",
            recovery_dispatch_count=1,
        )

        context = batcher._contexts["op-1"]
        assert (context.session_id, context.instance_id, context.owner_epoch) == (
            "successor-session",
            "successor-instance",
            9,
        )
        queued = batcher._pending.get("op-1", [])
        assert all(item.event_text == "successor" for item in queued)
        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        assert [event for batch in durable.batches for event in batch] == ["successor"]
    finally:
        await batcher.discard_operation(operation_id="op-1")
        await batcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_same_context_enqueue_does_not_wait_for_inflight_append(spool_format: str) -> None:
    """Admission for unchanged ownership does not block behind an in-flight database write."""
    durable = _BlockingBatchAppendDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
        spool_format=spool_format,
    )
    tasks: list[asyncio.Task] = []
    try:
        await _enqueue(batcher, "original", recovery_dispatch_count=1)
        tasks.append(asyncio.create_task(batcher.flush_pending_operation(operation_id="op-1")))
        await durable.batch_started.wait()

        # Completion must not depend on releasing the blocked database append.
        await asyncio.wait_for(_enqueue(batcher, "next", recovery_dispatch_count=1), timeout=1.0)
        terminal_task = asyncio.create_task(_enqueue(batcher, "terminal", terminal=True, recovery_dispatch_count=1))
        tasks.append(terminal_task)
        await asyncio.sleep(0)
        assert not terminal_task.done()
        assert durable.finalized == []

        durable.release_batch.set()
        await asyncio.gather(*tasks)
        batches = durable.batches if spool_format == "rows_v1" else durable.chunk_batches
        assert [event for batch in batches for event in batch] == ["original", "next", "terminal"]
        assert durable.finalized == ["op-1"]
    finally:
        durable.release_batch.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await batcher.discard_operation(operation_id="op-1")
        await batcher.close()


@pytest.mark.asyncio
async def test_owner_rebinding_waits_for_inflight_flush() -> None:
    """Owner rebinding serializes with an old-owner flush to prevent mixed ownership writes."""
    durable = _BlockingBatchAppendDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "original", recovery_dispatch_count=1)
        flush_task = asyncio.create_task(batcher.flush_pending_operation(operation_id="op-1"))
        await durable.batch_started.wait()

        rebind_task = asyncio.create_task(
            batcher.enqueue(
                operation_id="op-1",
                session_id="replacement-session",
                instance_id="replacement-instance",
                owner_epoch=9,
                event_text="replacement",
                recovery_dispatch_count=1,
            )
        )
        await asyncio.sleep(0)
        # Rebinding the same operation waits for its in-flight durable append,
        # while unrelated operations remain free of the global flush lock.
        assert not rebind_task.done()

        durable.release_batch.set()
        await flush_task
        await rebind_task
        assert durable.batches[0] == ["original"]

        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        assert durable.batches == [["original"], ["replacement"]]
    finally:
        await batcher.discard_operation(operation_id="op-1")
        await batcher.close()


@pytest.mark.asyncio
async def test_fence_operation_waits_for_inflight_append() -> None:
    """A recovery fence waits for an admitted append before retiring its generation."""
    durable = _BlockingBatchAppendDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "original", recovery_dispatch_count=0)
        flush_task = asyncio.create_task(batcher.flush_pending_operation(operation_id="op-1"))
        await durable.batch_started.wait()

        fence_task = asyncio.create_task(batcher.fence_operation(operation_id="op-1", recovery_dispatch_count=1))
        await asyncio.sleep(0)
        assert not fence_task.done()

        durable.release_batch.set()
        await flush_task
        await fence_task

        assert batcher._operation_generations == {"op-1": 1}
        await _enqueue(batcher, "replacement", recovery_dispatch_count=1)
        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        assert durable.batches == [["original"], ["replacement"]]
    finally:
        await batcher.discard_operation(operation_id="op-1")
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_append_waits_for_inflight_batch_flush() -> None:
    """Terminal persistence follows outstanding batches so stored event order is preserved."""
    durable = _BlockingBatchAppendDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
    )
    try:
        await _enqueue(batcher, "event-before-terminal")
        flush_task = asyncio.create_task(batcher.flush_pending_operation(operation_id="op-1"))
        await durable.batch_started.wait()

        terminal_task = asyncio.create_task(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=1,
                event_text="terminal",
                max_bytes=1024,
                state="completed",
            )
        )
        await asyncio.sleep(0)
        assert not terminal_task.done()

        durable.release_batch.set()
        await flush_task
        result = await terminal_task

        assert result.persisted is True
        assert durable.batches == [["event-before-terminal"]]
        assert durable.terminal_rows == ["terminal"]
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_flush_releases_operation_synchronization_state() -> None:
    """Completed operations release their per-operation locks and closing markers."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    await _enqueue(batcher, "terminal", terminal=True)

    assert batcher._operation_locks == {}
    assert batcher._operation_lock_users == {}
    assert batcher._flush_completion_events == {}

    await batcher.close()


@pytest.mark.asyncio
async def test_operation_lock_cleanup_retains_waiter_reference() -> None:
    """Lock cleanup must retain synchronization state while another task is waiting on it."""
    batcher = HttpBridgeOperationEventBatcher(
        _FakeDurableBridge(),
        max_bytes=1024,
        flush_interval_seconds=60.0,
    )
    operation_lock = await batcher._operation_lock_for("op-1")
    await operation_lock.acquire()
    await batcher._flush_lock.acquire()
    waiter = asyncio.create_task(batcher.fence_operation(operation_id="op-1", recovery_dispatch_count=1))
    try:
        for _ in range(20):
            if batcher._operation_lock_users.get("op-1") == 2:
                break
            await asyncio.sleep(0)
        assert batcher._operation_lock_users == {"op-1": 2}

        operation_lock.release()
        await batcher._release_operation_lock_user("op-1")
        await asyncio.sleep(0)

        # The fence waiter has the shared lock reference even though the lock
        # itself is currently unlocked while it waits for the flush lock.
        assert batcher._operation_lock_users == {"op-1": 1}
        assert batcher._operation_locks.get("op-1") is operation_lock
    finally:
        batcher._flush_lock.release()
        await waiter
        await batcher.discard_operation(operation_id="op-1")
        await batcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("flush_before_fence", [False, True])
async def test_rollback_fence_operation_restores_generation_after_rebind_rollback(flush_before_fence: bool) -> None:
    """Compensating a rebind restores the prior generation's event-admission fence."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "old-before-rebind")
        if flush_before_fence:
            await batcher.flush_pending_operation(operation_id="op-1")
        await batcher.fence_operation(operation_id="op-1", recovery_dispatch_count=1)
        assert await batcher.rollback_fence_operation(operation_id="op-1", recovery_dispatch_count=0) is True
        assert "op-1" not in batcher._operation_generations
        assert await batcher.rollback_fence_operation(operation_id="op-1", recovery_dispatch_count=0) is False
        await _enqueue(batcher, "replacement-after-rollback")
        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        expected = [["replacement-after-rollback"]]
        assert durable.batches in (expected, [["old-before-rebind"], *expected])
        if flush_before_fence:
            assert durable.batches == [["old-before-rebind"], *expected]
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_rollback_generation_cleanup_uses_injected_scheduler() -> None:
    """Delayed fence cleanup is owned by the injected scheduler rather than an orphan task."""
    scheduler = VirtualScheduler(VirtualClock())
    batcher = HttpBridgeOperationEventBatcher(_FakeDurableBridge(), max_bytes=1024, scheduler=scheduler)
    try:
        await batcher.fence_operation(operation_id="op-virtual", recovery_dispatch_count=2)
        assert await batcher.rollback_fence_operation(operation_id="op-virtual", recovery_dispatch_count=1)
        await scheduler.drain()
        assert batcher._operation_generations == {"op-virtual": 1}
        await scheduler.advance(299.0)
        assert batcher._operation_generations == {"op-virtual": 1}
        await scheduler.advance(1.0)
        assert batcher._operation_generations == {}
        assert scheduler.owned_tasks == frozenset()
    finally:
        await batcher.close()
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("flush_before_fence", [False, True])
async def test_rollback_fence_operation_retains_nonzero_restored_generation(flush_before_fence: bool) -> None:
    """Rolling back a later recovery retains its nonzero predecessor generation."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "generation-one", recovery_dispatch_count=1)
        if flush_before_fence:
            await batcher.flush_pending_operation(operation_id="op-1")
        await batcher.fence_operation(operation_id="op-1", recovery_dispatch_count=2)
        assert await batcher.rollback_fence_operation(operation_id="op-1", recovery_dispatch_count=1) is True
        assert batcher._operation_generations == {"op-1": 1}

        # The restored generation still fences events from the rolled-back
        # replacement while allowing the original generation to resume.
        await _enqueue(batcher, "stale-generation", recovery_dispatch_count=0)
        await _enqueue(batcher, "restored-generation", recovery_dispatch_count=1)
        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        expected = [["restored-generation"]]
        assert durable.batches in (expected, [["generation-one"], *expected])
        if flush_before_fence:
            assert durable.batches == [["generation-one"], *expected]
        assert durable.terminal_rows == []
    finally:
        await batcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("prior_events", [False, True])
async def test_rollback_fence_operation_bounded_cleanup_releases_abandoned_generation(
    rollback: bool, prior_events: bool
) -> None:
    """An unused restored generation is eventually released by bounded cleanup."""
    durable = _FakeDurableBridge()
    scheduler = VirtualScheduler(VirtualClock())
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
        scheduler=scheduler,
    )
    try:
        if prior_events:
            await _enqueue(batcher, "old", recovery_dispatch_count=1)
        await batcher.fence_operation(operation_id="op-1", recovery_dispatch_count=2)
        if rollback:
            assert await batcher.rollback_fence_operation(operation_id="op-1", recovery_dispatch_count=1) is True
        assert batcher._operation_generations == {"op-1": 1 if rollback else 2}
        await scheduler.drain()
        await scheduler.advance(300.0)
        assert batcher._operation_generations == {}
        assert batcher._generation_cleanup_tasks == {}
        assert batcher._operation_locks == {}
        assert batcher._contexts == {}
        assert await batcher.pending_operation_ids() == set()
    finally:
        await batcher.close()
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
@pytest.mark.parametrize("raises", [False, True])
async def test_failed_old_owner_batch_preserves_rebound_terminal(
    monkeypatch: pytest.MonkeyPatch, spool_format: str, raises: bool
) -> None:
    """Failure of an old-owner write must not discard a successor owner's terminal event."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable, max_bytes=1024, batch_size=100, flush_interval_seconds=60, spool_format=spool_format
    )
    started = asyncio.Event()
    release = asyncio.Event()
    rebound = asyncio.Event()
    method = "append_operation_events" if spool_format == "rows_v1" else "append_operation_event_chunk"
    append = getattr(durable, method)

    async def reject_old_owner(*, events, max_bytes):
        """Pause then reject the old owner's batch while permitting successor-owner writes."""
        if events[0].owner_epoch == 1:
            started.set()
            await release.wait()
            if raises:
                raise RuntimeError("old owner write failed")
            return False
        return await append(events=events, max_bytes=max_bytes)

    monkeypatch.setattr(durable, method, reject_old_owner)
    await _enqueue(batcher, "old-inflight")
    flush_task = asyncio.create_task(batcher.flush_pending_operation(operation_id="op-1"))
    terminal_task = None
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await _enqueue(batcher, "replacement-pending")
        flush = batcher.flush_pending_operation

        async def signal_rebound(*, operation_id):
            """Signal that rebinding reached the flush boundary before forwarding the operation drain."""
            rebound.set()
            return await flush(operation_id=operation_id)

        monkeypatch.setattr(batcher, "flush_pending_operation", signal_rebound)
        terminal_task = asyncio.create_task(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-2",
                instance_id="instance-2",
                owner_epoch=2,
                event_text="successor-terminal",
                max_bytes=1024,
                state="completed",
            )
        )
        await asyncio.wait_for(rebound.wait(), timeout=2)
        release.set()
        result = await asyncio.wait_for(terminal_task, timeout=2)
        await asyncio.wait_for(flush_task, timeout=2)
        assert result.persisted is True
        assert result.settlement_required is False
        batches = durable.batches if spool_format == "rows_v1" else durable.chunk_batches
        assert batches == [["replacement-pending"]]
        assert durable.terminal_kwargs[0]["owner_epoch"] == 2
        assert batcher._pending_count == 0
        assert batcher._pending_bytes == 0
    finally:
        release.set()
        await asyncio.gather(flush_task, *([terminal_task] if terminal_task else []), return_exceptions=True)
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_append_refreshes_context_for_new_recovery_generation() -> None:
    """A later recovery generation installs its owner context before terminal persistence."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "old-before-rebind")
        await batcher.fence_operation(operation_id="op-1", recovery_dispatch_count=1)

        result = await batcher.append_terminal_event(
            operation_id="op-1",
            session_id="replacement-session",
            instance_id="replacement-instance",
            owner_epoch=9,
            event_text="replacement-terminal",
            max_bytes=1024,
            state="completed",
            expected_recovery_dispatch_count=1,
            response_id="resp-replacement",
        )

        assert result.persisted is True
        assert durable.terminal_kwargs[0]["session_id"] == "replacement-session"
        assert durable.terminal_kwargs[0]["instance_id"] == "replacement-instance"
        assert durable.terminal_kwargs[0]["owner_epoch"] == 9
        assert durable.terminal_kwargs[0]["expected_recovery_dispatch_count"] == 1
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_append_refreshes_context_for_same_recovery_generation() -> None:
    """A valid same-generation owner refresh applies to terminal persistence too."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "old-before-rebind")

        result = await batcher.append_terminal_event(
            operation_id="op-1",
            session_id="replacement-session",
            instance_id="replacement-instance",
            owner_epoch=9,
            event_text="replacement-terminal",
            max_bytes=1024,
            state="completed",
            expected_recovery_dispatch_count=0,
            response_id="resp-replacement",
        )

        assert result.persisted is True
        assert durable.terminal_kwargs[0]["session_id"] == "replacement-session"
        assert durable.terminal_kwargs[0]["instance_id"] == "replacement-instance"
        assert durable.terminal_kwargs[0]["owner_epoch"] == 9
        assert durable.terminal_kwargs[0]["expected_recovery_dispatch_count"] == 0
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_append_rejects_stale_owner_for_same_generation() -> None:
    """Stale ownership cannot finalize an operation merely by matching its generation number."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        await batcher.enqueue(
            operation_id="op-1",
            session_id="successor-session",
            instance_id="successor-instance",
            owner_epoch=9,
            event_text="successor",
            recovery_dispatch_count=1,
        )
        result = await batcher.append_terminal_event(
            operation_id="op-1",
            session_id="predecessor-session",
            instance_id="predecessor-instance",
            owner_epoch=8,
            event_text="stale-terminal",
            max_bytes=1024,
            state="completed",
            expected_recovery_dispatch_count=1,
        )

        assert result.persisted is False
        assert durable.terminal_rows == []
        context = batcher._contexts["op-1"]
        assert (context.session_id, context.instance_id, context.owner_epoch) == (
            "successor-session",
            "successor-instance",
            9,
        )
    finally:
        await batcher.discard_operation(operation_id="op-1")
        await batcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_terminal_drain_rejects_same_generation_owner_handoff(
    monkeypatch: pytest.MonkeyPatch,
    spool_format: str,
) -> None:
    """Terminal draining rejects an ownership change that would mix contexts within the drain."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60,
        spool_format=spool_format,
    )
    drain_finished = asyncio.Event()
    handoff_finished = asyncio.Event()
    original_flush = batcher.flush_pending_operation

    async def paused_flush(*, operation_id: str) -> bool:
        """Pause after draining pending data so the test can perform a same-generation owner handoff."""
        result = await original_flush(operation_id=operation_id)
        drain_finished.set()
        await handoff_finished.wait()
        return result

    monkeypatch.setattr(batcher, "flush_pending_operation", paused_flush)
    await _enqueue(batcher, "old-output")
    task = asyncio.create_task(
        batcher.append_terminal_event(
            operation_id="op-1",
            session_id="session-1",
            instance_id="instance-1",
            owner_epoch=1,
            event_text="stale-terminal",
            max_bytes=1024,
            state="completed",
        )
    )
    try:
        await asyncio.wait_for(drain_finished.wait(), timeout=2)
        await batcher.enqueue(
            operation_id="op-1",
            session_id="session-2",
            instance_id="instance-2",
            owner_epoch=2,
            event_text="successor-output",
        )
        handoff_finished.set()
        result = await asyncio.wait_for(task, timeout=2)
        assert result.persisted is False
        assert result.settlement_required is False
        assert durable.terminal_rows == []
        assert durable.terminal_chunks == []
        context = batcher._contexts["op-1"]
        assert (context.session_id, context.instance_id, context.owner_epoch) == ("session-2", "instance-2", 2)
        assert await original_flush(operation_id="op-1") is True
        batches = durable.batches if spool_format == "rows_v1" else durable.chunk_batches
        assert batches == [["old-output"], ["successor-output"]]
    finally:
        handoff_finished.set()
        await asyncio.gather(task, return_exceptions=True)
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_cleanup_preserves_newer_recovery_fence() -> None:
    """Cleanup from an old terminal cannot remove a newer recovery generation's fence."""
    durable = _BlockingTerminalAppendDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        terminal_task = asyncio.create_task(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=1,
                event_text="old-terminal",
                max_bytes=1024,
                state="completed",
                expected_recovery_dispatch_count=0,
            )
        )
        await durable.terminal_started.wait()

        await batcher.fence_operation(operation_id="op-1", recovery_dispatch_count=1)
        await _enqueue(batcher, "replacement", recovery_dispatch_count=1)
        durable.release_terminal.set()

        result = await terminal_task

        assert result.persisted is True
        assert batcher._operation_generations == {"op-1": 1}
        assert batcher._contexts["op-1"].event_text == "replacement"
        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        assert durable.batches == [["replacement"]]
    finally:
        await batcher.discard_operation(operation_id="op-1")
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_cleanup_preserves_same_generation_successor_owner() -> None:
    """Old-owner cleanup leaves the same-generation successor's synchronization state intact."""
    durable = _BlockingTerminalAppendDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    try:
        terminal_task = asyncio.create_task(
            batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=1,
                event_text="old-terminal",
                max_bytes=1024,
                state="completed",
                expected_recovery_dispatch_count=0,
            )
        )
        await durable.terminal_started.wait()

        await batcher.enqueue(
            operation_id="op-1",
            session_id="session-2",
            instance_id="instance-2",
            owner_epoch=2,
            event_text="same-generation-successor",
            recovery_dispatch_count=0,
        )
        durable.release_terminal.set()
        result = await terminal_task

        assert result.persisted is True
        context = batcher._contexts["op-1"]
        assert (context.session_id, context.instance_id, context.owner_epoch) == ("session-2", "instance-2", 2)
        assert batcher._closing_operations == set()
        assert await batcher.flush_pending_operation(operation_id="op-1") is True
        assert durable.batches == [["same-generation-successor"]]
    finally:
        await batcher.discard_operation(operation_id="op-1")
        await batcher.close()


@pytest.mark.asyncio
async def test_close_cancels_background_flusher() -> None:
    """Closing the batcher cancels and joins its background flush task."""
    durable = _FakeDurableBridge()
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    await _enqueue(batcher, "one")
    task = batcher._task
    assert task is not None

    await batcher.close()

    assert batcher._task is None
    assert task.done()


@pytest.mark.asyncio
async def test_drain_terminal_finalizers_scopes_session_and_owner_epoch() -> None:
    """Closing one generation must not await another generation's finalizer."""
    batcher = HttpBridgeOperationEventBatcher(
        _FakeDurableBridge(),
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=60.0,
        max_pending_events=32,
    )
    release_matching = asyncio.Event()
    release_other_epoch = asyncio.Event()
    release_other_session = asyncio.Event()

    async def wait_for(event: asyncio.Event) -> None:
        await event.wait()

    matching = asyncio.create_task(wait_for(release_matching), name="matching-finalizer")
    other_epoch = asyncio.create_task(wait_for(release_other_epoch), name="other-epoch-finalizer")
    other_session = asyncio.create_task(wait_for(release_other_session), name="other-session-finalizer")
    for task, session_id, owner_epoch in (
        (matching, "session-a", 3),
        (other_epoch, "session-a", 4),
        (other_session, "session-b", 3),
    ):
        setattr(task, "_http_bridge_session_id", session_id)
        setattr(task, "_http_bridge_owner_epoch", owner_epoch)
    batcher._terminal_finalize_tasks.update({matching, other_epoch, other_session})

    try:
        release_matching.set()
        await batcher.drain_terminal_finalizers(session_id="session-a", owner_epoch=3)
        assert matching.done()
        assert not other_epoch.done()
        assert not other_session.done()
    finally:
        release_other_epoch.set()
        release_other_session.set()
        await asyncio.gather(matching, other_epoch, other_session, return_exceptions=True)
        await batcher.close()
