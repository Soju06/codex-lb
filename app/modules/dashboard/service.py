from __future__ import annotations

from datetime import datetime, timedelta

from app.core import usage as usage_core
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import UsageHistory
from app.modules.accounts.mappers import build_account_summaries
from app.modules.dashboard.builders import (
    build_dashboard_overview_summary,
    build_overview_timeframe,
    resolve_overview_timeframe,
)
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.schemas import (
    DashboardOverviewResponse,
    DashboardOverviewTimeframeKey,
    DashboardProjectionsResponse,
    DashboardUsageWindows,
    DepletionResponse,
)
from app.modules.dashboard.weekly_pace import build_weekly_credit_pace
from app.modules.usage.builders import (
    align_bucket_window_start,
    build_activity_summaries,
    build_trends_from_buckets,
    build_usage_window_response,
)
from app.modules.usage.depletion_service import (
    compute_aggregate_depletion,
    compute_depletion_for_account,
    filter_depletion_history_since,
    prune_depletion_cache,
)
from app.modules.usage.mappers import usage_history_to_window_row


def _parse_weekly_pace_working_days(value: str) -> set[int]:
    try:
        days = {int(part.strip()) for part in value.split(",") if part.strip()}
    except ValueError:
        return set()
    if not days or any(day < 0 or day > 6 for day in days):
        return set()
    if days == set(range(7)):
        return set()
    return days


class DashboardService:
    def __init__(self, repo: DashboardRepository) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()

    async def get_overview(
        self,
        timeframe_key: DashboardOverviewTimeframeKey = "7d",
    ) -> DashboardOverviewResponse:
        now = utcnow()
        overview_timeframe = resolve_overview_timeframe(timeframe_key)
        accounts = await self._repo.list_accounts()
        account_ids = [account.id for account in accounts]
        primary_usage = await self._repo.latest_usage_by_account("primary")
        secondary_usage = await self._repo.latest_usage_by_account("secondary")
        monthly_usage = await self._repo.latest_usage_by_account("monthly")
        limit_warmups_by_account = await self._repo.latest_limit_warmups_by_account(account_ids)

        account_summaries = sorted(
            build_account_summaries(
                accounts=accounts,
                primary_usage=primary_usage,
                secondary_usage=secondary_usage,
                monthly_usage=monthly_usage,
                limit_warmups_by_account=limit_warmups_by_account,
                encryptor=self._encryptor,
                include_auth=False,
            ),
            key=lambda a: a.capacity_credits_primary or 0,
            reverse=True,
        )

        primary_rows_raw = _rows_from_latest(primary_usage)
        secondary_rows_raw = _rows_from_latest(secondary_usage)
        primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
            primary_rows_raw,
            secondary_rows_raw,
        )

        bucket_since = now - timedelta(minutes=overview_timeframe.window_minutes)
        bucket_query_since = align_bucket_window_start(
            bucket_since,
            overview_timeframe.bucket_seconds,
        )
        bucket_rows = await self._repo.aggregate_logs_by_bucket(
            bucket_query_since,
            overview_timeframe.bucket_seconds,
        )
        trends, _, _ = build_trends_from_buckets(
            bucket_rows,
            bucket_since,
            bucket_seconds=overview_timeframe.bucket_seconds,
            bucket_count=overview_timeframe.bucket_count,
        )
        activity_aggregate = await self._repo.aggregate_activity_since(bucket_since)
        top_error = await self._repo.top_error_since(bucket_since)
        activity_metrics, activity_cost = build_activity_summaries(
            activity_aggregate,
            top_error=top_error,
        )

        summary = build_dashboard_overview_summary(
            accounts=accounts,
            primary_rows=primary_rows,
            secondary_rows=secondary_rows,
            activity_metrics=activity_metrics,
            activity_cost=activity_cost,
        )

        secondary_minutes = usage_core.resolve_window_minutes("secondary", secondary_rows)
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

        additional_ts = await self._repo.latest_additional_recorded_at()
        return DashboardOverviewResponse(
            last_sync_at=_latest_recorded_at(primary_usage, secondary_usage, monthly_usage, additional_ts),
            timeframe=build_overview_timeframe(overview_timeframe),
            accounts=account_summaries,
            summary=summary,
            windows=windows,
            trends=trends,
        )

    async def get_projections(self) -> DashboardProjectionsResponse:
        now = utcnow()
        accounts = await self._repo.list_accounts()
        primary_usage = await self._repo.latest_usage_by_account("primary")
        secondary_usage = await self._repo.latest_usage_by_account("secondary")
        monthly_usage = await self._repo.latest_usage_by_account("monthly")
        account_summaries = build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            monthly_usage=monthly_usage,
            encryptor=self._encryptor,
            include_auth=False,
        )
        primary_history, secondary_history = await _load_projection_histories(
            self._repo,
            primary_usage,
            secondary_usage,
            now,
        )
        pri_depletion, sec_depletion = _build_depletion_by_window(primary_history, secondary_history, now)
        settings = get_settings()
        dashboard_settings = await self._repo.get_settings()
        weekly_credit_pace = build_weekly_credit_pace(
            accounts=accounts,
            account_summaries=account_summaries,
            secondary_history=secondary_history,
            now=now,
            usage_refresh_interval_seconds=settings.usage_refresh_interval_seconds,
            working_days=_parse_weekly_pace_working_days(dashboard_settings.weekly_pace_working_days),
        )
        return DashboardProjectionsResponse(
            depletion_primary=pri_depletion,
            depletion_secondary=sec_depletion,
            weekly_credit_pace=weekly_credit_pace,
        )


