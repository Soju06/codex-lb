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
