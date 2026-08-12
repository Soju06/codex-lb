from __future__ import annotations

import asyncio

import pytest

from app.modules.proxy.http_bridge_event_batcher import HttpBridgeOperationEventBatcher


class _FakeDurableBridge:
    def __init__(self, *, append_result: bool = True) -> None:
        self.append_result = append_result
        self.batches: list[list[str]] = []
        self.finalized: list[str] = []
        self.updated: list[dict[str, object]] = []

    async def append_operation_events(self, *, events, max_bytes: int) -> bool:
        del max_bytes
        self.batches.append([event.event_text for event in events])
        return self.append_result

    async def finalize_operation_event_spool(self, **kwargs) -> bool:
        self.finalized.append(kwargs["operation_id"])
        return True

    async def update_operation(self, **kwargs) -> bool:
        self.updated.append(kwargs)
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
async def test_dropped_batch_is_never_marked_replayable() -> None:
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
        assert (
            await batcher.append_terminal_event(
                operation_id="op-1",
                session_id="session-1",
                instance_id="instance-1",
                owner_epoch=1,
                event_text="terminal",
                max_bytes=1024,
                state="failed",
            )
            is False
        )
        assert durable.finalized == []
        assert durable.updated[0]["state"] == "failed"
        assert batcher._contexts == {}
        assert batcher._dropped_operations == set()
    finally:
        await batcher.close()


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
