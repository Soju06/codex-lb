from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import AdditionalUsageHistory, Base
from app.modules.usage.additional_quota_keys import clear_additional_quota_registry_cache
from app.modules.usage.repository import AdditionalUsageRepository

pytestmark = pytest.mark.unit


@pytest.fixture
async def async_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = async_session_factory()
    try:
        yield session
    finally:
        await session.close()

    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_registry_cache() -> Iterator[None]:
    clear_additional_quota_registry_cache()
    yield
    clear_additional_quota_registry_cache()


def _write_registry(path: Path, *, quota_key: str, quota_key_aliases: list[str] | None = None) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "quota_key": quota_key,
                    "quota_key_aliases": quota_key_aliases or [],
                    "display_label": "Spark Enterprise",
                    "model_ids": ["gpt-5.3-codex-spark"],
                    "limit_name_aliases": ["codex_other"],
                    "metered_feature_aliases": ["codex_bengalfox"],
                }
            ]
        ),
        encoding="utf-8",
    )


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
async def test_latest_by_account_canonicalizes_legacy_limit_name_alias(async_session: AsyncSession) -> None:
    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="codex_other",
        metered_feature="codex_bengalfox",
        window="primary",
        used_percent=30.0,
        recorded_at=now,
    )

    result = await repo.latest_by_account(limit_name="GPT-5.3-Codex-Spark", window="primary")

    assert list(result) == ["acc_1"]
    assert result["acc_1"].quota_key == "codex_spark"
    assert result["acc_1"].limit_name == "codex_other"


@pytest.mark.asyncio
async def test_latest_by_account_reads_rows_under_legacy_quota_key_alias(
    async_session: AsyncSession,
    monkeypatch,
    tmp_path: Path,
) -> None:
    registry = tmp_path / "additional_quota_registry.json"
    _write_registry(registry, quota_key="spark_enterprise", quota_key_aliases=["codex_spark"])
    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry))
    clear_additional_quota_registry_cache()

    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    async_session.add(
        AdditionalUsageHistory(
            account_id="acc_legacy",
            quota_key="codex_spark",
            limit_name="codex_other",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=42.0,
            recorded_at=now,
        )
    )
    await async_session.commit()

    result = await repo.latest_by_account(quota_key="spark_enterprise", window="primary")

    assert list(result) == ["acc_legacy"]
    assert result["acc_legacy"].quota_key == "codex_spark"
    assert await repo.list_quota_keys() == ["spark_enterprise"]


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


@pytest.mark.asyncio
async def test_history_since_canonicalizes_legacy_limit_name_alias(async_session: AsyncSession) -> None:
    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_1",
        limit_name="codex_other",
        metered_feature="codex_bengalfox",
        window="primary",
        used_percent=30.0,
        recorded_at=now - timedelta(minutes=5),
    )
    await repo.add_entry(
        account_id="acc_1",
        limit_name="codex_other",
        metered_feature="codex_bengalfox",
        window="primary",
        used_percent=45.0,
        recorded_at=now,
    )

    result = await repo.history_since(
        account_id="acc_1",
        limit_name="GPT-5.3-Codex-Spark",
        window="primary",
        since=now - timedelta(hours=1),
    )

    assert [entry.used_percent for entry in result] == [30.0, 45.0]
    assert all(entry.quota_key == "codex_spark" for entry in result)


@pytest.mark.asyncio
async def test_history_since_reads_rows_under_legacy_quota_key_alias(
    async_session: AsyncSession,
    monkeypatch,
    tmp_path: Path,
) -> None:
    registry = tmp_path / "additional_quota_registry.json"
    _write_registry(registry, quota_key="spark_enterprise", quota_key_aliases=["codex_spark"])
    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry))
    clear_additional_quota_registry_cache()

    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    async_session.add_all(
        [
            AdditionalUsageHistory(
                account_id="acc_legacy",
                quota_key="codex_spark",
                limit_name="codex_other",
                metered_feature="codex_bengalfox",
                window="primary",
                used_percent=20.0,
                recorded_at=now - timedelta(minutes=5),
            ),
            AdditionalUsageHistory(
                account_id="acc_legacy",
                quota_key="codex_spark",
                limit_name="codex_other",
                metered_feature="codex_bengalfox",
                window="primary",
                used_percent=45.0,
                recorded_at=now,
            ),
        ]
    )
    await async_session.commit()

    result = await repo.history_since(
        account_id="acc_legacy",
        quota_key="spark_enterprise",
        window="primary",
        since=now - timedelta(hours=1),
    )

    assert [entry.used_percent for entry in result] == [20.0, 45.0]
    assert all(entry.quota_key == "codex_spark" for entry in result)


@pytest.mark.asyncio
async def test_delete_for_account_removes_all_rows(async_session: AsyncSession) -> None:
    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_hour",
        metered_feature="api_calls",
        window="1h",
        used_percent=60.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_keep",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=45.0,
        recorded_at=now,
    )

    await repo.delete_for_account("acc_delete")

    result = await async_session.execute(select(AdditionalUsageHistory).order_by(AdditionalUsageHistory.account_id))
    entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].account_id == "acc_keep"


