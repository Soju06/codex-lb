from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app.core.scheduling import leader_election as leader_election_module
from app.db.models import AuditLog, SchedulerLeader
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session(db_setup):
    async with SessionLocal() as session:
        yield session
        if session.in_transaction():
            await session.rollback()


def _enable_leader_election(monkeypatch: pytest.MonkeyPatch, *, ttl_seconds: float = 60) -> None:
    settings = SimpleNamespace(
        leader_election_enabled=True,
        leader_election_ttl_seconds=ttl_seconds,
    )
    monkeypatch.setattr(leader_election_module, "get_settings", lambda: settings)


async def _steal_lease(new_leader_id: str) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(SchedulerLeader)
            .where(SchedulerLeader.id == 1)
            .values(
                leader_id=new_leader_id,
                expires_at=datetime.now(UTC) + timedelta(seconds=120),
            )
        )
        await session.commit()


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
async def test_leader_election_elects_single_leader(db_setup, monkeypatch: pytest.MonkeyPatch):
    """Two replicas over one database: exactly one wins the lease.

    Regression: the old implementation short-circuited to True for every
    process on SQLite, so both instances became leader.
    """
    from app.core.scheduling.leader_election import LeaderElection

    _enable_leader_election(monkeypatch)

    election1 = LeaderElection(leader_id="instance-1")
    election2 = LeaderElection(leader_id="instance-2")

    result1, result2 = await asyncio.gather(election1.try_acquire(), election2.try_acquire())

    assert (result1, result2).count(True) == 1
    assert (result1, result2).count(False) == 1


@pytest.mark.asyncio
async def test_leader_election_holder_reacquires_and_follower_stays_out(db_setup, monkeypatch: pytest.MonkeyPatch):
    from app.core.scheduling.leader_election import LeaderElection

    _enable_leader_election(monkeypatch)

    election1 = LeaderElection(leader_id="instance-1")
    election2 = LeaderElection(leader_id="instance-2")

    assert await election1.try_acquire() is True
    assert await election2.try_acquire() is False
    assert await election1.try_acquire() is True
    assert await election2.try_acquire() is False


@pytest.mark.asyncio
async def test_leader_election_follower_takes_over_after_release(db_setup, monkeypatch: pytest.MonkeyPatch):
    """Regression: the old implementation never released the lease, so a
    follower had to wait out the full TTL after a graceful shutdown."""
    from app.core.scheduling.leader_election import LeaderElection

    _enable_leader_election(monkeypatch)

    election1 = LeaderElection(leader_id="instance-1")
    election2 = LeaderElection(leader_id="instance-2")

    assert await election1.try_acquire() is True
    assert await election2.try_acquire() is False

    await election1.release()

    assert election1.is_leader is False
    assert await election2.try_acquire() is True


@pytest.mark.asyncio
async def test_leader_election_follower_takes_over_after_expiry(db_setup, monkeypatch: pytest.MonkeyPatch):
    from app.core.scheduling.leader_election import LeaderElection

    _enable_leader_election(monkeypatch, ttl_seconds=0.2)

    election1 = LeaderElection(leader_id="instance-1")
    election2 = LeaderElection(leader_id="instance-2")

    assert await election1.try_acquire() is True
    assert await election2.try_acquire() is False

    await asyncio.sleep(0.3)

    assert await election2.try_acquire() is True


@pytest.mark.asyncio
async def test_leader_election_renew_demotes_when_lease_stolen(db_setup, monkeypatch: pytest.MonkeyPatch):
    """Regression: the old renew() ignored the UPDATE rowcount and reported
    success even after the lease had been taken over."""
    from app.core.scheduling.leader_election import LeaderElection

    _enable_leader_election(monkeypatch)

    election1 = LeaderElection(leader_id="instance-1")
    assert await election1.try_acquire() is True
    assert await election1.renew() is True

    await _steal_lease("thief")

    assert await election1.renew() is False
    assert election1.is_leader is False


