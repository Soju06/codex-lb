from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, HttpBridgeRetryCircuit
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository, durable_bridge_hash

pytestmark = pytest.mark.unit


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'purge.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class _BlockedSelectSession:
    def __init__(self, inner: AsyncSession) -> None:
        self._inner = inner
        self.selected = asyncio.Event()
        self.release_delete = asyncio.Event()
        self._blocked_once = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        result = await self._inner.execute(statement, *args, **kwargs)
        if getattr(statement, "is_select", False) and not self._blocked_once:
            self._blocked_once = True
            # Let the competing writer commit after this snapshot was read.
            await self._inner.rollback()
            self.selected.set()
            await self.release_delete.wait()
        return result


@pytest.mark.asyncio
async def test_batch_purge_preserves_lagging_clock_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    coordinator = DurableBridgeSessionCoordinator(session_factory)
    await coordinator.persist_retry_circuit(
        session_key_kind="session_header",
        session_key_value="lagging-clock-purge",
        api_key_id="purge-key",
        consecutive_failures=2,
        cooldown_until_epoch=1300.0,
        last_detail="stream_incomplete",
        updated_at_epoch=1200.0,
    )

    async with session_factory() as session:
        blocked = _BlockedSelectSession(session)
        repository = DurableBridgeRepository(cast(AsyncSession, blocked))
        purge_task = asyncio.create_task(repository.purge_retry_circuits_before(1201.0))
        try:
            await asyncio.wait_for(blocked.selected.wait(), timeout=2.0)
            newer = await coordinator.persist_retry_circuit(
                session_key_kind="session_header",
                session_key_value="lagging-clock-purge",
                api_key_id="purge-key",
                consecutive_failures=3,
                cooldown_until_epoch=1400.0,
                last_detail="stream_idle_timeout",
                updated_at_epoch=1100.0,
                base_updated_at_epoch=1200.0,
            )
            assert newer is not None
            assert newer.updated_at_epoch == 1200.0
            assert newer.admission_generation == 0
            assert newer.consecutive_failures == 3
            blocked.release_delete.set()
            deleted = await asyncio.wait_for(purge_task, timeout=2.0)
        finally:
            blocked.release_delete.set()
            if not purge_task.done():
                purge_task.cancel()
            await asyncio.gather(purge_task, return_exceptions=True)

    remaining = await coordinator.lookup_retry_circuit(
        session_key_kind="session_header",
        session_key_value="lagging-clock-purge",
        api_key_id="purge-key",
    )
    assert deleted == 0, f"stale purge deleted {deleted} row; newer failure after purge: {remaining!r}"
    assert remaining is not None
    assert remaining.updated_at_epoch == 1200.0
    assert remaining.admission_generation == 0
    assert remaining.consecutive_failures == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("include_tombstones", [False, True])
async def test_batch_purge_stays_within_sqlite_999_bind_limit(
    session_factory: async_sessionmaker[AsyncSession],
    include_tombstones: bool,
) -> None:
    row_count = 241
    async with session_factory() as session:
        session.add_all(
            HttpBridgeRetryCircuit(
                session_key_kind="session_header",
                session_key_hash=durable_bridge_hash(f"bind-limit-{index}"),
                api_key_scope="purge-key",
                consecutive_failures=2,
                cooldown_until_epoch=1300.0,
                last_detail="anchor_abandoned" if include_tombstones and index % 2 else "stream_incomplete",
                updated_at_epoch=900.0,
                admission_generation=0,
            )
            for index in range(row_count)
        )
        await session.commit()

        delete_bind_counts: list[int] = []

        def record_delete_bind_count(
            connection: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            if statement.lstrip().upper().startswith("DELETE FROM HTTP_BRIDGE_RETRY_CIRCUITS"):
                assert not executemany
                delete_bind_counts.append(len(parameters))

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", record_delete_bind_count)
        try:
            deleted = await DurableBridgeRepository(session).purge_retry_circuits_before(
                1000.0,
                tombstone_cutoff_epoch=1000.0 if include_tombstones else None,
            )
        finally:
            event.remove(engine, "before_cursor_execute", record_delete_bind_count)

        assert deleted == row_count
        assert len(delete_bind_counts) >= 2
        assert max(delete_bind_counts) <= 999, delete_bind_counts
        assert (await session.execute(select(HttpBridgeRetryCircuit))).first() is None


@pytest.mark.asyncio
async def test_durable_bridge_retry_circuit_batch_purge_is_timestamp_fenced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    coordinator = DurableBridgeSessionCoordinator(session_factory)
    initial_updated_at_epoch = 1200.0
    delayed_updated_at_epoch = 1200.5
    await coordinator.persist_retry_circuit(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit-batch-timestamp-race",
        api_key_id="key-batch-timestamp-race",
        consecutive_failures=2,
        cooldown_until_epoch=1300.0,
        last_detail="stream_incomplete",
        updated_at_epoch=initial_updated_at_epoch,
    )

    async with session_factory() as purge_session:
        blocked_session = _BlockedSelectSession(purge_session)
        repository = DurableBridgeRepository(cast(AsyncSession, blocked_session))
        purge_task = asyncio.create_task(
            repository.purge_retry_circuits_before(initial_updated_at_epoch + 1.0),
        )
        try:
            await asyncio.wait_for(blocked_session.selected.wait(), timeout=1.0)

            delayed = await coordinator.persist_retry_circuit(
                session_key_kind="session_header",
                session_key_value="sid-retry-circuit-batch-timestamp-race",
                api_key_id="key-batch-timestamp-race",
                consecutive_failures=3,
                cooldown_until_epoch=1400.0,
                last_detail="stream_idle_timeout",
                updated_at_epoch=delayed_updated_at_epoch,
                base_updated_at_epoch=initial_updated_at_epoch,
            )
            assert delayed is not None
            assert delayed.updated_at_epoch == delayed_updated_at_epoch
            assert delayed.admission_generation == 0

            blocked_session.release_delete.set()
            assert await asyncio.wait_for(purge_task, timeout=1.0) == 0
        finally:
            blocked_session.release_delete.set()
            if not purge_task.done():
                purge_task.cancel()
            await asyncio.gather(purge_task, return_exceptions=True)

    remaining = await coordinator.lookup_retry_circuit(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit-batch-timestamp-race",
        api_key_id="key-batch-timestamp-race",
    )
    assert remaining is not None
    assert remaining.updated_at_epoch == delayed_updated_at_epoch
    assert remaining.admission_generation == 0
    assert remaining.consecutive_failures == 3
