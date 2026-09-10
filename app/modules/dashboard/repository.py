from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.types import BucketConversationAggregate, BucketModelAggregate, RequestActivityAggregate
from app.db.models import (
    Account,
    AccountLimitWarmup,
    ApiKey,
    DashboardSettings,
    RequestLog,
    UsageHistory,
)
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.repository import (
    AdditionalUsageRepository,
    NormalizedUsageWindow,
    UsageHistorySnapshot,
    UsageRepository,
)

# The weekly credit pace's trailing-demand aggregate (a LAG window over seven
# days of usage_history per account) is re-run by every /dashboard/overview
# and /dashboard/projections poll although the displayed pace tolerates short
# staleness. Cache it per account->window signature for a small fixed TTL,
# mirroring the request-log COUNT cache (app/modules/request_logs/repository.py);
# the test suite patches the TTL to 0 so pace figures stay exact within a test.
_TRAILING_DEMAND_TTL_SECONDS = 60.0
_TRAILING_DEMAND_MAX_ENTRIES = 16
# (window span in whole seconds, sorted account -> window pairs)
_TrailingDemandKey = tuple[int, tuple[tuple[str, NormalizedUsageWindow], ...]]
_trailing_demand_cache: dict[_TrailingDemandKey, tuple[dict[str, float], float]] = {}


def _clear_trailing_demand_cache() -> None:
    _trailing_demand_cache.clear()


def _cached_trailing_demand(key: _TrailingDemandKey) -> dict[str, float] | None:
    entry = _trailing_demand_cache.get(key)
    if entry is None:
        return None
    deltas, expires_at = entry
    if time.monotonic() >= expires_at:
        _trailing_demand_cache.pop(key, None)
        return None
    return deltas


def _store_trailing_demand(key: _TrailingDemandKey, deltas: dict[str, float], ttl_seconds: float) -> None:
    if len(_trailing_demand_cache) >= _TRAILING_DEMAND_MAX_ENTRIES:
        oldest = min(_trailing_demand_cache, key=lambda existing: _trailing_demand_cache[existing][1])
        _trailing_demand_cache.pop(oldest, None)
    _trailing_demand_cache[key] = (deltas, time.monotonic() + ttl_seconds)


