from __future__ import annotations

from datetime import datetime, timedelta

from app.core import usage as usage_core
from app.core.crypto import TokenEncryptor
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import BurnRateHistory, UsageHistory
from app.modules.accounts.mappers import build_account_summaries
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.schemas import (
    BurnRateSnapshotResponse,
    DashboardOverviewResponse,
    DashboardUsageWindows,
    DepletionResponse,
)
from app.modules.usage.builders import (
    build_trends_from_buckets,
    build_usage_summary_response,
    build_usage_window_response,
)
from app.modules.usage.burnrate import BurnRateSnapshot, compute_burn_rate_snapshot
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

        # Compute depletion separately for primary-window and secondary-window
        # accounts so the aggregate is not skewed by mixing different window
        # durations.  The response includes a "window" field that tells the
        # frontend which donut to render the safe-line marker on.
        normalized_primary_ids = {row.account_id for row in primary_rows}
        all_account_ids = set(primary_usage.keys()) | set(secondary_usage.keys())

        # Batch fetch: collect account IDs and determine the widest lookback
        # per window so we can issue at most 2 bulk queries instead of O(N).
        pri_fetch_ids: list[str] = []
        sec_fetch_ids: list[str] = []
        pri_since = now  # will be narrowed to the earliest needed
        sec_since = now
        # Per-account cutoffs for in-memory filtering after bulk fetch
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

        # Issue at most 2 bulk queries
        all_pri_rows = (
            await self._repo.bulk_usage_history_since(pri_fetch_ids, "primary", pri_since) if pri_fetch_ids else {}
        )
        all_sec_rows = (
            await self._repo.bulk_usage_history_since(sec_fetch_ids, "secondary", sec_since) if sec_fetch_ids else {}
        )

        # Filter in-memory to each account's actual cutoff
        primary_history: dict[str, list[UsageHistory]] = {}
        secondary_history: dict[str, list[UsageHistory]] = {}

        for account_id in all_account_ids:
            if account_id in normalized_primary_ids:
                cutoff = pri_cutoffs[account_id]
                rows = [r for r in all_pri_rows.get(account_id, []) if r.recorded_at >= cutoff]
                if rows:
                    primary_history[account_id] = rows
                if account_id in sec_cutoffs:
                    s_cutoff = sec_cutoffs[account_id]
                    s_rows = [r for r in all_sec_rows.get(account_id, []) if r.recorded_at >= s_cutoff]
                    if s_rows:
                        secondary_history[account_id] = s_rows
            elif account_id in weekly_only_ids:
                source = weekly_only_history_sources[account_id]
                if source == "primary":
                    cutoff = pri_cutoffs[account_id]
                    rows = [r for r in all_pri_rows.get(account_id, []) if r.recorded_at >= cutoff]
                else:
                    cutoff = sec_cutoffs[account_id]
                    rows = [r for r in all_sec_rows.get(account_id, []) if r.recorded_at >= cutoff]
                if rows:
                    secondary_history[account_id] = rows
            else:
                cutoff = sec_cutoffs[account_id]
                rows = [r for r in all_sec_rows.get(account_id, []) if r.recorded_at >= cutoff]
                if rows:
                    secondary_history[account_id] = rows

        pri_depletion, sec_depletion = _build_depletion_by_window(primary_history, secondary_history, now)

        additional_ts = await self._repo.latest_additional_recorded_at()
        latest_burn_rate_entry = await self._repo.latest_burn_rate_entry()
        computed_burn_rate = compute_burn_rate_snapshot(
            accounts=accounts,
            latest_primary_usage=primary_usage,
            latest_secondary_usage=secondary_usage,
            now=now,
        )
        burn_rate = _resolve_burn_rate_response(latest_burn_rate_entry, computed_burn_rate)

        return DashboardOverviewResponse(
            last_sync_at=_latest_recorded_at(primary_usage, secondary_usage, additional_ts),
            accounts=account_summaries,
            summary=summary,
            windows=windows,
            trends=trends,
            depletion_primary=pri_depletion,
            depletion_secondary=sec_depletion,
            burn_rate=burn_rate,
        )


def _build_depletion_by_window(
    primary_history: dict[str, list[UsageHistory]],
    secondary_history: dict[str, list[UsageHistory]],
    now,
) -> tuple[DepletionResponse | None, DepletionResponse | None]:
    """Compute depletion independently per window."""

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

    return _aggregate(primary_history, "primary"), _aggregate(secondary_history, "secondary")


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


def _should_use_weekly_primary_history(
    primary_entry: UsageHistory,
    secondary_entry: UsageHistory | None,
) -> bool:
    return usage_core.should_use_weekly_primary(
        _usage_history_to_window_row(primary_entry),
        _usage_history_to_window_row(secondary_entry) if secondary_entry is not None else None,
    )


def _usage_history_to_window_row(entry: UsageHistory) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )


def _latest_recorded_at(
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    additional_ts: datetime | None = None,
):
    timestamps = [
        entry.recorded_at
        for entry in list(primary_usage.values()) + list(secondary_usage.values())
        if entry.recorded_at is not None
    ]
    if additional_ts is not None:
        timestamps.append(additional_ts)
    return max(timestamps) if timestamps else None



def _resolve_burn_rate_response(
    latest_entry: BurnRateHistory | None,
    computed_snapshot: BurnRateSnapshot,
) -> BurnRateSnapshotResponse:
    if latest_entry is not None:
        return BurnRateSnapshotResponse(
            recorded_at=latest_entry.recorded_at,
            primary_projected_plus_accounts=latest_entry.primary_projected_plus_accounts,
            secondary_projected_plus_accounts=latest_entry.secondary_projected_plus_accounts,
            primary_used_plus_accounts=latest_entry.primary_used_plus_accounts,
            secondary_used_plus_accounts=latest_entry.secondary_used_plus_accounts,
            primary_window_minutes=latest_entry.primary_window_minutes,
            secondary_window_minutes=latest_entry.secondary_window_minutes,
            primary_account_count=latest_entry.primary_account_count,
            secondary_account_count=latest_entry.secondary_account_count,
            primary_max_plus_equivalent_accounts=latest_entry.primary_max_plus_equivalent_accounts,
            secondary_max_plus_equivalent_accounts=latest_entry.secondary_max_plus_equivalent_accounts,
        )

    return BurnRateSnapshotResponse(
        recorded_at=computed_snapshot.recorded_at,
        primary_projected_plus_accounts=computed_snapshot.primary.projected_plus_accounts,
        secondary_projected_plus_accounts=computed_snapshot.secondary.projected_plus_accounts,
        primary_used_plus_accounts=computed_snapshot.primary.used_plus_accounts,
        secondary_used_plus_accounts=computed_snapshot.secondary.used_plus_accounts,
        primary_window_minutes=computed_snapshot.primary.window_minutes,
        secondary_window_minutes=computed_snapshot.secondary.window_minutes,
        primary_account_count=computed_snapshot.primary.included_account_count,
        secondary_account_count=computed_snapshot.secondary.included_account_count,
        primary_max_plus_equivalent_accounts=computed_snapshot.primary.max_plus_equivalent_accounts,
        secondary_max_plus_equivalent_accounts=computed_snapshot.secondary.max_plus_equivalent_accounts,
    )
