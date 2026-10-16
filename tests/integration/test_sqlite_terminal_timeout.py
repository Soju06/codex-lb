from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import asynccontextmanager, closing
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.scheduling import leader_election
from app.db.models import Base, HttpBridgeOperationRecord, HttpBridgeSessionRecord
from app.db.session import _configure_sqlite_engine, _sqlite_file_async_engine_kwargs, close_session
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.http_bridge_event_batcher import HttpBridgeOperationEventBatcher

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("spool_format", ["rows_v1", "chunks_v2"])
async def test_terminal_timeout_releases_writer_for_bridge_and_leader(
    tmp_path: Path, monkeypatch, spool_format
) -> None:
    """A real transcript timeout releases SQLite writes and preserves settlement fences."""
    path = tmp_path / "bridge.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", **_sqlite_file_async_engine_kwargs())
    _configure_sqlite_engine(engine.sync_engine, enable_wal=True)
    entered = threading.Event()
    release = threading.Event()
    retained_errors: list[BaseException] = []
    returning_inserts: list[str] = []

    def pause_write() -> int:
        """Keep the transcript INSERT executing until its persistence deadline expires."""
        entered.set()
        assert release.wait(5)
        return 1

    @event.listens_for(engine.sync_engine, "connect")
    def register_pause(connection, _record) -> None:
        """Register the trigger's blocking function on each SQLite connection."""
        connection.create_function("pause_write", 0, pause_write)

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record_returning(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        """Verify the regression exercises the driver's unfinished RETURNING path."""
        if statement.startswith("INSERT INTO http_bridge_operation_event") and "RETURNING" in statement:
            returning_inserts.append(statement)

    class RetainingSession(AsyncSession):
        async def commit(self) -> None:
            """Retain cancellation frames independently of the batcher's task ownership."""
            try:
                await super().commit()
            except asyncio.CancelledError as exc:
                retained_errors.append(exc)
                raise

    sessions = async_sessionmaker(engine, class_=RetainingSession, expire_on_commit=False)
    coordinator = DurableBridgeSessionCoordinator(sessions)
    batcher = HttpBridgeOperationEventBatcher(
        coordinator, max_bytes=4096, spool_format=spool_format, terminal_append_timeout_seconds=0.2
    )

    @asynccontextmanager
    async def background_session():
        """Run leader writes against the same file with independently owned sessions."""
        session = sessions()
        try:
            yield session
        finally:
            await close_session(session)

    monkeypatch.setattr(leader_election, "get_background_session", background_session)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        session.add(
            HttpBridgeSessionRecord(
                id="session",
                session_key_kind="thread_header",
                session_key_value="thread",
                session_key_hash="hash",
                api_key_scope="__anonymous__",
                owner_instance_id="instance",
                owner_epoch=1,
            )
        )
        await session.commit()
        session.add(
            HttpBridgeOperationRecord(
                operation_id="operation",
                session_id="session",
                request_fingerprint="fingerprint",
                state="completed",
                spool_format=spool_format,
            )
        )
        await session.commit()
    table = "http_bridge_operation_events" if spool_format == "rows_v1" else "http_bridge_operation_event_chunks"
    async with engine.begin() as connection:
        await connection.execute(text(f"CREATE TRIGGER stall BEFORE INSERT ON {table} BEGIN SELECT pause_write(); END"))
    append = asyncio.create_task(
        batcher.append_terminal_event(
            operation_id="operation",
            session_id="session",
            instance_id="instance",
            owner_epoch=1,
            event_text='data: {"type":"response.completed"}\n\n',
            max_bytes=4096,
            state="completed",
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        retained_tasks = tuple(batcher._terminal_append_tasks)
        result = await asyncio.wait_for(append, 2)
        assert not result.persisted
        assert result.settlement_required
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*retained_tasks, return_exceptions=True), 3)
        assert isinstance(results[0], asyncio.CancelledError)
        assert retained_errors[0].__traceback__ is not None
        assert returning_inserts
        # Fail fast here if the writer lock is stranded, rather than waiting
        # through production's 30-second busy timeout on the next bridge write.
        with closing(sqlite3.connect(path, timeout=0.2)) as independent, independent:
            independent.execute("DROP TRIGGER stall")
            assert independent.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert await coordinator.settle_terminal_append_failure(
            operation_id="operation",
            session_id="session",
            instance_id="instance",
            owner_epoch=1,
            state="completed",
            expected_response_id=None,
        )
        operation = await coordinator.get_operation(operation_id="operation")
        assert operation is not None
        assert operation.state == "completed"
        assert not operation.event_spool_complete
        # Settlement fences the cancelled attempt even if it tries to append again.
        append_method = (
            coordinator.append_terminal_operation_event
            if spool_format == "rows_v1"
            else coordinator.append_terminal_operation_chunk
        )
        assert not await append_method(
            operation_id="operation",
            session_id="session",
            instance_id="instance",
            owner_epoch=1,
            event_text="late",
            max_bytes=4096,
            state="completed",
        )
        leader = leader_election.LeaderElection("after-timeout")
        assert await leader.try_acquire()
        await leader.release()
    finally:
        release.set()
        await asyncio.gather(append, return_exceptions=True)
        await batcher.close()
        await engine.dispose()
