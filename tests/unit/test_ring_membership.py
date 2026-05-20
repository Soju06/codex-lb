from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.utils.time import utcnow
from app.db.models import Base, BridgeRingMember
from app.modules.proxy.ring_membership import (
    RING_HEARTBEAT_INTERVAL_SECONDS,
    RING_STALE_GRACE_SECONDS,
    RING_STALE_THRESHOLD_SECONDS,
    RingMembershipService,
)

pytestmark = pytest.mark.unit


@pytest.fixture
async def async_session_factory() -> AsyncIterator[Callable[[], AsyncSession]]:
    """Create in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

    def get_session() -> AsyncSession:
        return async_session_maker()

    yield get_session

    await engine.dispose()


@pytest.fixture
async def ring_service(async_session_factory: Callable[[], AsyncSession]) -> RingMembershipService:
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
async def test_list_active_can_require_advertised_endpoint(ring_service: RingMembershipService) -> None:
    await ring_service.register("pod-a", endpoint_base_url="http://10.0.0.12:8080")
    await ring_service.register("pod-b", endpoint_base_url=None)

    active = await ring_service.list_active(require_endpoint=True)

    assert active == ["pod-a"]


@pytest.mark.asyncio
async def test_register_replaces_prior_owner_for_same_endpoint(ring_service: RingMembershipService) -> None:
    await ring_service.register("pod-old", endpoint_base_url="http://127.0.0.1:3455")

    await ring_service.register("pod-new", endpoint_base_url="http://127.0.0.1:3455")

    assert await ring_service.list_active(require_endpoint=True) == ["pod-new"]
    assert await ring_service.resolve_endpoint("pod-old") is None
    assert await ring_service.resolve_endpoint("pod-new") == "http://127.0.0.1:3455"


@pytest.mark.asyncio
async def test_heartbeat_retries_sqlite_locked_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.sqlite_retry.asyncio.sleep", _no_sleep)
    state = _LockedSessionState()

    def session_factory() -> _LockedOnceAsyncSession:
        return _LockedOnceAsyncSession(state)

    service = RingMembershipService(session_factory)  # type: ignore[arg-type]

    await service.heartbeat("pod-new")

    assert state.execute_calls == 2
    assert state.rollback_calls == 1
    assert state.commit_calls == 1


@pytest.mark.asyncio
async def test_heartbeat_replaces_prior_owner_for_same_endpoint(ring_service: RingMembershipService) -> None:
    await ring_service.register("pod-old", endpoint_base_url="http://127.0.0.1:3455")

    await ring_service.heartbeat("pod-new", endpoint_base_url="http://127.0.0.1:3455")

    assert await ring_service.list_active(require_endpoint=True) == ["pod-new"]


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
async def test_mark_stale_keeps_member_visible_until_grace_window_elapses(
    ring_service: RingMembershipService,
) -> None:
    await ring_service.register("pod-grace")

    await ring_service.mark_stale(
        "pod-grace",
        stale_threshold_seconds=RING_STALE_THRESHOLD_SECONDS,
        grace_seconds=RING_STALE_GRACE_SECONDS,
    )

    assert await ring_service.list_active(stale_threshold_seconds=RING_STALE_THRESHOLD_SECONDS) == ["pod-grace"]
    assert await ring_service.list_active(stale_threshold_seconds=RING_HEARTBEAT_INTERVAL_SECONDS) == []


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


@pytest.mark.asyncio
async def test_resolve_endpoint_returns_advertised_base_url(ring_service: RingMembershipService) -> None:
    await ring_service.register("pod-endpoint", endpoint_base_url="http://10.0.0.12:8080")

    endpoint = await ring_service.resolve_endpoint("pod-endpoint")

    assert endpoint == "http://10.0.0.12:8080"


@pytest.mark.asyncio
async def test_resolve_endpoint_ignores_stale_member_metadata(ring_service: RingMembershipService) -> None:
    await ring_service.register("pod-stale-endpoint", endpoint_base_url="http://10.0.0.14:8080")
    await ring_service.mark_stale(
        "pod-stale-endpoint",
        stale_threshold_seconds=RING_STALE_THRESHOLD_SECONDS,
        grace_seconds=0,
    )

    endpoint = await ring_service.resolve_endpoint("pod-stale-endpoint", stale_threshold_seconds=1)

    assert endpoint is None


@pytest.mark.asyncio
async def test_mark_stale_preserves_endpoint_within_grace_window(ring_service: RingMembershipService) -> None:
    await ring_service.register("pod-grace-endpoint", endpoint_base_url="http://10.0.0.15:8080")
    await ring_service.mark_stale(
        "pod-grace-endpoint",
        stale_threshold_seconds=RING_STALE_THRESHOLD_SECONDS,
        grace_seconds=RING_STALE_GRACE_SECONDS,
    )

    endpoint = await ring_service.resolve_endpoint("pod-grace-endpoint")

    assert endpoint == "http://10.0.0.15:8080"


class _LockedSessionState:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.rollback_calls = 0
        self.commit_calls = 0
        self.close_calls = 0


class _LockedOnceAsyncSession:
    def __init__(self, state: _LockedSessionState) -> None:
        self._state = state

    def get_bind(self) -> Any:
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    async def execute(self, _: object) -> None:
        self._state.execute_calls += 1
        if self._state.execute_calls == 1:
            raise OperationalError("upsert bridge ring member", {}, Exception("database is locked"))

    async def commit(self) -> None:
        self._state.commit_calls += 1

    async def rollback(self) -> None:
        self._state.rollback_calls += 1

    async def close(self) -> None:
        self._state.close_calls += 1


async def _no_sleep(_: float) -> None:
    return None
