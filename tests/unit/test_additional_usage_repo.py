from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import AdditionalUsageHistory, Base
from app.modules.usage.repository import AdditionalUsageRepository

pytestmark = pytest.mark.unit


@pytest.fixture
async def async_session():
    """Create an in-memory SQLite session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[arg-type]
    async with async_session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_add_entry(async_session: AsyncSession) -> None:
    """Test adding an entry to additional usage history."""
    repo = AdditionalUsageRepository(async_session)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=50.0,
        reset_at=1735689600,
        window_minutes=1,
    )

    # Verify entry was added
    from sqlalchemy import select

    stmt = select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "acc_1")
    result = await async_session.execute(stmt)
    entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].account_id == "acc_1"
    assert entries[0].limit_name == "requests_per_minute"
    assert entries[0].used_percent == 50.0


@pytest.mark.asyncio
async def test_latest_by_account_returns_most_recent_per_account(async_session: AsyncSession) -> None:
    """Test that latest_by_account returns only the most recent entry per account."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)
    old_time = now - timedelta(hours=1)

    # Add multiple entries for same account, different times
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=old_time,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=50.0,
        recorded_at=now,
    )

    result = await repo.latest_by_account(limit_name="requests_per_minute", window="1m")

    assert len(result) == 1
    assert "acc_1" in result
    assert result["acc_1"].used_percent == 50.0


@pytest.mark.asyncio
async def test_latest_by_account_multiple_accounts(async_session: AsyncSession) -> None:
    """Test latest_by_account with multiple accounts."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_2",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=60.0,
        recorded_at=now,
    )

    result = await repo.latest_by_account(limit_name="requests_per_minute", window="1m")

    assert len(result) == 2
    assert result["acc_1"].used_percent == 30.0
    assert result["acc_2"].used_percent == 60.0


@pytest.mark.asyncio
async def test_latest_by_account_empty_when_no_data(async_session: AsyncSession) -> None:
    """Test latest_by_account returns empty dict when no data exists."""
    repo = AdditionalUsageRepository(async_session)

    result = await repo.latest_by_account(limit_name="requests_per_minute", window="1m")

    assert result == {}


@pytest.mark.asyncio
async def test_latest_by_account_filters_by_limit_name(async_session: AsyncSession) -> None:
    """Test that latest_by_account only returns requested limit_name."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_hour",
        metered_feature="api_calls",
        window="1h",
        used_percent=60.0,
        recorded_at=now,
    )

    result = await repo.latest_by_account(limit_name="requests_per_minute", window="1m")

    assert len(result) == 1
    assert result["acc_1"].limit_name == "requests_per_minute"
    assert result["acc_1"].used_percent == 30.0


@pytest.mark.asyncio
async def test_latest_by_account_filters_by_window(async_session: AsyncSession) -> None:
    """Test that latest_by_account filters by window."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="5m",
        used_percent=60.0,
        recorded_at=now,
    )

    result = await repo.latest_by_account(limit_name="requests_per_minute", window="1m")

    assert len(result) == 1
    assert result["acc_1"].window == "1m"
    assert result["acc_1"].used_percent == 30.0


@pytest.mark.asyncio
async def test_list_limit_names_returns_distinct_names(async_session: AsyncSession) -> None:
    """Test list_limit_names returns distinct limit names."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_2",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=60.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_hour",
        metered_feature="api_calls",
        window="1h",
        used_percent=40.0,
        recorded_at=now,
    )

    result = await repo.list_limit_names()

    assert len(result) == 2
    assert "requests_per_minute" in result
    assert "requests_per_hour" in result


@pytest.mark.asyncio
async def test_list_limit_names_empty_when_no_data(async_session: AsyncSession) -> None:
    """Test list_limit_names returns empty list when no data exists."""
    repo = AdditionalUsageRepository(async_session)

    result = await repo.list_limit_names()

    assert result == []


@pytest.mark.asyncio
async def test_history_since_returns_time_series(async_session: AsyncSession) -> None:
    """Test history_since returns time-series entries in order."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)
    t1 = now - timedelta(hours=2)
    t2 = now - timedelta(hours=1)
    t3 = now

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=t1,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=40.0,
        recorded_at=t2,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=50.0,
        recorded_at=t3,
    )

    since = now - timedelta(hours=3)
    result = await repo.history_since(
        account_id="acc_1",
        limit_name="requests_per_minute",
        window="1m",
        since=since,
    )

    assert len(result) == 3
    assert result[0].used_percent == 30.0
    assert result[1].used_percent == 40.0
    assert result[2].used_percent == 50.0


@pytest.mark.asyncio
async def test_history_since_filters_by_since_time(async_session: AsyncSession) -> None:
    """Test history_since only returns entries after since time."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)
    t1 = now - timedelta(hours=2)
    t2 = now - timedelta(hours=1)
    t3 = now

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=t1,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=40.0,
        recorded_at=t2,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=50.0,
        recorded_at=t3,
    )

    since = now - timedelta(hours=1, minutes=30)
    result = await repo.history_since(
        account_id="acc_1",
        limit_name="requests_per_minute",
        window="1m",
        since=since,
    )

    assert len(result) == 2
    assert result[0].used_percent == 40.0
    assert result[1].used_percent == 50.0


@pytest.mark.asyncio
async def test_history_since_filters_by_account_id(async_session: AsyncSession) -> None:
    """Test history_since only returns entries for specified account."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_2",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=60.0,
        recorded_at=now,
    )

    since = now - timedelta(hours=1)
    result = await repo.history_since(
        account_id="acc_1",
        limit_name="requests_per_minute",
        window="1m",
        since=since,
    )

    assert len(result) == 1
    assert result[0].account_id == "acc_1"
    assert result[0].used_percent == 30.0


@pytest.mark.asyncio
async def test_history_since_filters_by_limit_name(async_session: AsyncSession) -> None:
    """Test history_since only returns entries for specified limit_name."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_hour",
        metered_feature="api_calls",
        window="1h",
        used_percent=60.0,
        recorded_at=now,
    )

    since = now - timedelta(hours=1)
    result = await repo.history_since(
        account_id="acc_1",
        limit_name="requests_per_minute",
        window="1m",
        since=since,
    )

    assert len(result) == 1
    assert result[0].limit_name == "requests_per_minute"


@pytest.mark.asyncio
async def test_history_since_filters_by_window(async_session: AsyncSession) -> None:
    """Test history_since only returns entries for specified window."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="5m",
        used_percent=60.0,
        recorded_at=now,
    )

    since = now - timedelta(hours=1)
    result = await repo.history_since(
        account_id="acc_1",
        limit_name="requests_per_minute",
        window="1m",
        since=since,
    )

    assert len(result) == 1
    assert result[0].window == "1m"


@pytest.mark.asyncio
async def test_history_since_empty_when_no_data(async_session: AsyncSession) -> None:
    """Test history_since returns empty list when no data exists."""
    repo = AdditionalUsageRepository(async_session)

    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(hours=1)

    result = await repo.history_since(
        account_id="acc_1",
        limit_name="requests_per_minute",
        window="1m",
        since=since,
    )

    assert result == []
