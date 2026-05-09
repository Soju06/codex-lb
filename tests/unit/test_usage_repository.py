from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdditionalUsageHistory, UsageHistory
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.unit


def _sqlite_locked_error() -> OperationalError:
    return OperationalError("insert usage history", {}, Exception("database is locked"))


class _RetryCommitSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: object | None = None

    def add(self, row: object) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1
        if self.commits == 1:
            raise _sqlite_locked_error()

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, row: object) -> None:
        self.refreshed = row


@pytest.mark.asyncio
async def test_usage_history_add_entry_retries_sqlite_lock_with_rollback() -> None:
    session = _RetryCommitSession()
    repo = UsageRepository(cast(AsyncSession, session))

    entry = await repo.add_entry("acc_retry_usage", 42.0)

    assert isinstance(entry, UsageHistory)
    assert session.commits == 2
    assert session.rollbacks == 1
    assert len(session.added) == 2
    assert session.added[-1] is entry
    assert session.refreshed is entry


@pytest.mark.asyncio
async def test_additional_usage_add_entry_retries_sqlite_lock_with_rollback() -> None:
    session = _RetryCommitSession()
    repo = AdditionalUsageRepository(cast(AsyncSession, session))

    await repo.add_entry(
        account_id="acc_retry_additional",
        limit_name="GPT-5.4",
        metered_feature="responses",
        quota_key="gpt-5.4",
        window="primary",
        used_percent=12.5,
    )

    assert isinstance(session.added[-1], AdditionalUsageHistory)
    assert session.commits == 2
    assert session.rollbacks == 1