async def _load_projection_histories(
    repo: DashboardRepository,
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    now: datetime,
) -> tuple[dict[str, list[UsageHistory]], dict[str, list[UsageHistory]]]:
    # Compute depletion separately for primary-window and secondary-window
    # accounts so the aggregate is not skewed by mixing different window durations.
    primary_rows_raw = _rows_from_latest(primary_usage)
    secondary_rows_raw = _rows_from_latest(secondary_usage)
    primary_rows, _ = usage_core.normalize_weekly_only_rows(
        primary_rows_raw,
        secondary_rows_raw,
    )
    normalized_primary_ids = {row.account_id for row in primary_rows}
    all_account_ids = set(primary_usage.keys()) | set(secondary_usage.keys())

    # Batch fetch: collect account IDs and determine the widest lookback per
    # window so we can issue at most 2 bulk queries instead of O(N).
    pri_fetch_ids: list[str] = []
    sec_fetch_ids: list[str] = []
    pri_since = now
    sec_since = now
    pri_cutoffs: dict[str, datetime] = {}
    sec_cutoffs: dict[str, datetime] = {}
    weekly_only_ids: set[str] = set()
    weekly_only_history_sources: dict[str, str] = {}

    for account_id in all_account_ids:
        if account_id in normalized_primary_ids:
            usage_entry = primary_usage[account_id]
            acct_window = usage_entry.window_minutes if usage_entry.window_minutes else 300
            acct_since = now - timedelta(minutes=acct_window)
            pri_fetch_ids.append(account_id)
            pri_cutoffs[account_id] = acct_since
            if acct_since < pri_since:
                pri_since = acct_since
            if account_id in secondary_usage:
                sec_entry = secondary_usage[account_id]
                sec_window = sec_entry.window_minutes if sec_entry.window_minutes else 10080
                s_since = now - timedelta(minutes=sec_window)
                sec_fetch_ids.append(account_id)
                sec_cutoffs[account_id] = s_since
                if s_since < sec_since:
                    sec_since = s_since
        elif account_id in primary_usage:
            weekly_only_ids.add(account_id)
            primary_entry = primary_usage[account_id]
            sec_entry = secondary_usage.get(account_id)
            use_primary_stream = _should_use_weekly_primary_history(primary_entry, sec_entry)
            weekly_only_history_sources[account_id] = "primary" if use_primary_stream else "secondary"
            current_entry = primary_entry if use_primary_stream else sec_entry
            acct_window = current_entry.window_minutes if current_entry and current_entry.window_minutes else 10080
            acct_since = now - timedelta(minutes=acct_window)
            if use_primary_stream:
                pri_fetch_ids.append(account_id)
                pri_cutoffs[account_id] = acct_since
                if acct_since < pri_since:
                    pri_since = acct_since
            else:
                sec_fetch_ids.append(account_id)
                sec_cutoffs[account_id] = acct_since
                if acct_since < sec_since:
                    sec_since = acct_since
        else:
            sec_entry = secondary_usage[account_id]
            acct_window = sec_entry.window_minutes if sec_entry.window_minutes else 10080
            acct_since = now - timedelta(minutes=acct_window)
            sec_fetch_ids.append(account_id)
            sec_cutoffs[account_id] = acct_since
            if acct_since < sec_since:
                sec_since = acct_since

    all_pri_rows = await repo.bulk_usage_history_since(pri_fetch_ids, "primary", pri_since) if pri_fetch_ids else {}
    all_sec_rows = await repo.bulk_usage_history_since(sec_fetch_ids, "secondary", sec_since) if sec_fetch_ids else {}

    primary_history: dict[str, list[UsageHistory]] = {}
    secondary_history: dict[str, list[UsageHistory]] = {}

    for account_id in all_account_ids:
        if account_id in normalized_primary_ids:
            cutoff = pri_cutoffs[account_id]
            rows = filter_depletion_history_since(all_pri_rows.get(account_id, []), cutoff)
            if rows:
                primary_history[account_id] = rows
            if account_id in sec_cutoffs:
                s_cutoff = sec_cutoffs[account_id]
                s_rows = filter_depletion_history_since(all_sec_rows.get(account_id, []), s_cutoff)
                if s_rows:
                    secondary_history[account_id] = s_rows
        elif account_id in weekly_only_ids:
            source = weekly_only_history_sources[account_id]
            if source == "primary":
                cutoff = pri_cutoffs[account_id]
                rows = filter_depletion_history_since(all_pri_rows.get(account_id, []), cutoff)
            else:
                cutoff = sec_cutoffs[account_id]
                rows = filter_depletion_history_since(all_sec_rows.get(account_id, []), cutoff)
            if rows:
                secondary_history[account_id] = rows
        else:
            cutoff = sec_cutoffs[account_id]
            rows = filter_depletion_history_since(all_sec_rows.get(account_id, []), cutoff)
            if rows:
                secondary_history[account_id] = rows

    return primary_history, secondary_history


