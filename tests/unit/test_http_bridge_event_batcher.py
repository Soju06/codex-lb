from __future__ import annotations

import asyncio
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
        self.terminal_chunks.append(kwargs["event_text"])
        return self.append_result

    async def append_terminal_operation_chunk(self, **kwargs) -> bool:
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
        assert durable.batches == []
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
