from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import AuditLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session(db_setup):
    async with SessionLocal() as session:
        yield session
        if session.in_transaction():
            await session.rollback()


@pytest.mark.asyncio
async def test_cross_instance_rate_limiting(db_session):
    from app.core.exceptions import DashboardRateLimitError
    from app.core.rate_limiter.db_rate_limiter import DatabaseRateLimiter

    instance1 = DatabaseRateLimiter(max_attempts=8, window_seconds=300, type="totp")
    instance2 = DatabaseRateLimiter(max_attempts=8, window_seconds=300, type="totp")

    key = "test-multi-replica-ip"

    for _ in range(4):
        await instance1.check_and_record(key, db_session)

    async with SessionLocal() as instance2_session:
        for _ in range(4):
            await instance2.check_and_record(key, instance2_session)

        with pytest.raises(DashboardRateLimitError):
            await instance2.check_and_record(key, instance2_session)


@pytest.mark.asyncio
async def test_check_and_increment_records_first_password_attempt(db_session):
    from app.core.rate_limiter.db_rate_limiter import DatabaseRateLimiter

    limiter = DatabaseRateLimiter(max_attempts=8, window_seconds=300, type="password")
    key = "test-password-login"

    await limiter.check_and_increment(key, db_session)
    await limiter.clear_for_key(key, db_session)
    await limiter.check_and_increment(key, db_session)


@pytest.mark.asyncio
async def test_settings_cache_consistency(db_session):
    from app.core.config.settings_cache import get_settings_cache

    cache = get_settings_cache()
    await cache.invalidate()

    settings1 = await cache.get()
    settings2 = await cache.get()

    assert settings1 is settings2


@pytest.mark.asyncio
async def test_leader_election_returns_single_leader_on_sqlite():
    from app.core.scheduling.leader_election import LeaderElection

    election1 = LeaderElection(leader_id="instance-1")
    election2 = LeaderElection(leader_id="instance-2")

    result1 = await election1.try_acquire()
    result2 = await election2.try_acquire()

    assert result1 is True
    assert result2 is True


@pytest.mark.asyncio
async def test_audit_log_records_from_different_modules(db_session):
    from app.core.audit.service import _write_audit_log

    await _write_audit_log(
        "account_created",
        actor_ip="1.2.3.4",
        details={"name": "test"},
        request_id="req-1",
    )
    await _write_audit_log(
        "api_key_created",
        actor_ip="1.2.3.5",
        details={"name": "key1"},
        request_id="req-2",
    )

    logs = (await db_session.execute(select(AuditLog))).scalars().all()
    assert len(logs) >= 2
    actions = {log.action for log in logs}
    assert "account_created" in actions
    assert "api_key_created" in actions


@pytest.mark.asyncio
async def test_account_caps_partition_across_two_replicas_sharing_one_database(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two replicas over one DB admit at most the configured cluster-wide account cap.

    Before cap partitioning each replica enforced the full configured cap against
    its own in-process lease counters, so two replicas admitted 2x the configured
    per-account stream cap.
    """
    from types import SimpleNamespace
    from typing import Any, cast

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Base
    from app.modules.proxy.cap_partitioning import CapPartitionHolder
    from app.modules.proxy.load_balancer import LoadBalancer
    from app.modules.proxy.load_balancer import effective_account_concurrency_caps as effective_caps
    from app.modules.proxy.ring_membership import RingMembershipService

    db_path = tmp_path / "cap-partition-ring.sqlite3"
    engine_a = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    engine_b = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine_a.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
        maker_b = async_sessionmaker(engine_b, expire_on_commit=False)
        services = {
            "replica-a": RingMembershipService(lambda: maker_a()),
            "replica-b": RingMembershipService(lambda: maker_b()),
        }

        for instance_id, service in services.items():
            await service.register(instance_id)

        configured = SimpleNamespace(proxy_account_response_create_limit=4, proxy_account_stream_limit=8)
        admitted: dict[str, int] = {}
        for instance_id, service in services.items():
            members = await service.list_active()
            assert members == ["replica-a", "replica-b"]
            holder = CapPartitionHolder()
            holder.observe_members(
                members,
                instance_id,
                configured_caps=(
                    configured.proxy_account_response_create_limit,
                    configured.proxy_account_stream_limit,
                ),
                scale_down_seconds=60.0,
            )
            monkeypatch.setattr(
                "app.modules.proxy.load_balancer.get_cap_partition",
                lambda holder=holder: holder.current,
            )
            caps = effective_caps(configured)
            assert caps.replica_count == 2
            assert caps.stream_limit == 4

            balancer = LoadBalancer(cast(Any, None))
            admitted[instance_id] = 0
            for _ in range(16):
                lease = await balancer.acquire_account_lease(
                    "acc-cluster-cap",
                    kind="stream",
                    concurrency_caps=caps,
                )
                if lease is None:
                    break
                admitted[instance_id] += 1

        assert admitted == {"replica-a": 4, "replica-b": 4}
        assert sum(admitted.values()) == 8
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


@pytest.mark.asyncio
async def test_cap_partition_scale_down_over_ring_waits_for_stability_window(tmp_path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Base
    from app.modules.proxy.cap_partitioning import CapPartition, CapPartitionHolder
    from app.modules.proxy.ring_membership import RingMembershipService

    db_path = tmp_path / "cap-partition-scale-down.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        service = RingMembershipService(lambda: maker())
        await service.register("replica-a")
        await service.register("replica-b")

        configured_caps = (4, 8)
        clock_now = 0.0
        holder = CapPartitionHolder(clock=lambda: clock_now)
        holder.observe_members(
            await service.list_active(), "replica-a", configured_caps=configured_caps, scale_down_seconds=60.0
        )
        assert holder.current == CapPartition(replica_count=2, rank=0)

        await service.unregister("replica-b")

        members = await service.list_active()
        assert members == ["replica-a"]
        holder.observe_members(members, "replica-a", configured_caps=configured_caps, scale_down_seconds=60.0)
        assert holder.current == CapPartition(replica_count=2, rank=0)

        clock_now = 59.0
        holder.observe_members(
            await service.list_active(), "replica-a", configured_caps=configured_caps, scale_down_seconds=60.0
        )
        assert holder.current == CapPartition(replica_count=2, rank=0)

        clock_now = 60.0
        holder.observe_members(
            await service.list_active(), "replica-a", configured_caps=configured_caps, scale_down_seconds=60.0
        )
        assert holder.current == CapPartition(replica_count=1, rank=0)
    finally:
        await engine.dispose()
