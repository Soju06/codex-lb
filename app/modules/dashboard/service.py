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
from app.modules.request_logs.mappers import to_request_log_entry
from app.modules.usage.builders import (
    build_usage_summary_response,
    build_usage_window_response,
)


class DashboardService:
    def __init__(self, repo: DashboardRepository) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()

    async def get_overview(self, *, request_limit: int, request_offset: int) -> DashboardOverviewResponse:
        now = utcnow()
        accounts = await self._repo.list_accounts()
        primary_usage = await self._repo.latest_usage_by_account("primary")
        secondary_usage = await self._repo.latest_usage_by_account("secondary")
        spark_primary_usage = await self._repo.latest_usage_by_account("spark_primary")
        spark_secondary_usage = await self._repo.latest_usage_by_account("spark_secondary")

        account_summaries = build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            spark_primary_usage=spark_primary_usage,
            spark_secondary_usage=spark_secondary_usage,
            encryptor=self._encryptor,
        )

        primary_rows = _rows_from_latest(primary_usage)
        secondary_rows = _rows_from_latest(secondary_usage)
        spark_primary_rows = _rows_from_latest(spark_primary_usage)
        spark_secondary_rows = _rows_from_latest(spark_secondary_usage)
        spark_window_label = _spark_window_label_from_latest(spark_primary_usage, spark_secondary_usage)

        secondary_minutes = await self._repo.latest_window_minutes("secondary")
        if secondary_minutes is None:
            secondary_minutes = usage_core.default_window_minutes("secondary")
        logs_secondary = []
        if secondary_minutes:
            logs_secondary = await self._repo.list_logs_since(now - timedelta(minutes=secondary_minutes))

        summary = build_usage_summary_response(
            accounts=accounts,
            primary_rows=primary_rows,
            secondary_rows=secondary_rows,
            spark_primary_rows=spark_primary_rows,
            spark_secondary_rows=spark_secondary_rows,
            spark_window_label=spark_window_label,
            logs_secondary=logs_secondary,
        )

        primary_window_minutes = await self._repo.latest_window_minutes("primary")
        if primary_window_minutes is None:
            primary_window_minutes = usage_core.default_window_minutes("primary")

        spark_primary_window = None
        if spark_primary_rows:
            spark_primary_window_minutes = await self._repo.latest_window_minutes("spark_primary")
            if spark_primary_window_minutes is None:
                spark_primary_window_minutes = usage_core.default_window_minutes("spark_primary")
            spark_primary_window = build_usage_window_response(
                window_key="spark_primary",
                window_minutes=spark_primary_window_minutes,
                usage_rows=spark_primary_rows,
                accounts=accounts,
            )

        spark_secondary_window = None
        if spark_secondary_rows:
            spark_secondary_window_minutes = await self._repo.latest_window_minutes("spark_secondary")
            if spark_secondary_window_minutes is None:
                spark_secondary_window_minutes = usage_core.default_window_minutes("spark_secondary")
            spark_secondary_window = build_usage_window_response(
                window_key="spark_secondary",
                window_minutes=spark_secondary_window_minutes,
                usage_rows=spark_secondary_rows,
                accounts=accounts,
            )

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
            spark_primary=spark_primary_window,
            spark_secondary=spark_secondary_window,
        )

        recent_logs = await self._repo.list_recent_logs(limit=request_limit, offset=request_offset)
        request_logs = [to_request_log_entry(log) for log in recent_logs]

        return DashboardOverviewResponse(
            last_sync_at=_latest_recorded_at(
                primary_usage,
                secondary_usage,
                spark_primary_usage,
                spark_secondary_usage,
            ),
            accounts=account_summaries,
            summary=summary,
            windows=windows,
            request_logs=request_logs,
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
    spark_primary_usage: dict[str, UsageHistory],
    spark_secondary_usage: dict[str, UsageHistory],
):
    timestamps = [
        entry.recorded_at
        for entry in [
            *list(primary_usage.values()),
            *list(secondary_usage.values()),
            *list(spark_primary_usage.values()),
            *list(spark_secondary_usage.values()),
        ]
        if entry.recorded_at is not None
    ]
    return max(timestamps) if timestamps else None


def _spark_window_label_from_latest(
    spark_primary_usage: dict[str, UsageHistory],
    spark_secondary_usage: dict[str, UsageHistory],
) -> str | None:
    entries = [*list(spark_primary_usage.values()), *list(spark_secondary_usage.values())]
    if not entries:
        return None
    for entry in entries:
        label = (entry.window_label or "").strip()
        if label:
            return usage_core.normalize_spark_window_label(label)
    return usage_core.normalize_spark_window_label(None)