def _build_depletion_by_window(
    primary_history: dict[str, list[UsageHistory]],
    secondary_history: dict[str, list[UsageHistory]],
    now,
) -> tuple[DepletionResponse | None, DepletionResponse | None]:
    """Compute depletion independently per window."""
    active_cache_keys = {(account_id, "standard", "primary") for account_id in primary_history}
    active_cache_keys.update((account_id, "standard", "secondary") for account_id in secondary_history)

    def _aggregate(history: dict[str, list[UsageHistory]], window: str) -> DepletionResponse | None:
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
        agg = compute_aggregate_depletion(metrics)
        if agg is None:
            return None
        return DepletionResponse(
            risk=agg.risk,
            risk_level=agg.risk_level,
            burn_rate=agg.burn_rate,
            safe_usage_percent=agg.safe_usage_percent,
            projected_exhaustion_at=agg.projected_exhaustion_at,
            seconds_until_exhaustion=agg.seconds_until_exhaustion,
        )

    primary_depletion = _aggregate(primary_history, "primary")
    secondary_depletion = _aggregate(secondary_history, "secondary")
    prune_depletion_cache(active_cache_keys)
    return primary_depletion, secondary_depletion


def _rows_from_latest(latest: dict[str, UsageHistory]) -> list[UsageWindowRow]:
    return [usage_history_to_window_row(entry) for entry in latest.values()]


def _should_use_weekly_primary_history(
    primary_entry: UsageHistory,
    secondary_entry: UsageHistory | None,
) -> bool:
    return usage_core.should_use_weekly_primary(
        usage_history_to_window_row(primary_entry),
        usage_history_to_window_row(secondary_entry) if secondary_entry is not None else None,
    )


def _latest_recorded_at(
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    monthly_usage: dict[str, UsageHistory],
    additional_ts: datetime | None = None,
):
    timestamps = [
        entry.recorded_at
        for entry in list(primary_usage.values()) + list(secondary_usage.values()) + list(monthly_usage.values())
        if entry.recorded_at is not None
    ]
    if additional_ts is not None:
        timestamps.append(additional_ts)
    return max(timestamps) if timestamps else None