@pytest.mark.asyncio
async def test_run_if_leader_heartbeat_keeps_follower_out_during_long_body(db_setup, monkeypatch: pytest.MonkeyPatch):
    """Regression: the old implementation never renewed during a task, so a
    body outliving the TTL let a second replica become a concurrent leader."""
    from app.core.scheduling.leader_election import LeaderElection

    _enable_leader_election(monkeypatch, ttl_seconds=3)

    election1 = LeaderElection(leader_id="instance-1")
    election2 = LeaderElection(leader_id="instance-2")
    body_started = asyncio.Event()
    body_done = asyncio.Event()

    async def _long_body() -> str:
        body_started.set()
        # Outlives the 3s TTL; the heartbeat (interval 1s) must keep the
        # lease alive the whole time.
        await asyncio.sleep(3.6)
        body_done.set()
        return "finished"

    leader_task = asyncio.create_task(election1.run_if_leader(_long_body))
    # Poll only once the leader holds the lease: polling concurrently with
    # the initial acquire lets the follower win that race, after which the
    # body never runs and the old poll loop spun forever.
    await asyncio.wait_for(body_started.wait(), timeout=5)

    attempts: list[bool] = []
    # Also stop when the leader task settles so a cancelled or failed body
    # surfaces as an assertion failure instead of an endless poll loop.
    while not body_done.is_set() and not leader_task.done():
        attempts.append(await election2.try_acquire())
        await asyncio.sleep(0.3)

    result = await asyncio.wait_for(leader_task, timeout=15)

    assert result == "finished"
    assert attempts
    assert not any(attempts)


@pytest.mark.asyncio
async def test_run_if_leader_cancels_body_when_lease_lost(db_setup, monkeypatch: pytest.MonkeyPatch):
    from app.core.scheduling.leader_election import LeaderElection

    _enable_leader_election(monkeypatch, ttl_seconds=2)

    election1 = LeaderElection(leader_id="instance-1")
    body_started = asyncio.Event()
    body_cancelled = asyncio.Event()

    async def _long_body() -> str:
        body_started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            body_cancelled.set()
            raise
        return "finished"

    task = asyncio.create_task(election1.run_if_leader(_long_body))
    await asyncio.wait_for(body_started.wait(), timeout=2)

    await _steal_lease("thief")

    # The heartbeat renews every 1s, observes rowcount 0 and cancels the body.
    result = await asyncio.wait_for(task, timeout=5)

    assert result is None
    assert body_cancelled.is_set()
    assert election1.is_leader is False


@pytest.mark.asyncio
async def test_automations_scheduler_tick_body_runs_once_across_two_replicas(db_setup, monkeypatch: pytest.MonkeyPatch):
    """Two automations scheduler replicas tick concurrently over one database;
    the tick body must run exactly once.

    Regression: with the old SQLite everyone-is-leader bypass both replicas
    executed the tick body.
    """
    import app.modules.automations.scheduler as automations_scheduler_module
    from app.core.scheduling.leader_election import LeaderElection
    from app.modules.automations.scheduler import AutomationsScheduler

    _enable_leader_election(monkeypatch)

    elections = [LeaderElection(leader_id="instance-1"), LeaderElection(leader_id="instance-2")]
    monkeypatch.setattr(automations_scheduler_module, "_get_leader_election", lambda: elections.pop(0))

    body_runs = 0

    async def _counting_body(self: AutomationsScheduler) -> None:
        nonlocal body_runs
        body_runs += 1

    monkeypatch.setattr(AutomationsScheduler, "_run_due_as_leader", _counting_body)

    scheduler1 = AutomationsScheduler(interval_seconds=60, enabled=True)
    scheduler2 = AutomationsScheduler(interval_seconds=60, enabled=True)

    await asyncio.gather(scheduler1._run_due_once(), scheduler2._run_due_once())

    assert body_runs == 1


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
