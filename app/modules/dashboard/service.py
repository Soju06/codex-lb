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
    DashboardMetricsComparison,
    DashboardMetricsComparisonPrevious,
    DashboardOverviewResponse,
    DashboardOverviewTimeframeKey,
    DashboardProjectionsResponse,
    DashboardUsageWindows,
    DepletionResponse,
    WeeklyCreditApiKeyAttribution,
    WeeklyCreditPaceResponse,
)
from app.modules.dashboard.weekly_pace import DEMAND_WINDOW, FLEET_BURN_WINDOW, build_weekly_credit_pace
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
from app.modules.usage.repository import NormalizedUsageWindow

# Newest-first per-account row bound for the projections history fetch
# (PostgreSQL; the SQLite snapshot cache keeps the shared floor). Live
# snapshot ingestion appends a usage row whenever an account's usage
# fingerprint moves, so one busy account's 7-day window can hold tens of
# thousands of rows while the consumers only read the recent tail. Rows
# older than the equal-weight floor below feed only count-decaying EWMAs
# (depletion rate, weekly-pace recent burn; alpha 0.4). The first tail row
# only seeds the EWMA, so a cap-row tail performs cap-1 updates and the
# pre-tail state's residual on the replayed rate is at most
# ``0.6**(cap-1)`` times the largest per-second sample slope: below
# ~1.1e-12 %/s even at the theoretical 100 %/s step, and far below that at
# real slopes, so the tail reproduces the full-window replay to
# floating-point noise on the rate. Fields derived from the rate inherit
# that residual scaled by their formulas (burn rate multiplies it by
# seconds-until-reset over remaining percent), and the exhaustion ETA,
# which is emitted only for a strictly positive rate, may be absent from
# the tail replay when the full replay still carries a ghost rate below
# the residual (an account flat at 100%). The EWMA advances once per
# distinct integer epoch second (``ewma_update`` skips a sample whose
# ``naive_utc_to_epoch`` equals the previous one), so rows written faster
# than one per second share an update: a tail packed into fewer distinct
# seconds than the cap — a same-second write burst older than the floor
# with no newer rows to decay it — may diverge from the full replay.
# Every equal-weight consumer is protected by the floor instead.
_PROJECTION_EWMA_TAIL_ROWS = 64


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
        conversation_bucket_rows = await self._repo.aggregate_conversations_by_bucket(
            bucket_query_since,
            overview_timeframe.bucket_seconds,
        )
        trends, _, _ = build_trends_from_buckets(
            bucket_rows,
            bucket_since,
            bucket_seconds=overview_timeframe.bucket_seconds,
            bucket_count=overview_timeframe.bucket_count,
            conversation_rows=conversation_bucket_rows,
        )
        previous_window_start = bucket_since - timedelta(minutes=overview_timeframe.window_minutes)
        activity_aggregate = await self._repo.aggregate_activity_between(bucket_since, now)
        previous_activity_aggregate = await self._repo.aggregate_activity_between(previous_window_start, bucket_since)
        top_error = await self._repo.top_error_between(bucket_since, now)
        earliest_activity_at = await self._repo.earliest_activity_at()
        activity_metrics, activity_cost = build_activity_summaries(
            activity_aggregate,
            top_error=top_error,
        )
        previous_metrics, previous_cost = build_activity_summaries(previous_activity_aggregate)
        comparison = DashboardMetricsComparison(
            canCompare=earliest_activity_at is not None and earliest_activity_at <= previous_window_start,
            previous=DashboardMetricsComparisonPrevious(
                requests=previous_metrics.requests or 0,
                tokens=previous_metrics.tokens or 0,
                costUsd=previous_cost.total_usd,
            ),
        )

        summary = build_dashboard_overview_summary(
            accounts=accounts,
            primary_rows=primary_rows,
            secondary_rows=secondary_rows,
            activity_metrics=activity_metrics,
            activity_cost=activity_cost,
            comparison=comparison,
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

        dashboard_settings = await self._repo.get_settings()
        _, secondary_history = await _load_projection_histories(
            self._repo,
            primary_usage,
            secondary_usage,
            now,
            smoothing_window_minutes=dashboard_settings.weekly_pace_smoothing_minutes,
            include_primary=False,
        )
        settings = get_settings()
        trailing_demand = await self._repo.positive_used_percent_deltas_by_account(
            _weekly_history_windows(primary_usage, secondary_usage),
            since=now - DEMAND_WINDOW,
            until=now,
        )
        weekly_credit_pace = build_weekly_credit_pace(
            accounts=accounts,
            account_summaries=account_summaries,
            secondary_history=secondary_history,
            now=now,
            usage_refresh_interval_seconds=settings.usage_refresh_interval_seconds,
            trailing_demand_used_percent_by_account=trailing_demand,
            working_days=_parse_weekly_pace_working_days(dashboard_settings.weekly_pace_working_days),
            smoothing_window_minutes=dashboard_settings.weekly_pace_smoothing_minutes,
        )
        await _attach_top_api_keys(self._repo, weekly_credit_pace, now)

        additional_ts = await self._repo.latest_additional_recorded_at()
        return DashboardOverviewResponse(
            last_sync_at=_latest_recorded_at(primary_usage, secondary_usage, monthly_usage, additional_ts),
            timeframe=build_overview_timeframe(overview_timeframe),
            accounts=account_summaries,
            summary=summary,
            windows=windows,
            trends=trends,
            weekly_credit_pace=weekly_credit_pace,
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
        dashboard_settings = await self._repo.get_settings()
        primary_history, secondary_history = await _load_projection_histories(
            self._repo,
            primary_usage,
            secondary_usage,
            now,
            smoothing_window_minutes=dashboard_settings.weekly_pace_smoothing_minutes,
        )
        pri_depletion, sec_depletion = _build_depletion_by_window(primary_history, secondary_history, now)
        settings = get_settings()
        trailing_demand = await self._repo.positive_used_percent_deltas_by_account(
            _weekly_history_windows(primary_usage, secondary_usage),
            since=now - DEMAND_WINDOW,
            until=now,
        )
        weekly_credit_pace = build_weekly_credit_pace(
            accounts=accounts,
            account_summaries=account_summaries,
            secondary_history=secondary_history,
            now=now,
            usage_refresh_interval_seconds=settings.usage_refresh_interval_seconds,
            trailing_demand_used_percent_by_account=trailing_demand,
            working_days=_parse_weekly_pace_working_days(dashboard_settings.weekly_pace_working_days),
            smoothing_window_minutes=dashboard_settings.weekly_pace_smoothing_minutes,
        )
        await _attach_top_api_keys(self._repo, weekly_credit_pace, now)
        return DashboardProjectionsResponse(
            depletion_primary=pri_depletion,
            depletion_secondary=sec_depletion,
            weekly_credit_pace=weekly_credit_pace,
        )


async def _attach_top_api_keys(
    repo: DashboardRepository,
    weekly_credit_pace: WeeklyCreditPaceResponse | None,
    now: datetime,
) -> None:
    if weekly_credit_pace is None:
        return
    rows = await repo.top_api_key_attribution_since(now - timedelta(hours=2), now=now)
    weekly_credit_pace.top_api_keys = [
        WeeklyCreditApiKeyAttribution(
            api_key_id=row.api_key_id,
            name=row.name,
            requests=row.requests,
            billable_tokens=row.billable_tokens,
            cached_tokens=row.cached_tokens,
            dominant_model=row.dominant_model,
        )
        for row in rows
    ]


async def _load_projection_histories(
    repo: DashboardRepository,
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    now: datetime,
    *,
    smoothing_window_minutes: int,
    include_primary: bool = True,
) -> tuple[dict[str, list[UsageHistory]], dict[str, list[UsageHistory]]]:
    # Compute depletion separately for primary-window and secondary-window
    # accounts so the aggregate is not skewed by mixing different window durations.
    # Callers that only consume the secondary half (the overview weekly pace)
    # pass include_primary=False to skip the primary bulk fetch; weekly-only
    # accounts whose history source is the primary stream are still fetched
    # because their rows feed secondary_history.
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
            if include_primary:
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

    # The weekly-pace smoothing mean (configured window) and fleet burn
    # (fixed 3h window) weigh every sample in their windows equally, so rows
    # inside the wider of the two are exempt from the row cap (ingestion
    # writes per fingerprint change; a burst could otherwise out-write the
    # cap and silently shift those values). The floor applies to both
    # fetches: weekly-only accounts sourced from the primary stream feed
    # ``secondary_history`` too, so the primary fetch cannot go floorless.
    uncapped_floor = now - max(timedelta(minutes=smoothing_window_minutes), FLEET_BURN_WINDOW)
    all_pri_rows = (
        await repo.bulk_usage_history_since(
            pri_fetch_ids,
            "primary",
            pri_since,
            cutoffs=pri_cutoffs,
            per_account_row_cap=_PROJECTION_EWMA_TAIL_ROWS,
            uncapped_recent_floor=uncapped_floor,
        )
        if pri_fetch_ids
        else {}
    )
    all_sec_rows = (
        await repo.bulk_usage_history_since(
            sec_fetch_ids,
            "secondary",
            sec_since,
            cutoffs=sec_cutoffs,
            per_account_row_cap=_PROJECTION_EWMA_TAIL_ROWS,
            uncapped_recent_floor=uncapped_floor,
        )
        if sec_fetch_ids
        else {}
    )

    primary_history: dict[str, list[UsageHistory]] = {}
    secondary_history: dict[str, list[UsageHistory]] = {}

    for account_id in all_account_ids:
        if account_id in normalized_primary_ids:
            if include_primary:
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


def _weekly_history_windows(
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
) -> dict[str, NormalizedUsageWindow]:
    primary_rows, _ = usage_core.normalize_weekly_only_rows(
        _rows_from_latest(primary_usage),
        _rows_from_latest(secondary_usage),
    )
    normalized_primary_ids = {row.account_id for row in primary_rows}
    windows: dict[str, NormalizedUsageWindow] = {}
    for account_id in set(primary_usage) | set(secondary_usage):
        if account_id in normalized_primary_ids:
            if account_id in secondary_usage:
                windows[account_id] = "secondary"
        elif account_id in primary_usage:
            windows[account_id] = (
                "primary"
                if _should_use_weekly_primary_history(primary_usage[account_id], secondary_usage.get(account_id))
                else "secondary"
            )
        else:
            windows[account_id] = "secondary"
    return windows


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
