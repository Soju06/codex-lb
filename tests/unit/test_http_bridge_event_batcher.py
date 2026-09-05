from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.modules.proxy.http_bridge_event_batcher import HttpBridgeOperationEventBatcher


class _FakeDurableBridge:
    def __init__(self, *, append_result: bool = True, update_result: bool = True) -> None:
        self.append_result = append_result
        self.update_result = update_result
        self.batches: list[list[str]] = []
        self.chunk_batches: list[list[str]] = []
        self.terminal_chunks: list[str] = []
        self.terminal_append_kwargs: list[dict[str, object]] = []
        self.finalized: list[str] = []
        self.updated: list[dict[str, object]] = []

    async def append_operation_events(self, *, events, max_bytes: int) -> bool:
        del max_bytes
        self.batches.append([event.event_text for event in events])
        return self.append_result

    async def append_operation_event_chunk(self, *, events, max_bytes: int) -> bool:
        del max_bytes
        self.chunk_batches.append([event.event_text for event in events])
        return self.append_result

    async def append_terminal_operation_event(self, **kwargs) -> bool:
        self.terminal_append_kwargs.append(dict(kwargs))
        self.terminal_chunks.append(kwargs["event_text"])
        return self.append_result

    async def append_terminal_operation_chunk(self, **kwargs) -> bool:
        self.terminal_append_kwargs.append(dict(kwargs))
        self.terminal_chunks.append(kwargs["event_text"])
        return self.append_result

    async def finalize_operation_event_spool(self, **kwargs) -> bool:
        self.finalized.append(kwargs["operation_id"])
        return True

    async def update_operation(self, **kwargs) -> bool:
        self.updated.append(kwargs)
        return self.update_result

    async def settle_terminal_append_failure(self, **kwargs) -> bool:
        kwargs["event_spool_complete"] = False
        return await self.update_operation(**kwargs)


class _TerminalAppendFailingDurableBridge(_FakeDurableBridge):
    def __init__(self, *, append_result: bool = True, update_result: bool = True) -> None:
        super().__init__(append_result=append_result, update_result=update_result)
        self.update_called = asyncio.Event()

    async def append_terminal_operation_event(self, **kwargs) -> bool:
        del kwargs
        raise RuntimeError("injected terminal append failure")

    async def update_operation(self, **kwargs) -> bool:
        result = await super().update_operation(**kwargs)
        self.update_called.set()
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
) -> None:
    await batcher.enqueue(
        operation_id="op-1",
        session_id="session-1",
        instance_id="instance-1",
        owner_epoch=1,
        event_text=text,
        terminal=terminal,
    )


def test_from_settings_defaults_to_rows_and_accepts_chunk_canary() -> None:
    durable = _FakeDurableBridge()

    default_batcher = HttpBridgeOperationEventBatcher.from_settings(durable, SimpleNamespace())
    chunk_batcher = HttpBridgeOperationEventBatcher.from_settings(
        durable,
        SimpleNamespace(http_responses_session_bridge_operation_spool_format="chunks_v2"),
    )

    assert default_batcher._spool_format == "rows_v1"
    assert chunk_batcher._spool_format == "chunks_v2"


def test_constructor_rejects_unknown_spool_format() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        HttpBridgeOperationEventBatcher(
            _FakeDurableBridge(),
            max_bytes=1024,
            spool_format="unknown",
        )


@pytest.mark.asyncio
async def test_batches_without_blocking_and_finalizes_terminal_event() -> None:
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
async def test_chunk_mode_routes_batch_and_terminal_without_legacy_writes() -> None:
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
    durable = _FakeDurableBridge(append_result=False)
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        batch_size=8,
        flush_interval_seconds=0.01,
        max_pending_events=32,
    )
    try:
        await _enqueue(batcher, "one")
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
        )
        assert result.persisted is False
        assert result.settlement_required is True
        assert durable.finalized == []
        assert durable.updated == []
        assert batcher._contexts == {}
        assert batcher._dropped_operations == set()
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_terminal_append_failure_settles_operation() -> None:
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
            "expected_recovery_dispatch_count": 0,
            "alternate_expected_response_id": None,
            "response_id": "resp-1",
            "event_spool_complete": False,
        }
    ]


@pytest.mark.asyncio
async def test_terminal_append_false_requires_fallback_settlement() -> None:
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

            durable.finalize_stall.release.set()
            await asyncio.wait_for(close_task, timeout=1.0)

        assert finalize_task.done()
        assert not finalize_task.cancelled()
        assert batcher._terminal_finalize_tasks == set()
    finally:
        durable.finalize_stall.release.set()


@pytest.mark.asyncio
async def test_close_cancels_background_flusher() -> None:
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
