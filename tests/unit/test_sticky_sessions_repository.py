from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import StickySession, StickySessionKind
from app.modules.proxy.sticky_repository import StickySessionsRepository

pytestmark = pytest.mark.unit


def _sqlite_locked_error() -> OperationalError:
    return OperationalError("write sticky session", {}, Exception("database is locked"))


async def _no_sleep(_: float) -> None:
    return None


class _ScalarOneResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _ScalarListResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


class _RetryStickyUpsertSession:
    def __init__(self, row: StickySession) -> None:
        self._row = row
        self.executes = 0
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: object | None = None

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    async def execute(self, _: object) -> _ScalarOneResult:
        self.executes += 1
        if self.commits >= 2:
            return _ScalarOneResult(self._row)
        return _ScalarOneResult(None)

    async def commit(self) -> None:
        self.commits += 1
        if self.commits == 1:
            raise _sqlite_locked_error()

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, row: object) -> None:
        self.refreshed = row


@pytest.mark.asyncio
async def test_upsert_retries_sqlite_lock_with_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.sqlite_retry.asyncio.sleep", _no_sleep)
    row = StickySession(key="sticky-retry", kind=StickySessionKind.PROMPT_CACHE, account_id="acc_retry")
    session = _RetryStickyUpsertSession(row)
    repo = StickySessionsRepository(cast(AsyncSession, session))

    saved = await repo.upsert("sticky-retry", "acc_retry", kind=StickySessionKind.PROMPT_CACHE)

    assert saved is row
    assert session.commits == 2
    assert session.rollbacks == 1
    assert session.executes == 3
    assert session.refreshed is row


class _RetryStickyPurgeSession:
    def __init__(self) -> None:
        self.executes = 0
        self.commits = 0
        self.rollbacks = 0

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    async def execute(self, _: object) -> _ScalarListResult:
        self.executes += 1
        return _ScalarListResult(["sticky-a", "sticky-b"])

    async def commit(self) -> None:
        self.commits += 1
        if self.commits == 1:
            raise _sqlite_locked_error()

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_purge_before_retries_sqlite_lock_with_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.sqlite_retry.asyncio.sleep", _no_sleep)
    session = _RetryStickyPurgeSession()
    repo = StickySessionsRepository(cast(AsyncSession, session))

    deleted = await repo.purge_before(
        utcnow() - timedelta(minutes=10),
        kind=StickySessionKind.PROMPT_CACHE,
    )

    assert deleted == 2
    assert session.executes == 2
    assert session.commits == 2
    assert session.rollbacks == 1
