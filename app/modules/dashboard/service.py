from __future__ import annotations

from datetime import timedelta

from app.core import usage as usage_core
from app.core.crypto import TokenEncryptor
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import UsageHistory
from app.modules.accounts.mappers import build_account_summaries
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.schemas import DashboardOverviewResponse, DashboardUsageWindows
from app.modules.usage.builders import (
    build_trends_from_buckets,
    build_usage_summary_response,
    build_usage_window_response,
)


class DashboardService:
    def __init__(self, repo: DashboardRepository) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()

    async def get_overview(self) -> DashboardOverviewResponse:
        now = utcnow()
        accounts = await self._repo.list_accounts()
        primary_usage = await self._repo.latest_usage_by_account("primary")
        secondary_usage = await self._repo.latest_usage_by_account("secondary")

        account_summaries = build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            encryptor=self._encryptor,
            include_auth=False,
        )

        primary_rows = _rows_from_latest(primary_usage)
        secondary_rows = _rows_from_latest(secondary_usage)

        secondary_minutes = await self._repo.latest_window_minutes("secondary")
        if secondary_minutes is None:
            secondary_minutes = usage_core.default_window_minutes("secondary")

        # Use bucket aggregation instead of loading all logs
        bucket_since = now - timedelta(minutes=secondary_minutes) if secondary_minutes else now - timedelta(days=7)
        bucket_rows = await self._repo.aggregate_logs_by_bucket(bucket_since)
        trends, bucket_metrics, bucket_cost = build_trends_from_buckets(bucket_rows, bucket_since)

        summary = build_usage_summary_response(
            accounts=accounts,
            primary_rows=primary_rows,
            secondary_rows=secondary_rows,
            logs_secondary=[],
            metrics_override=bucket_metrics,
            cost_override=bucket_cost,
        )

        primary_window_minutes = await self._repo.latest_window_minutes("primary")
        if primary_window_minutes is None:
            primary_window_minutes = usage_core.default_window_minutes("primary")

        windows = DashboardUsageWindows(
            primary=build_usage_window_response(
                window_key="primary",
                window_minutes=primary_window_minutes,
                usage_rows=primary_rows,
                accounts=accounts,
            ),
            secondary=build_usage_window_response(
                window_key="secondary",
                window_minutes=secondary_minutes,
                usage_rows=secondary_rows,
                accounts=accounts,
            ),
        )

        return DashboardOverviewResponse(
            last_sync_at=_latest_recorded_at(primary_usage, secondary_usage),
            accounts=account_summaries,
            summary=summary,
            windows=windows,
            trends=trends,
        )


def _rows_from_latest(latest: dict[str, UsageHistory]) -> list[UsageWindowRow]:
    return [
        UsageWindowRow(
            account_id=entry.account_id,
            used_percent=entry.used_percent,
            reset_at=entry.reset_at,
            window_minutes=entry.window_minutes,
        )
        for entry in latest.values()
    ]


def _latest_recorded_at(
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
):
    timestamps = [
        entry.recorded_at
        for entry in list(primary_usage.values()) + list(secondary_usage.values())
        if entry.recorded_at is not None
    ]
    return max(timestamps) if timestamps else None
