from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.exceptions import DashboardRateLimitError
from app.core.rate_limiter.db_rate_limiter import DatabaseRateLimiter
from app.db.models import Base, RateLimitAttempt

pytestmark = pytest.mark.unit


@pytest.fixture
async def async_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_single_instance_blocks_after_eight_attempts(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    limiter = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="totp")

    async with async_session_factory() as session:
        for _ in range(8):
            await limiter.check_and_record("ip:single", session)

        with pytest.raises(DashboardRateLimitError):
            await limiter.check_and_record("ip:single", session)


@pytest.mark.asyncio
async def test_cross_replica_combined_attempts_are_enforced(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    replica_one = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="password")
    replica_two = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="password")

    async with async_session_factory() as session_one:
        for _ in range(4):
            await replica_one.check_and_record("ip:replica", session_one)

    async with async_session_factory() as session_two:
        for _ in range(4):
            await replica_two.check_and_record("ip:replica", session_two)

    async with async_session_factory() as session_one_again:
        with pytest.raises(DashboardRateLimitError):
            await replica_one.check_and_record("ip:replica", session_one_again)


@pytest.mark.asyncio
async def test_window_expiry_ignores_old_attempts(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    limiter = DatabaseRateLimiter(max_attempts=1, window_seconds=60, type="totp")

    async with async_session_factory() as session:
        old_attempt = RateLimitAttempt(
            key="ip:expired",
            type="totp",
            attempted_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        session.add(old_attempt)
        await session.commit()

        await limiter.check_and_record("ip:expired", session)
        with pytest.raises(DashboardRateLimitError):
            await limiter.check_and_record("ip:expired", session)


def test_migration_upgrade_downgrade_upgrade_is_reversible(tmp_path: Path) -> None:
    db_path = tmp_path / "rate_limit_migration.db"
    cfg = Config()
    cfg.set_main_option("script_location", str((Path(__file__).resolve().parents[2] / "app/db/alembic").resolve()))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    engine = sa.create_engine(f"sqlite:///{db_path}", future=True)
    try:
        inspector = sa.inspect(engine)
        assert inspector.has_table("rate_limit_attempts") is True
    finally:
        engine.dispose()


# RED (xfail) tests proving the bug: successful logins are counted toward lockout


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="BUG: check_and_record is called for both success and failure, so 8 successful logins cause lockout",
)
async def test_successful_login_not_counted_toward_lockout(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """8 successful logins should NOT cause lockout. Currently they do (bug)."""
    limiter = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="password")

    async with async_session_factory() as session:
        # Simulate 8 "successful" logins — currently check_and_record is called for all
        for _ in range(8):
            await limiter.check_and_record("ip:success-test", session)

        # After 8 successful logins, the user should still be able to log in
        # But currently they're blocked (this is the bug)
        # With correct behavior: successful login would reset the counter
        # Here we prove that after 8 check_and_records, the 9th fails
        # The test FAILS (xfail) because currently successes are counted
        await limiter.check_and_record("ip:success-test", session)  # Should NOT raise if successes reset counter


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="clear_for_key method not yet implemented — needed to reset counter on successful login",
)
async def test_clear_for_key_resets_lockout(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Successful login should reset the rate limit counter via clear_for_key."""
    limiter = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="password")

    async with async_session_factory() as session:
        # Record 8 failed attempts
        for _ in range(8):
            await limiter.check_and_record("ip:clear-test", session)

        # clear_for_key doesn't exist yet — this will raise AttributeError (xfail)
        await limiter.clear_for_key("ip:clear-test", session)  # type: ignore[attr-defined]

        # After clearing, should be able to attempt again
        await limiter.check_and_record("ip:clear-test", session)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="check() method not yet implemented — needed to check without incrementing counter",
)
async def test_check_only_does_not_increment_counter(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """check() should only read, not write to the attempts table."""
    limiter = DatabaseRateLimiter(max_attempts=2, window_seconds=60, type="password")

    async with async_session_factory() as session:
        # check() without recording — 10 checks should NOT block
        for _ in range(10):
            await limiter.check("ip:check-only", session)  # type: ignore[attr-defined]

        # Still able to record
        await limiter.check_and_record("ip:check-only", session)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="record_failure() method not yet implemented — needed to record only failures",
)
async def test_record_failure_only_counts_failures(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """record_failure() should be the method called only when authentication fails."""
    limiter = DatabaseRateLimiter(max_attempts=3, window_seconds=60, type="password")

    async with async_session_factory() as session:
        # 2 failures recorded explicitly
        await limiter.record_failure("ip:failure-test", session)  # type: ignore[attr-defined]
        await limiter.record_failure("ip:failure-test", session)  # type: ignore[attr-defined]

        # 1 success (clear) in between
        await limiter.clear_for_key("ip:failure-test", session)  # type: ignore[attr-defined]

        # 1 more failure after success — should only be 1 total
        await limiter.record_failure("ip:failure-test", session)  # type: ignore[attr-defined]

        # Should NOT be blocked (only 1 failure since last success)
        await limiter.check("ip:failure-test", session)  # type: ignore[attr-defined]