@pytest.mark.asyncio
async def test_delete_for_account_and_limit_removes_only_matching_rows(async_session: AsyncSession) -> None:
    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="5m",
        used_percent=35.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_hour",
        metered_feature="api_calls",
        window="1h",
        used_percent=60.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_keep",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="1m",
        used_percent=45.0,
        recorded_at=now,
    )

    await repo.delete_for_account_and_limit("acc_delete", "requests_per_minute")

    result = await async_session.execute(
        select(AdditionalUsageHistory).order_by(
            AdditionalUsageHistory.account_id,
            AdditionalUsageHistory.limit_name,
            AdditionalUsageHistory.window,
        )
    )
    entries = result.scalars().all()

    assert len(entries) == 2
    assert [(entry.account_id, entry.limit_name, entry.window) for entry in entries] == [
        ("acc_delete", "requests_per_hour", "1h"),
        ("acc_keep", "requests_per_minute", "1m"),
    ]


@pytest.mark.asyncio
async def test_delete_for_account_and_limit_canonicalizes_legacy_alias(async_session: AsyncSession) -> None:
    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_delete",
        limit_name="codex_other",
        metered_feature="codex_bengalfox",
        window="primary",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_hour",
        metered_feature="api_calls",
        window="primary",
        used_percent=60.0,
        recorded_at=now,
    )

    await repo.delete_for_account_and_limit("acc_delete", "GPT-5.3-Codex-Spark")

    result = await async_session.execute(
        select(AdditionalUsageHistory).order_by(
            AdditionalUsageHistory.account_id,
            AdditionalUsageHistory.limit_name,
            AdditionalUsageHistory.window,
        )
    )
    entries = result.scalars().all()

    assert [(entry.account_id, entry.limit_name, entry.window) for entry in entries] == [
        ("acc_delete", "requests_per_hour", "primary"),
    ]


@pytest.mark.asyncio
async def test_delete_for_account_and_quota_key_removes_rows_under_legacy_quota_key_alias(
    async_session: AsyncSession,
    monkeypatch,
    tmp_path: Path,
) -> None:
    registry = tmp_path / "additional_quota_registry.json"
    _write_registry(registry, quota_key="spark_enterprise", quota_key_aliases=["codex_spark"])
    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry))
    clear_additional_quota_registry_cache()

    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    async_session.add(
        AdditionalUsageHistory(
            account_id="acc_delete",
            quota_key="codex_spark",
            limit_name="codex_other",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=30.0,
            recorded_at=now,
        )
    )
    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_hour",
        metered_feature="api_calls",
        window="primary",
        used_percent=60.0,
        recorded_at=now,
    )
    await async_session.commit()

    await repo.delete_for_account_and_quota_key("acc_delete", "spark_enterprise")

    result = await async_session.execute(
        select(AdditionalUsageHistory).order_by(
            AdditionalUsageHistory.account_id,
            AdditionalUsageHistory.limit_name,
            AdditionalUsageHistory.window,
        )
    )
    entries = result.scalars().all()

    assert [(entry.account_id, entry.limit_name, entry.window) for entry in entries] == [
        ("acc_delete", "requests_per_hour", "primary"),
    ]


@pytest.mark.asyncio
async def test_delete_for_account_limit_window_removes_only_matching_window(async_session: AsyncSession) -> None:
    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="primary",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_minute",
        metered_feature="api_calls",
        window="secondary",
        used_percent=35.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_delete",
        limit_name="requests_per_hour",
        metered_feature="api_calls",
        window="primary",
        used_percent=60.0,
        recorded_at=now,
    )

    await repo.delete_for_account_limit_window("acc_delete", "requests_per_minute", "secondary")

    result = await async_session.execute(
        select(AdditionalUsageHistory).order_by(
            AdditionalUsageHistory.account_id,
            AdditionalUsageHistory.limit_name,
            AdditionalUsageHistory.window,
        )
    )
    entries = result.scalars().all()

    assert [(entry.account_id, entry.limit_name, entry.window) for entry in entries] == [
        ("acc_delete", "requests_per_hour", "primary"),
        ("acc_delete", "requests_per_minute", "primary"),
    ]


@pytest.mark.asyncio
async def test_delete_for_account_limit_window_canonicalizes_legacy_alias(async_session: AsyncSession) -> None:
    repo = AdditionalUsageRepository(async_session)
    now = datetime.now(tz=timezone.utc)

    await repo.add_entry(
        account_id="acc_delete",
        limit_name="codex_other",
        metered_feature="codex_bengalfox",
        window="primary",
        used_percent=30.0,
        recorded_at=now,
    )
    await repo.add_entry(
        account_id="acc_delete",
        limit_name="codex_other",
        metered_feature="codex_bengalfox",
        window="secondary",
        used_percent=35.0,
        recorded_at=now,
    )

    await repo.delete_for_account_limit_window("acc_delete", "GPT-5.3-Codex-Spark", "secondary")

    result = await async_session.execute(
        select(AdditionalUsageHistory).order_by(
            AdditionalUsageHistory.account_id,
            AdditionalUsageHistory.limit_name,
            AdditionalUsageHistory.window,
        )
    )
    entries = result.scalars().all()

    assert [(entry.account_id, entry.limit_name, entry.window) for entry in entries] == [
        ("acc_delete", "codex_other", "primary"),
    ]
