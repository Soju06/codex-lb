from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core.utils.time import utcnow
from app.db.models import ModelSourceOwnershipHistory
from app.db.session import SessionLocal
from app.modules.model_sources.ownership_repository import SourceOwnershipConflict, SourceOwnershipRepository
from tests.integration.test_source_ownership_storage import _claim

pytestmark = pytest.mark.integration


async def test_pruned_reference_keeps_credential_evidence(db_setup) -> None:
    assert await _claim("historical-reference", "original-source")
    future = utcnow() + timedelta(days=31)
    async with SessionLocal() as session:
        repository = SourceOwnershipRepository(session)
        assert await repository.prune(now=future, batch_size=10) == 1
        await session.commit()
        assert not await repository.find(["historical-reference"], now=future)
        history = await repository.find_history(["historical-reference"])
        assert [(row.source_id, row.source_revision) for row in history["historical-reference"]] == [
            ("original-source", "revision")
        ]
    assert not await _claim("historical-reference", "replacement-source")
    assert not await _claim("historical-reference", "original-source", revision="replacement-token")
    assert await _claim("historical-reference", "original-source")


async def test_conflicting_batch_does_not_leave_historical_claims(db_setup) -> None:
    assert await _claim("owned-reference", "original-source")
    async with SessionLocal() as session:
        with pytest.raises(SourceOwnershipConflict):
            await SourceOwnershipRepository(session).claim(
                ["new-reference", "owned-reference"],
                source_id="competing-source",
                source_revision="revision",
                expires_at=utcnow() + timedelta(days=30),
            )
        await session.rollback()
    async with SessionLocal() as session:
        history = await SourceOwnershipRepository(session).find_history(["new-reference", "owned-reference"])
        assert set(history) == {"owned-reference"}
        assert {row.source_id for row in history["owned-reference"]} == {"original-source"}


async def test_postgres_losing_backend_rolls_back_history_before_winner_renews(db_setup) -> None:
    async with SessionLocal() as winner:
        if winner.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL concurrent publication transaction regression")
        await SourceOwnershipRepository(winner).claim(
            ["racing-reference"],
            source_id="winning-source",
            source_revision="revision",
            expires_at=utcnow() + timedelta(days=30),
        )
        backend_pid: asyncio.Future[int] = asyncio.Future()

        async def compete() -> bool:
            async with SessionLocal() as loser:
                pid = await loser.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(pid, int)
                backend_pid.set_result(pid)
                try:
                    await SourceOwnershipRepository(loser).claim(
                        ["loser-only-reference", "racing-reference"],
                        source_id="losing-source",
                        source_revision="revision",
                        expires_at=utcnow() + timedelta(days=30),
                    )
                    await loser.commit()
                    return True
                except SourceOwnershipConflict:
                    await loser.rollback()
                    return False

        pending = asyncio.create_task(compete())
        try:
            pid = await asyncio.wait_for(backend_pid, 5)
            async with asyncio.timeout(5):
                async with SessionLocal() as observer:
                    while not await observer.scalar(text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid}):
                        await asyncio.sleep(0.01)
            await winner.commit()
            assert not await asyncio.wait_for(pending, 5)
        finally:
            await winner.rollback()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async with SessionLocal() as session:
        rows = list(await session.scalars(select(ModelSourceOwnershipHistory)))
        assert [(row.reference_key, row.source_id) for row in rows] == [("racing-reference", "winning-source")]
    assert await _claim("racing-reference", "winning-source")
