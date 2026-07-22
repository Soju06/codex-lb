from __future__ import annotations

from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Account, AccountStatus, Base, UsageHistory
from app.modules.usage import background_repository as background_repository_module
from app.modules.usage.background_repository import BackgroundUsageRepository
from app.modules.usage.repository import UsageRepository, UsageWindowWrite

pytestmark = pytest.mark.unit


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _account(account_id: str) -> Account:
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime(2026, 7, 22, tzinfo=timezone.utc),
        status=AccountStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_add_account_snapshot_commits_all_windows_once_with_one_timestamp(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(_account("acc_snapshot"))
        await session.commit()
        commit_count = 0

        def record_commit(_: object) -> None:
            nonlocal commit_count
            commit_count += 1

        event.listen(session.sync_session, "after_commit", record_commit)
        captured_at = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        try:
            rows = await UsageRepository(session).add_account_snapshot(
                "acc_snapshot",
                [
                    UsageWindowWrite(
                        window="primary",
                        used_percent=12.5,
                        reset_at=100,
                        window_minutes=300,
                        credits_has=True,
                        credits_balance=42.5,
                    ),
                    UsageWindowWrite(
                        window="secondary",
                        used_percent=55.0,
                        reset_at=200,
                        window_minutes=10080,
                    ),
                ],
                recorded_at=captured_at,
            )
        finally:
            event.remove(session.sync_session, "after_commit", record_commit)

        assert commit_count == 1
        assert [row.window for row in rows] == ["primary", "secondary"]
        assert {row.recorded_at for row in rows} == {captured_at}
        session.expunge_all()
        persisted_rows = list(
            (
                await session.scalars(
                    select(UsageHistory)
                    .where(UsageHistory.account_id == "acc_snapshot")
                    .order_by(UsageHistory.id.asc())
                )
            ).all()
        )
        assert [row.window for row in persisted_rows] == ["primary", "secondary"]
        assert {row.recorded_at for row in persisted_rows} == {captured_at.replace(tzinfo=None)}


@pytest.mark.asyncio
async def test_add_account_snapshot_rolls_back_partial_flush_and_keeps_caller_session_reusable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(_account("acc_rollback"))
        await session.commit()
        inserted_windows: list[str | None] = []
        rollback_count = 0

        def reject_secondary(_: object, __: object, target: UsageHistory) -> None:
            inserted_windows.append(target.window)
            if target.window == "secondary":
                raise RuntimeError("injected secondary-row failure")

        def record_rollback(_: object) -> None:
            nonlocal rollback_count
            rollback_count += 1

        event.listen(UsageHistory, "before_insert", reject_secondary)
        event.listen(session.sync_session, "after_rollback", record_rollback)
        try:
            with pytest.raises(RuntimeError, match="injected secondary-row failure"):
                await UsageRepository(session).add_account_snapshot(
                    "acc_rollback",
                    [
                        UsageWindowWrite(window="primary", used_percent=10.0),
                        UsageWindowWrite(window="secondary", used_percent=20.0),
                    ],
                )
        finally:
            event.remove(UsageHistory, "before_insert", reject_secondary)
            event.remove(session.sync_session, "after_rollback", record_rollback)

        assert inserted_windows == ["primary", "secondary"]
        assert rollback_count == 1
        assert (
            await session.scalar(select(func.count(UsageHistory.id)).where(UsageHistory.account_id == "acc_rollback"))
            == 0
        )

        rows = await UsageRepository(session).add_account_snapshot(
            "acc_rollback",
            [UsageWindowWrite(window="primary", used_percent=30.0)],
        )
        assert len(rows) == 1
        assert (
            await session.scalar(select(func.count(UsageHistory.id)).where(UsageHistory.account_id == "acc_rollback"))
            == 1
        )


@pytest.mark.asyncio
async def test_background_snapshot_uses_one_owned_session_until_rows_are_detached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object()
    events: list[str] = []
    persisted_rows = [
        UsageHistory(
            id=1,
            account_id="acc_background",
            window="primary",
            used_percent=10.0,
            recorded_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )
    ]

    @asynccontextmanager
    async def recording_background_session() -> AsyncIterator[object]:
        events.append("session_open")
        try:
            yield session
        finally:
            events.append("session_close")

    class RecordingUsageRepository:
        def __init__(self, provided_session: object) -> None:
            assert provided_session is session
            events.append("repository_created")

        async def add_account_snapshot(
            self,
            account_id: str,
            windows: Collection[UsageWindowWrite],
            *,
            recorded_at: datetime | None = None,
        ) -> list[UsageHistory]:
            assert account_id == "acc_background"
            assert [window.window for window in windows] == ["primary", "secondary"]
            assert recorded_at is not None
            events.append("snapshot_persisted")
            return persisted_rows

    def record_detach(provided_session: object) -> None:
        assert provided_session is session
        events.append("rows_detached")

    monkeypatch.setattr(background_repository_module, "get_background_session", recording_background_session)
    monkeypatch.setattr(background_repository_module, "UsageRepository", RecordingUsageRepository)
    monkeypatch.setattr(background_repository_module, "detach_session_objects", record_detach)

    rows = await BackgroundUsageRepository().add_account_snapshot(
        "acc_background",
        [
            UsageWindowWrite(window="primary", used_percent=10.0),
            UsageWindowWrite(window="secondary", used_percent=20.0),
        ],
        recorded_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert rows is persisted_rows
    assert events == [
        "session_open",
        "repository_created",
        "snapshot_persisted",
        "rows_detached",
        "session_close",
    ]
