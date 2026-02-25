from __future__ import annotations

from datetime import timedelta

from app.core import usage as usage_core
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
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

    async def get_usage_summary(self, *, owner_user_id: str | None = None) -> UsageSummaryResponse:
        now = utcnow()
        accounts = await self._accounts_repo.list_accounts(owner_user_id=owner_user_id)

        primary_rows_raw = await self._latest_usage_rows("primary", owner_user_id=owner_user_id)
        secondary_rows_raw = await self._latest_usage_rows("secondary", owner_user_id=owner_user_id)
        primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
            primary_rows_raw,
            secondary_rows_raw,
        )

        secondary_minutes = usage_core.resolve_window_minutes("secondary", secondary_rows)
        logs_secondary = []
        if secondary_minutes:
            logs_secondary = await self._logs_repo.list_since(
                now - timedelta(minutes=secondary_minutes),
                owner_user_id=owner_user_id,
            )
        return build_usage_summary_response(
            accounts=accounts,
            primary_rows=primary_rows,
            secondary_rows=secondary_rows,
            logs_secondary=logs_secondary,
        )

    async def get_usage_history(self, hours: int, *, owner_user_id: str | None = None) -> UsageHistoryResponse:
        now = utcnow()
        since = now - timedelta(hours=hours)
        accounts = await self._accounts_repo.list_accounts(owner_user_id=owner_user_id)
        usage_rows = [
            row.to_window_row()
            for row in await self._usage_repo.aggregate_since(since, window="primary", owner_user_id=owner_user_id)
        ]

        return build_usage_history_response(
            hours=hours,
            usage_rows=usage_rows,
            accounts=accounts,
            window="primary",
        )

    async def get_usage_window(self, window: str, *, owner_user_id: str | None = None) -> UsageWindowResponse:
        window_key = (window or "").lower()
        if window_key not in {"primary", "secondary"}:
            raise ValueError("window must be 'primary' or 'secondary'")
        accounts = await self._accounts_repo.list_accounts(owner_user_id=owner_user_id)
        primary_rows_raw = await self._latest_usage_rows("primary", owner_user_id=owner_user_id)
        secondary_rows_raw = await self._latest_usage_rows("secondary", owner_user_id=owner_user_id)
        primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
            primary_rows_raw,
            secondary_rows_raw,
        )
        usage_rows = primary_rows if window_key == "primary" else secondary_rows
        window_minutes = usage_core.resolve_window_minutes(window_key, usage_rows)
        return build_usage_window_response(
            window_key=window_key,
            window_minutes=window_minutes,
            usage_rows=usage_rows,
            accounts=accounts,
        )

    async def _latest_usage_rows(self, window: str, *, owner_user_id: str | None = None) -> list[UsageWindowRow]:
        latest = await self._usage_repo.latest_by_account(window=window, owner_user_id=owner_user_id)
        return [
            UsageWindowRow(
                account_id=entry.account_id,
                used_percent=entry.used_percent,
                reset_at=entry.reset_at,
                window_minutes=entry.window_minutes,
                recorded_at=entry.recorded_at,
            )
            for entry in latest.values()
        ]
