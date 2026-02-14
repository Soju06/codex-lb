from __future__ import annotations

from datetime import timedelta

from app.core import usage as usage_core
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.builders import (
    build_usage_history_response,
    build_usage_summary_response,
    build_usage_window_response,
)
from app.modules.usage.repository import UsageRepository
from app.modules.usage.schemas import (
    UsageHistoryResponse,
    UsageSummaryResponse,
    UsageWindowResponse,
)


class UsageService:
    def __init__(
        self,
        usage_repo: UsageRepository,
        logs_repo: RequestLogsRepository,
        accounts_repo: AccountsRepository,
    ) -> None:
        self._usage_repo = usage_repo
        self._logs_repo = logs_repo
        self._accounts_repo = accounts_repo

    async def get_usage_summary(self) -> UsageSummaryResponse:
        now = utcnow()
        accounts = await self._accounts_repo.list_accounts()

        primary_rows = await self._latest_usage_rows("primary")
        secondary_rows = await self._latest_usage_rows("secondary")
        spark_primary_rows = await self._latest_usage_rows("spark_primary")
        spark_secondary_rows = await self._latest_usage_rows("spark_secondary")
        spark_window_label = await self._spark_window_label()

        secondary_minutes = await self._usage_repo.latest_window_minutes("secondary")
        if secondary_minutes is None:
            secondary_minutes = usage_core.default_window_minutes("secondary")
        logs_secondary = []
        if secondary_minutes:
            logs_secondary = await self._logs_repo.list_since(now - timedelta(minutes=secondary_minutes))
        return build_usage_summary_response(
            accounts=accounts,
            primary_rows=primary_rows,
            secondary_rows=secondary_rows,
            spark_primary_rows=spark_primary_rows,
            spark_secondary_rows=spark_secondary_rows,
            spark_window_label=spark_window_label,
            logs_secondary=logs_secondary,
        )

    async def get_usage_history(self, hours: int) -> UsageHistoryResponse:
        now = utcnow()
        since = now - timedelta(hours=hours)
        accounts = await self._accounts_repo.list_accounts()
        usage_rows = [row.to_window_row() for row in await self._usage_repo.aggregate_since(since, window="primary")]

        return build_usage_history_response(
            hours=hours,
            usage_rows=usage_rows,
            accounts=accounts,
            window="primary",
        )

    async def get_usage_window(self, window: str) -> UsageWindowResponse:
        window_key = (window or "").lower()
        if window_key not in {"primary", "secondary"}:
            raise ValueError("window must be 'primary' or 'secondary'")
        accounts = await self._accounts_repo.list_accounts()
        usage_rows = await self._latest_usage_rows(window_key)
        window_minutes = await self._usage_repo.latest_window_minutes(window_key)
        if window_minutes is None:
            window_minutes = usage_core.default_window_minutes(window_key)
        return build_usage_window_response(
            window_key=window_key,
            window_minutes=window_minutes,
            usage_rows=usage_rows,
            accounts=accounts,
        )

    async def _latest_usage_rows(self, window: str) -> list[UsageWindowRow]:
        latest = await self._usage_repo.latest_by_account(window=window)
        return [
            UsageWindowRow(
                account_id=entry.account_id,
                used_percent=entry.used_percent,
                reset_at=entry.reset_at,
                window_minutes=entry.window_minutes,
            )
            for entry in latest.values()
        ]

    async def _spark_window_label(self) -> str | None:
        spark_primary_latest = await self._usage_repo.latest_by_account(window="spark_primary")
        spark_secondary_latest = await self._usage_repo.latest_by_account(window="spark_secondary")
        return _spark_window_label_from_entries(
            list(spark_primary_latest.values()),
            list(spark_secondary_latest.values()),
        )


def _spark_window_label_from_entries(
    spark_primary_entries: list[UsageHistory],
    spark_secondary_entries: list[UsageHistory],
) -> str | None:
    entries = [*spark_primary_entries, *spark_secondary_entries]
    if not entries:
        return None
    for entry in entries:
        label = (entry.window_label or "").strip()
        if label:
            return usage_core.normalize_spark_window_label(label)
    return usage_core.normalize_spark_window_label(None)
