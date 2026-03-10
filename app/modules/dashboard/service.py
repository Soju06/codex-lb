from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core import usage as usage_core
from app.core.crypto import TokenEncryptor
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import UsageHistory
from app.modules.accounts.mappers import build_account_summaries
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.schemas import (
    AdditionalQuotaResponse,
    AdditionalWindowResponse,
    DashboardOverviewResponse,
    DashboardUsageWindows,
    DepletionResponse,
)
from app.modules.usage.builders import (
    build_additional_usage_summary,
    build_trends_from_buckets,
    build_usage_summary_response,
    build_usage_window_response,
)
from app.modules.usage.depletion_service import (
    compute_aggregate_depletion,
    compute_depletion_for_account,
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

        primary_rows_raw = _rows_from_latest(primary_usage)
        secondary_rows_raw = _rows_from_latest(secondary_usage)
        primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
            primary_rows_raw,
            secondary_rows_raw,
        )

        secondary_minutes = usage_core.resolve_window_minutes("secondary", secondary_rows)

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

        primary_window_minutes = usage_core.resolve_window_minutes("primary", primary_rows)

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

        # Fetch additional usage data
        additional_quotas, additional_sync_ts = await self._build_additional_quotas()

        # Compute depletion separately for primary-window and secondary-window
        # accounts so the aggregate is not skewed by mixing different window
        # durations.  The response includes a "window" field that tells the
        # frontend which donut to render the safe-line marker on.
        normalized_primary_ids = {row.account_id for row in primary_rows}
        all_account_ids = set(primary_usage.keys()) | set(secondary_usage.keys())
        primary_history: dict[str, list[UsageHistory]] = {}
        secondary_history: dict[str, list[UsageHistory]] = {}
        for account_id in all_account_ids:
            if account_id in normalized_primary_ids:
                usage_entry = primary_usage[account_id]
                acct_window = usage_entry.window_minutes if usage_entry.window_minutes else 300
                acct_since = now - timedelta(minutes=acct_window)
                rows = await self._repo.usage_history_since(account_id, "primary", acct_since)
                if rows:
                    primary_history[account_id] = rows
            elif account_id in primary_usage:
                # Weekly-only: history is stored under window="primary" even
                # though normalization remapped the latest row to secondary.
                sec_entry = secondary_usage.get(account_id)
                acct_window = sec_entry.window_minutes if sec_entry and sec_entry.window_minutes else 10080
                acct_since = now - timedelta(minutes=acct_window)
                rows = await self._repo.usage_history_since(account_id, "primary", acct_since)
                if rows:
                    secondary_history[account_id] = rows
            else:
                sec_entry = secondary_usage[account_id]
                acct_window = sec_entry.window_minutes if sec_entry.window_minutes else 10080
                acct_since = now - timedelta(minutes=acct_window)
                rows = await self._repo.usage_history_since(account_id, "secondary", acct_since)
                if rows:
                    secondary_history[account_id] = rows

        depletion_response = _build_depletion_by_window(primary_history, secondary_history, now)

        return DashboardOverviewResponse(
            last_sync_at=_latest_recorded_at(primary_usage, secondary_usage, additional_sync_ts),
            accounts=account_summaries,
            summary=summary,
            windows=windows,
            trends=trends,
            additional_quotas=additional_quotas,
            depletion=depletion_response,
        )

    async def _build_additional_quotas(self) -> tuple[list[AdditionalQuotaResponse], list[datetime]]:
        """Fetch additional usage data and build quota responses.

        Returns the quota list and a list of recorded_at timestamps for sync tracking.
        """
        repo = self._repo
        limit_names = await repo.list_additional_limit_names()

        additional_usage_data: dict[str, dict[str, dict[str, Any]]] = {}
        sync_timestamps: list[datetime] = []
        for limit_name in limit_names:
            primary_entries = await repo.latest_additional_usage_by_account(limit_name, "primary")
            secondary_entries = await repo.latest_additional_usage_by_account(limit_name, "secondary")
            additional_usage_data[limit_name] = {
                "primary": primary_entries,
                "secondary": secondary_entries,
            }
            for entry in list(primary_entries.values()) + list(secondary_entries.values()):
                if hasattr(entry, "recorded_at") and entry.recorded_at is not None:
                    sync_timestamps.append(entry.recorded_at)

        additional_summaries = build_additional_usage_summary(additional_usage_data)

        quotas = [
            AdditionalQuotaResponse(
                limit_name=s.limit_name,
                metered_feature=s.metered_feature,
                primary_window=AdditionalWindowResponse(
                    used_percent=s.primary_window.used_percent,
                    reset_at=s.primary_window.reset_at,
                    window_minutes=s.primary_window.window_minutes,
                )
                if s.primary_window
                else None,
                secondary_window=AdditionalWindowResponse(
                    used_percent=s.secondary_window.used_percent,
                    reset_at=s.secondary_window.reset_at,
                    window_minutes=s.secondary_window.window_minutes,
                )
                if s.secondary_window
                else None,
            )
            for s in additional_summaries
        ]
        return quotas, sync_timestamps


def _build_depletion_by_window(
    primary_history: dict[str, list[UsageHistory]],
    secondary_history: dict[str, list[UsageHistory]],
    now,
) -> DepletionResponse | None:
    """Compute depletion per window and return the higher-risk result."""

    def _aggregate(history: dict[str, list[UsageHistory]], window: str):
        metrics = []
        for account_id, rows in history.items():
            m = compute_depletion_for_account(
                account_id=account_id,
                limit_name="standard",
                window=window,
                history=rows,
                now=now,
            )
            metrics.append(m)
        return compute_aggregate_depletion(metrics)

    pri_agg = _aggregate(primary_history, "primary")
    sec_agg = _aggregate(secondary_history, "secondary")

    # Pick the higher-risk window; prefer primary on tie.
    if pri_agg is not None and sec_agg is not None:
        chosen, window = (sec_agg, "secondary") if sec_agg.risk > pri_agg.risk else (pri_agg, "primary")
    elif pri_agg is not None:
        chosen, window = pri_agg, "primary"
    elif sec_agg is not None:
        chosen, window = sec_agg, "secondary"
    else:
        return None

    return DepletionResponse(
        risk=chosen.risk,
        risk_level=chosen.risk_level,
        burn_rate=chosen.burn_rate,
        safe_usage_percent=chosen.safe_usage_percent,
        projected_exhaustion_at=chosen.projected_exhaustion_at,
        seconds_until_exhaustion=chosen.seconds_until_exhaustion,
        window=window,
    )


def _rows_from_latest(latest: dict[str, UsageHistory]) -> list[UsageWindowRow]:
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


def _latest_recorded_at(
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    extra_timestamps: list[datetime] | None = None,
):
    timestamps = [
        entry.recorded_at
        for entry in list(primary_usage.values()) + list(secondary_usage.values())
        if entry.recorded_at is not None
    ]
    if extra_timestamps:
        timestamps.extend(extra_timestamps)
    return max(timestamps) if timestamps else None
