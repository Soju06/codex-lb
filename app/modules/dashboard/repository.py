from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.types import BucketModelAggregate
from app.db.models import Account, AdditionalUsageHistory, RequestLog, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._accounts_repo = AccountsRepository(session)
        self._usage_repo = UsageRepository(session)
        self._logs_repo = RequestLogsRepository(session)
        self._additional_usage_repo = AdditionalUsageRepository(session)

    async def list_accounts(self) -> list[Account]:
        return await self._accounts_repo.list_accounts()

    async def latest_usage_by_account(self, window: str) -> dict[str, UsageHistory]:
        return await self._usage_repo.latest_by_account(window=window)

    async def usage_history_since(
        self,
        account_id: str,
        window: str,
        since: datetime,
    ) -> list[UsageHistory]:
        return await self._usage_repo.history_since(account_id, window, since)

    async def bulk_usage_history_since(
        self,
        account_ids: list[str],
        window: str,
        since: datetime,
    ) -> dict[str, list[UsageHistory]]:
        return await self._usage_repo.bulk_history_since(account_ids, window, since)

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

    async def list_additional_quota_keys(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        return await self._additional_usage_repo.list_quota_keys(account_ids=account_ids, since=since)

    async def latest_additional_usage_by_account(
        self, quota_key: str, window: str
    ) -> dict[str, AdditionalUsageHistory]:
        return await self._additional_usage_repo.latest_by_account(quota_key, window)

    async def latest_additional_recorded_at(self) -> datetime | None:
        return await self._additional_usage_repo.latest_recorded_at()