@dataclass(frozen=True, slots=True)
class ApiKeyAttributionRow:
    api_key_id: str | None
    name: str
    requests: int
    billable_tokens: int
    cached_tokens: int
    dominant_model: str


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._accounts_repo = AccountsRepository(session)
        self._usage_repo = UsageRepository(session)
        self._logs_repo = RequestLogsRepository(session)
        self._additional_usage_repo = AdditionalUsageRepository(session)
        self._limit_warmup_repo = LimitWarmupRepository(session)
        self._settings_repo = SettingsRepository(session)

    async def list_accounts(self) -> list[Account]:
        return await self._accounts_repo.list_accounts()

    async def latest_usage_by_account(self, window: str) -> dict[str, UsageHistory]:
        return await self._usage_repo.latest_by_account(window=window)

    async def bulk_usage_history_since(
        self,
        account_ids: list[str],
        window: str,
        since: datetime,
        *,
        cutoffs: dict[str, datetime] | None = None,
        per_account_row_cap: int | None = None,
        uncapped_recent_floor: datetime | None = None,
    ) -> dict[str, list[UsageHistorySnapshot]]:
        return await self._usage_repo.bulk_history_since(
            account_ids,
            window,
            since,
            cutoffs=cutoffs,
            per_account_row_cap=per_account_row_cap,
            uncapped_recent_floor=uncapped_recent_floor,
        )

    async def latest_window_minutes(self, window: str) -> int | None:
        return await self._usage_repo.latest_window_minutes(window)

    async def positive_used_percent_deltas_by_account(
        self,
        account_windows: Mapping[str, NormalizedUsageWindow],
        *,
        since: datetime,
        until: datetime,
    ) -> dict[str, float]:
        """Trailing positive used-percent deltas, memoized per window span and account->window signature.

        The key carries the span ``until - since`` but not the bounds themselves:
        both dashboard callers pass a trailing window anchored at ``now``, so
        within the TTL the window drifts by at most the TTL and the figure is
        display-only. A different span, account set or window mapping is its
        own key.
        """
        ttl_seconds = _TRAILING_DEMAND_TTL_SECONDS
        cache_key: _TrailingDemandKey = (
            round((until - since).total_seconds()),
            tuple(sorted(account_windows.items())),
        )
        if ttl_seconds > 0:
            cached = _cached_trailing_demand(cache_key)
            if cached is not None:
                return dict(cached)
        deltas = await self._usage_repo.positive_used_percent_deltas_by_account(
            account_windows,
            since=since,
            until=until,
        )
        if ttl_seconds > 0:
            _store_trailing_demand(cache_key, deltas, ttl_seconds)
        return dict(deltas)

    async def aggregate_logs_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
    ) -> list[BucketModelAggregate]:
        return await self._logs_repo.aggregate_by_bucket(since, bucket_seconds)

    async def aggregate_conversations_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
    ) -> list[BucketConversationAggregate]:
        return await self._logs_repo.aggregate_conversations_by_bucket(since, bucket_seconds)

    async def aggregate_activity_since(self, since: datetime) -> RequestActivityAggregate:
        return await self._logs_repo.aggregate_activity_since(since)

    async def aggregate_activity_between(
        self,
        since: datetime,
        until: datetime,
    ) -> RequestActivityAggregate:
        return await self._logs_repo.aggregate_activity_between(since, until)

    async def top_error_since(self, since: datetime) -> str | None:
        return await self._logs_repo.top_error_since(since)

    async def top_error_between(self, since: datetime, until: datetime) -> str | None:
        return await self._logs_repo.top_error_between(since, until)

    async def earliest_activity_at(self) -> datetime | None:
        return await self._logs_repo.earliest_activity_at()

    async def top_api_key_attribution_since(
        self,
        since: datetime,
        *,
        now: datetime,
        per_metric_limit: int = 3,
    ) -> list[ApiKeyAttributionRow]:
        key_name = func.coalesce(func.nullif(ApiKey.name, ""), "(unnamed)")
        billable_tokens = func.coalesce(RequestLog.input_tokens, 0) + func.coalesce(
            RequestLog.output_tokens, RequestLog.reasoning_tokens, 0
        )
        grouped_models = (
            select(
                RequestLog.api_key_id.label("api_key_id"),
                key_name.label("name"),
                RequestLog.model.label("model"),
                func.count(RequestLog.id).label("requests"),
                func.coalesce(func.sum(billable_tokens), 0).label("billable_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_tokens"),
            )
            .select_from(RequestLog)
            .outerjoin(ApiKey, ApiKey.id == RequestLog.api_key_id)
            .where(
                RequestLog.requested_at >= since,
                RequestLog.requested_at <= now,
                RequestLog.deleted_at.is_(None),
                # Warmup probes are internal traffic and must not surface as
                # top consumers, matching the request-log usage queries.
                RequestLogsRepository._exclude_warmup_clause(),
            )
            .group_by(RequestLog.api_key_id, key_name, RequestLog.model)
            .cte("weekly_pace_key_models")
        )
        ranked_models = select(
            grouped_models,
            func.row_number()
            .over(
                partition_by=grouped_models.c.api_key_id,
                order_by=(
                    grouped_models.c.requests.desc(),
                    grouped_models.c.billable_tokens.desc(),
                    grouped_models.c.model.asc(),
                ),
            )
            .label("model_rank"),
        ).cte("weekly_pace_ranked_models")
        key_totals = (
            select(
                ranked_models.c.api_key_id,
                func.max(ranked_models.c.name).label("name"),
                func.sum(ranked_models.c.requests).label("requests"),
                func.sum(ranked_models.c.billable_tokens).label("billable_tokens"),
                func.sum(ranked_models.c.cached_tokens).label("cached_tokens"),
                func.max(
                    case(
                        (ranked_models.c.model_rank == 1, ranked_models.c.model),
                        else_=None,
                    )
                ).label("dominant_model"),
            )
            .group_by(ranked_models.c.api_key_id)
            .cte("weekly_pace_key_totals")
        )
        top_by_requests = (
            select(key_totals)
            .order_by(
                key_totals.c.requests.desc(),
                key_totals.c.billable_tokens.desc(),
                key_totals.c.api_key_id.asc(),
            )
            .limit(per_metric_limit)
            .subquery("weekly_pace_top_requests")
        )
        top_by_billable = (
            select(key_totals)
            .order_by(
                key_totals.c.billable_tokens.desc(),
                key_totals.c.requests.desc(),
                key_totals.c.api_key_id.asc(),
            )
            .limit(per_metric_limit)
            .subquery("weekly_pace_top_billable")
        )
        candidates = union_all(
            select(top_by_requests),
            select(top_by_billable),
        ).cte("weekly_pace_key_candidates")
        statement = (
            select(
                candidates.c.api_key_id,
                func.max(candidates.c.name).label("name"),
                func.max(candidates.c.requests).label("requests"),
                func.max(candidates.c.billable_tokens).label("billable_tokens"),
                func.max(candidates.c.cached_tokens).label("cached_tokens"),
                func.max(candidates.c.dominant_model).label("dominant_model"),
            )
            .group_by(candidates.c.api_key_id)
            .order_by(
                func.max(candidates.c.requests).desc(),
                func.max(candidates.c.billable_tokens).desc(),
                candidates.c.api_key_id.asc(),
            )
            .limit(per_metric_limit * 2)
        )
        rows = (await self._session.execute(statement)).all()
        return [
            ApiKeyAttributionRow(
                api_key_id=str(row.api_key_id) if row.api_key_id is not None else None,
                name=str(row.name),
                requests=int(row.requests),
                billable_tokens=int(row.billable_tokens),
                cached_tokens=int(row.cached_tokens),
                dominant_model=str(row.dominant_model),
            )
            for row in rows
        ]

    async def latest_additional_recorded_at(self) -> datetime | None:
        return await self._additional_usage_repo.latest_recorded_at()

    async def latest_limit_warmups_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]:
        return await self._limit_warmup_repo.latest_by_account(account_ids)

    async def get_settings(self) -> DashboardSettings:
        return await self._settings_repo.get_or_create()
