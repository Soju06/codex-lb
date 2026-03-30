from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.utils.time import utcnow
from app.db.models import Base, BridgeRingMember
from app.modules.proxy.ring_membership import RingMembershipService

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit


@pytest.fixture
async def async_session_factory() -> Callable[[], AsyncSession]:
    """Create in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_maker: sessionmaker[AsyncSession] = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def get_session():
        return async_session_maker()

    yield get_session

    await engine.dispose()


@pytest.fixture
async def ring_service(async_session_factory):
    """Create RingMembershipService with test session factory."""
    return RingMembershipService(async_session_factory)


@pytest.mark.asyncio
async def test_register_and_list_active(ring_service: RingMembershipService) -> None:
    """Register 3 pods, list_active() returns all 3 in sorted order."""
    await ring_service.register("pod-c")
    await ring_service.register("pod-a")
    await ring_service.register("pod-b")

    active = await ring_service.list_active()
    assert active == ["pod-a", "pod-b", "pod-c"]


@pytest.mark.asyncio
async def test_unregister(ring_service: RingMembershipService) -> None:
    """Register then unregister, list_active() returns empty."""
    await ring_service.register("pod-1")
    assert await ring_service.list_active() == ["pod-1"]

    await ring_service.unregister("pod-1")
    assert await ring_service.list_active() == []


@pytest.mark.asyncio
async def test_stale_heartbeat_excluded(ring_service: RingMembershipService) -> None:
    """Register pod, set last_heartbeat_at to 200s ago, list_active(120) → empty."""
    await ring_service.register("pod-stale")

    # Manually update the heartbeat to be stale
    async with ring_service._session() as session:
        from sqlalchemy import update

        stale_time = utcnow() - timedelta(seconds=200)
        stmt = (
            update(BridgeRingMember)
            .where(BridgeRingMember.instance_id == "pod-stale")
            .values(last_heartbeat_at=stale_time)
        )
        await session.execute(stmt)
        await session.commit()

    # With 120s threshold, stale pod should be excluded
    active = await ring_service.list_active(stale_threshold_seconds=120)
    assert active == []

    # With 300s threshold, stale pod should be included
    active = await ring_service.list_active(stale_threshold_seconds=300)
    assert active == ["pod-stale"]


@pytest.mark.asyncio
async def test_ring_fingerprint_deterministic(ring_service: RingMembershipService) -> None:
    """Same members → same fingerprint."""
    await ring_service.register("pod-1")
    await ring_service.register("pod-2")
    await ring_service.register("pod-3")

    fp1 = await ring_service.ring_fingerprint()
    fp2 = await ring_service.ring_fingerprint()

    assert fp1 == fp2
    assert len(fp1) == 64  # SHA256 hex digest length


@pytest.mark.asyncio
async def test_ring_fingerprint_changes_on_membership_change(ring_service: RingMembershipService) -> None:
    """Different members → different fingerprint."""
    await ring_service.register("pod-1")
    await ring_service.register("pod-2")
    fp1 = await ring_service.ring_fingerprint()

    await ring_service.register("pod-3")
    fp2 = await ring_service.ring_fingerprint()

    assert fp1 != fp2


@pytest.mark.asyncio
async def test_heartbeat_updates_timestamp(ring_service: RingMembershipService) -> None:
    """Heartbeat updates last_heartbeat_at."""
    await ring_service.register("pod-hb")

    # Get initial heartbeat
    async with ring_service._session() as session:
        from sqlalchemy import select

        result = await session.execute(select(BridgeRingMember).where(BridgeRingMember.instance_id == "pod-hb"))
        member1 = result.scalar_one()
        initial_hb = member1.last_heartbeat_at

    # Wait a tiny bit and call heartbeat
    import asyncio

    await asyncio.sleep(0.01)
    await ring_service.heartbeat("pod-hb")

    # Get updated heartbeat
    async with ring_service._session() as session:
        from sqlalchemy import select

        result = await session.execute(select(BridgeRingMember).where(BridgeRingMember.instance_id == "pod-hb"))
        member2 = result.scalar_one()
        updated_hb = member2.last_heartbeat_at

    assert updated_hb > initial_hb
