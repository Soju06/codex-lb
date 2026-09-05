from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.types import BucketConversationAggregate, BucketModelAggregate, RequestActivityAggregate
from app.db.models import (
    Account,
    AccountLimitWarmup,
    AdditionalUsageHistory,
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
        return await self._usage_repo.positive_used_percent_deltas_by_account(
            account_windows,
            since=since,
            until=until,
        )

    async def list_logs_since(self, since: datetime) -> list[RequestLog]:
        return await self._logs_repo.list_since(since)

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

    async def latest_limit_warmups_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]:
        return await self._limit_warmup_repo.latest_by_account(account_ids)

    async def get_settings(self) -> DashboardSettings:
        return await self._settings_repo.get_or_create()
