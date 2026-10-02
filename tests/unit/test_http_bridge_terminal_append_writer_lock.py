"""The terminal-append bound must not leak the SQLite writer slot (issue #1981).

The batcher waits ``terminal_append_timeout_seconds`` for the terminal
transcript append. The append runs against a real aiosqlite connection on a
temp SQLite file, inside ``sqlite_writer_section()``, and holds a write
transaction across a statement that outlives the bound. Cancelling it there
makes SQLAlchemy invalidate the connection mid-statement and can leave the
aiosqlite handle with its write transaction open, after which no other writer
can take the slot.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import close_session, sqlite_writer_section
from app.modules.proxy.http_bridge_event_batcher import HttpBridgeOperationEventBatcher

_BOUND_SECONDS = 0.5


class _WriterHoldingDurableBridge:
    """Terminal append that holds the writer slot across a blocked statement.

    Mirrors ``DurableBridgeSessionCoordinator``: one session per call, closed
    through ``close_session``, with the repository write under
    ``sqlite_writer_section()``. ``hold_writer()`` runs on the aiosqlite worker
    thread and blocks until the test releases it, so the statement is still
    in flight, with the transaction's INSERT already applied, when the batcher's
    bound expires.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self.raw_connections: list[sqlite3.Connection] = []
        self.statement_started = threading.Event()
        self.release_statement = threading.Event()
        event.listen(engine.sync_engine, "connect", self._on_connect)

    def _hold_writer(self) -> int:
        self.statement_started.set()
        self.release_statement.wait(timeout=10.0)
        return 1

    def _on_connect(self, dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.create_function("hold_writer", 0, self._hold_writer)
        self.raw_connections.append(dbapi_connection.driver_connection._conn)

    async def append_terminal_operation_event(self, **kwargs: Any) -> bool:
        session = self._session_factory()
        try:
            async with sqlite_writer_section():
                await session.execute(
                    text("INSERT INTO terminal_events (operation_id) VALUES (:operation_id)"),
                    {"operation_id": kwargs["operation_id"]},
                )
                await session.execute(text("SELECT hold_writer()"))
                await session.commit()
        finally:
            await close_session(session)
        return True

    async def finalize_operation_event_spool(self, **kwargs: Any) -> bool:
        del kwargs
        return True


def _begin_immediate(path: Path, busy_timeout_seconds: float) -> None:
    probe = sqlite3.connect(path, timeout=busy_timeout_seconds, isolation_level=None)
    try:
        probe.execute("BEGIN IMMEDIATE")
        probe.execute("ROLLBACK")
    finally:
        probe.close()


@pytest.fixture
async def sqlite_engine(tmp_path: Path):
    path = tmp_path / "writer-lock.db"
    setup = sqlite3.connect(path)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute("CREATE TABLE terminal_events (operation_id TEXT NOT NULL)")
    setup.commit()
    setup.close()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    yield engine, path
    await engine.dispose()


def _append_terminal_event(batcher: HttpBridgeOperationEventBatcher):
    return batcher.append_terminal_event(
        operation_id="op-1",
        session_id="session-1",
        instance_id="instance-1",
        owner_epoch=7,
        event_text="terminal",
        max_bytes=1024,
        state="completed",
        response_id="resp-1",
    )


def _in_transaction(connection: sqlite3.Connection) -> bool:
    try:
        return connection.in_transaction
    except sqlite3.ProgrammingError:
        # Closed from Python's side. If sqlite3_close_v2 deferred the close,
        # the handle can still hold the writer slot; the probe covers that.
        return False


async def _assert_append_releases_writer_slot(
    durable: _WriterHoldingDurableBridge,
    append_tasks: tuple[asyncio.Task[Any], ...],
    path: Path,
) -> None:
    # The append is still mid-statement. Let it run to its end.
    assert len(append_tasks) == 1
    durable.release_statement.set()
    await asyncio.wait(append_tasks, timeout=5.0)
    assert append_tasks[0].done()

    # Another connection can take the writer slot, and no connection the
    # append opened is still inside a transaction.
    await asyncio.to_thread(_begin_immediate, path, 2.0)
    assert durable.raw_connections
    assert not any(_in_transaction(connection) for connection in durable.raw_connections)
    # The append was left running at the bound and committed.
    assert not append_tasks[0].cancelled()
    assert append_tasks[0].result().persisted is True


@pytest.mark.asyncio
async def test_terminal_append_bound_does_not_leak_sqlite_writer_slot(sqlite_engine) -> None:
    engine, path = sqlite_engine
    durable = _WriterHoldingDurableBridge(engine)
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=_BOUND_SECONDS,
    )
    try:
        started = time.monotonic()
        result = await asyncio.wait_for(_append_terminal_event(batcher), timeout=_BOUND_SECONDS + 2.0)
        elapsed = time.monotonic() - started

        # The caller keeps its bound and its settlement result.
        assert durable.statement_started.is_set()
        assert result.persisted is False
        assert result.settlement_required is True
        assert elapsed < _BOUND_SECONDS + 1.0

        await _assert_append_releases_writer_slot(durable, tuple(batcher._terminal_append_tasks), path)
    finally:
        durable.release_statement.set()
        await batcher.close()


@pytest.mark.asyncio
async def test_cancelled_terminal_append_caller_does_not_leak_sqlite_writer_slot(sqlite_engine) -> None:
    engine, path = sqlite_engine
    durable = _WriterHoldingDurableBridge(engine)
    batcher = HttpBridgeOperationEventBatcher(
        durable,
        max_bytes=1024,
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=60.0,
    )
    try:
        caller = asyncio.create_task(_append_terminal_event(batcher))
        assert await asyncio.to_thread(durable.statement_started.wait, 5.0)
        append_tasks = tuple(batcher._terminal_append_tasks)

        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(caller, timeout=1.0)

        await _assert_append_releases_writer_slot(durable, append_tasks, path)
    finally:
        durable.release_statement.set()
        await batcher.close()
