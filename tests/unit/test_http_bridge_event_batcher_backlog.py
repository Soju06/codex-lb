from __future__ import annotations

import asyncio

import pytest

from app.modules.proxy.durable_bridge_repository import DurableBridgeOperationEventInput
from app.modules.proxy.http_bridge_event_batcher import HttpBridgeOperationEventBatcher


class _RecordingWriter:
    def __init__(self, *, expected_batches: int) -> None:
        self.expected_batches = expected_batches
        self.batches: list[tuple[str, list[str]]] = []
        self.drained = asyncio.Event()
        self.finalized: list[str] = []

    async def append_operation_events(self, *, events: list[DurableBridgeOperationEventInput], max_bytes: int) -> bool:
        del max_bytes
        self.batches.append((events[0].operation_id, [event.event_text for event in events]))
        if len(self.batches) == self.expected_batches:
            self.drained.set()
        return True

    async def append_operation_event_chunk(
        self, *, events: list[DurableBridgeOperationEventInput], max_bytes: int
    ) -> bool:
        return await self.append_operation_events(events=events, max_bytes=max_bytes)

    async def finalize_operation_event_spool(
        self, *, operation_id: str, session_id: str, instance_id: str, owner_epoch: int
    ) -> bool:
        del session_id, instance_id, owner_epoch
        self.finalized.append(operation_id)
        return True


class _BlockedWriter(_RecordingWriter):
    def __init__(self, *, block_batch: int) -> None:
        super().__init__(expected_batches=3)
        self.block_batch = block_batch
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exited = asyncio.Event()

    async def append_operation_events(self, *, events: list[DurableBridgeOperationEventInput], max_bytes: int) -> bool:
        if len(self.batches) + 1 == self.block_batch:
            self.entered.set()
            try:
                await self.release.wait()
            finally:
                self.exited.set()
        return await super().append_operation_events(events=events, max_bytes=max_bytes)


async def _enqueue(batcher: HttpBridgeOperationEventBatcher, operation_id: str, text: str) -> None:
    await batcher.enqueue(
        operation_id=operation_id,
        session_id="session",
        instance_id="instance",
        owner_epoch=1,
        event_text=text,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_background_drains_quiet_backlog_in_ordered_bounded_fair_passes(spool_format: str) -> None:
    writer = _RecordingWriter(expected_batches=6)
    batcher = HttpBridgeOperationEventBatcher(
        writer, max_bytes=1024, batch_size=2, flush_interval_seconds=60, spool_format=spool_format
    )
    try:
        for operation_id in ("a", "b"):
            for text in ("one", "two", "three", "four", "five"):
                await _enqueue(batcher, operation_id, text)

        await asyncio.wait_for(writer.drained.wait(), timeout=1)

        assert writer.batches == [
            ("a", ["one", "two"]),
            ("b", ["one", "two"]),
            ("a", ["three", "four"]),
            ("b", ["three", "four"]),
            ("a", ["five"]),
            ("b", ["five"]),
        ]
    finally:
        await batcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_close_cancels_writer_blocked_during_rearmed_pass(spool_format: str) -> None:
    writer = _BlockedWriter(block_batch=2)
    batcher = HttpBridgeOperationEventBatcher(
        writer, max_bytes=1024, batch_size=2, flush_interval_seconds=60, spool_format=spool_format
    )
    try:
        for text in ("one", "two", "three", "four", "five"):
            await _enqueue(batcher, "a", text)
        await asyncio.wait_for(writer.entered.wait(), timeout=1)

        await asyncio.wait_for(batcher.close(), timeout=1)

        assert writer.exited.is_set()
        assert writer.batches == [("a", ["one", "two"])]
        assert writer.finalized == []
    finally:
        await batcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_terminal_drain_finishes_in_order_after_blocked_background_write(spool_format: str) -> None:
    writer = _BlockedWriter(block_batch=2)
    batcher = HttpBridgeOperationEventBatcher(
        writer, max_bytes=1024, batch_size=2, flush_interval_seconds=60, spool_format=spool_format
    )
    terminal: asyncio.Task[None] | None = None
    try:
        for text in ("one", "two", "three", "four", "five"):
            await _enqueue(batcher, "a", text)
        await asyncio.wait_for(writer.entered.wait(), timeout=1)
        terminal = asyncio.create_task(
            batcher.enqueue(
                operation_id="a",
                session_id="session",
                instance_id="instance",
                owner_epoch=1,
                event_text="terminal",
                terminal=True,
            )
        )
        await asyncio.sleep(0)
        assert not terminal.done()
        assert writer.finalized == []

        writer.release.set()
        await asyncio.wait_for(terminal, timeout=1)

        assert writer.batches == [
            ("a", ["one", "two"]),
            ("a", ["three", "four"]),
            ("a", ["five", "terminal"]),
        ]
        assert writer.finalized == ["a"]
    finally:
        if terminal is not None:
            terminal.cancel()
            await asyncio.gather(terminal, return_exceptions=True)
        await batcher.close()


class _FailingWriter(_RecordingWriter):
    def __init__(self, *, raise_error: bool) -> None:
        super().__init__(expected_batches=3)
        self.raise_error = raise_error
        self.failed_attempts = 0

    async def append_operation_events(self, *, events: list[DurableBridgeOperationEventInput], max_bytes: int) -> bool:
        if events[0].operation_id == "a":
            self.failed_attempts += 1
            if self.raise_error:
                raise RuntimeError("injected persistence failure")
            return False
        return await super().append_operation_events(events=events, max_bytes=max_bytes)


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
@pytest.mark.parametrize("raise_error", [False, True])
async def test_failed_operation_does_not_stall_sibling_backlog_or_become_replayable(
    spool_format: str, raise_error: bool
) -> None:
    writer = _FailingWriter(raise_error=raise_error)
    batcher = HttpBridgeOperationEventBatcher(
        writer, max_bytes=1024, batch_size=2, flush_interval_seconds=60, spool_format=spool_format
    )
    try:
        for operation_id in ("a", "b"):
            for text in ("one", "two", "three", "four", "five"):
                await _enqueue(batcher, operation_id, text)
        await asyncio.wait_for(writer.drained.wait(), timeout=1)

        terminal = await batcher.append_terminal_event(
            operation_id="a",
            session_id="session",
            instance_id="instance",
            owner_epoch=1,
            event_text="terminal",
            max_bytes=1024,
            state="completed",
        )

        assert writer.failed_attempts == 1
        assert writer.batches == [
            ("b", ["one", "two"]),
            ("b", ["three", "four"]),
            ("b", ["five"]),
        ]
        assert not terminal.persisted
        assert terminal.settlement_required
        assert writer.finalized == []
    finally:
        await batcher.close()
