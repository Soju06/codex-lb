from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core.retention.job import prune_source_ownership
from app.core.utils.time import utcnow
from app.db.models import ModelSourceOwnership
from app.db.session import SessionLocal, sqlite_writer_section
from app.modules.model_sources.ownership_repository import SourceOwnershipConflict, SourceOwnershipRepository
from app.modules.proxy.source_ownership import OwnershipScope

pytestmark = pytest.mark.integration


async def _claim(key: str, source: str, *, revision: str = "revision") -> bool:
    async with SessionLocal() as session:
        try:
            async with sqlite_writer_section():
                await SourceOwnershipRepository(session).claim(
                    [key], source_id=source, source_revision=revision, expires_at=utcnow() + timedelta(days=30)
                )
                await session.commit()
            return True
        except SourceOwnershipConflict:
            await session.rollback()
            return False


async def test_independent_backend_sessions_cannot_claim_the_same_state(db_setup):
    key = OwnershipScope("client", "model").key("encrypted", "opaque-state")
    claims = await asyncio.gather(_claim(key, "source-a"), _claim(key, "source-b"))
    assert sorted(claims) == [False, True]
    winner = "source-a" if claims[0] else "source-b"
    async with SessionLocal() as session:
        rows = await SourceOwnershipRepository(session).find([key], now=utcnow())
        assert rows[key].source_id == winner
    assert await _claim(key, winner)
    assert not await _claim(key, winner, revision="replacement-credential")


async def test_conflicting_batch_rolls_back_new_references(db_setup):
    assert await _claim("existing", "source-a")
    async with SessionLocal() as session:
        with pytest.raises(SourceOwnershipConflict):
            async with sqlite_writer_section():
                await SourceOwnershipRepository(session).claim(
                    ["new", "existing"],
                    source_id="source-b",
                    source_revision="revision",
                    expires_at=utcnow() + timedelta(days=30),
                )
        await session.rollback()
    async with SessionLocal() as session:
        assert await session.get(ModelSourceOwnership, "new") is None
        existing = await session.get(ModelSourceOwnership, "existing")
        assert existing is not None
        assert existing.source_id == "source-a"


async def test_scopes_are_isolated_and_only_fingerprints_are_stored(db_setup):
    scopes = [
        OwnershipScope("key-a", "model-a"),
        OwnershipScope("key-b", "model-a"),
        OwnershipScope("key-a", "model-b"),
    ]
    keys = [scope.key("encrypted", "opaque-state") for scope in scopes]
    assert len(set(keys)) == 3
    assert scopes[0].key("item", "opaque-state") not in keys
    for index, key in enumerate(keys):
        assert await _claim(key, f"source-{index}")
    async with SessionLocal() as session:
        rows = list(await session.scalars(select(ModelSourceOwnership)))
        assert all(len(row.reference_key) == 64 and "opaque-state" not in row.reference_key for row in rows)


async def test_expired_ownership_is_not_used_and_is_pruned_in_batches(db_setup, monkeypatch):
    from app.core.retention import job

    now = utcnow()
    async with SessionLocal() as session:
        for index in range(5):
            session.add(
                ModelSourceOwnership(
                    reference_key=f"expired-{index}", source_id="source", source_revision="rev", expires_at=now
                )
            )
        session.add(
            ModelSourceOwnership(
                reference_key="live", source_id="source", source_revision="rev", expires_at=now + timedelta(days=1)
            )
        )
        await session.commit()
        assert not await SourceOwnershipRepository(session).find(["expired-0"], now=now)
    monkeypatch.setattr(job, "BATCH_SIZE", 2)
    assert await prune_source_ownership(now=now) == 5
    async with SessionLocal() as session:
        assert list(await session.scalars(select(ModelSourceOwnership.reference_key))) == ["live"]


async def test_postgres_pruning_preserves_a_concurrent_backend_refresh(db_setup):
    now = utcnow()
    async with SessionLocal() as setup:
        if setup.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL READ COMMITTED row-lock regression")
        setup.add(
            ModelSourceOwnership(reference_key="refreshing", source_id="source", source_revision="rev", expires_at=now)
        )
        await setup.commit()

    async with SessionLocal() as refresher:
        await SourceOwnershipRepository(refresher).claim(
            ["refreshing"], source_id="source", source_revision="rev", expires_at=now + timedelta(days=30)
        )
        backend_pid = asyncio.Future()

        async def prune_on_another_backend():
            async with SessionLocal() as pruner:
                backend_pid.set_result(await pruner.scalar(text("SELECT pg_backend_pid()")))
                count = await SourceOwnershipRepository(pruner).prune(now=now, batch_size=10)
                await pruner.commit()
                return count

        pending = asyncio.create_task(prune_on_another_backend())
        try:
            pid = await asyncio.wait_for(backend_pid, 5)
            async with asyncio.timeout(5):
                async with SessionLocal() as observer:
                    while not await observer.scalar(text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid}):
                        await asyncio.sleep(0.01)
            # The DELETE saw the expired row, but is waiting for this renewal.
            await refresher.commit()
            assert await asyncio.wait_for(pending, 5) == 0
        finally:
            await refresher.rollback()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async with SessionLocal() as session:
        records = await SourceOwnershipRepository(session).find(["refreshing"], now=now)
        assert records["refreshing"].expires_at == now + timedelta(days=30)
