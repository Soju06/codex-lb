from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.types import BucketModelAggregate
from app.db.models import Account, RequestLog, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._accounts_repo = AccountsRepository(session)
        self._usage_repo = UsageRepository(session)
        self._logs_repo = RequestLogsRepository(session)

    async def list_accounts(self) -> list[Account]:
        return await self._accounts_repo.list_accounts()

    async def latest_usage_by_account(self, window: str) -> dict[str, UsageHistory]:
        return await self._usage_repo.latest_by_account(window=window)

    async def latest_window_minutes(self, window: str) -> int | None:
        return await self._usage_repo.latest_window_minutes(window)

    async def list_logs_since(self, since: datetime) -> list[RequestLog]:
        return await self._logs_repo.list_since(since)

    async def aggregate_logs_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
    ) -> list[BucketModelAggregate]:
        return await self._logs_repo.aggregate_by_bucket(since, bucket_seconds)
