from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import cast as typing_cast

import anyio
from sqlalchemy import Integer, String, and_, case, cast, func, literal_column, or_, select
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.core.usage.logs import RequestLogLike, calculated_cost_from_log
from app.core.usage.types import (
    BucketConversationAggregate,
    BucketModelAggregate,
    RequestActivityAggregate,
    UsageSummaryLogsAggregate,
)
from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, AccountUsageRollupState, ApiKey, RequestKind, RequestLog, RequestUsageHourlyRollup
from app.db.session import sqlite_writer_section
from app.modules.accounts.usage_rollup import lock_fold_state
from app.modules.accounts.usage_time_rollup import (
    HOURLY_BUCKET_SECONDS,
    WARMUP_REQUEST_KINDS,
    floor_to_hour,
    from_dimension,
)
from app.modules.accounts.usage_time_rollup_read import (
    RawWindow,
    earliest_hourly_bucket_at,
    raw_windows_clause,
    read_errors_window,
    read_hourly_window,
)


@dataclass(frozen=True, slots=True)
class _RequestLogFilters:
    conditions: list
    needs_related_search_joins: bool


@dataclass(frozen=True, slots=True)
class RequestLogsResult:
    logs: list[RequestLog]
    total: int
    aggregated_cost_usd: float | None = None


# The exact COUNT(*) behind the request-log listing's "X-Y of N" scans the
# whole filtered set on PostgreSQL; the dashboard re-runs it on every 30s
# poll and every pagination click even though the displayed total is
# tolerant of short staleness. Cache it per filter signature for a small
# fixed TTL (issue #1340 / PRINCIPLES.md P2; the test suite patches the
# TTL to 0 so totals stay exact within a test).
_COUNT_CACHE_TTL_SECONDS = 30.0
_COUNT_CACHE_MAX_ENTRIES = 256
_CONVERSATION_WHITESPACE = " \t\n\v\f\r"
_recent_count_cache: dict[tuple, tuple[int, float]] = {}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_conversation_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip(_CONVERSATION_WHITESPACE)
    return normalized or None


def _clear_recent_count_cache() -> None:
    _recent_count_cache.clear()


def _cached_recent_count(key: tuple) -> int | None:
    entry = _recent_count_cache.get(key)
    if entry is None:
        return None
    total, expires_at = entry
    if time.monotonic() >= expires_at:
        _recent_count_cache.pop(key, None)
        return None
    return total


def _store_recent_count(key: tuple, total: int, ttl_seconds: float) -> None:
    if len(_recent_count_cache) >= _COUNT_CACHE_MAX_ENTRIES:
        oldest = min(_recent_count_cache, key=lambda existing: _recent_count_cache[existing][1])
        _recent_count_cache.pop(oldest, None)
    _recent_count_cache[key] = (total, time.monotonic() + ttl_seconds)


@dataclass(frozen=True, slots=True)
class PreviousResponseOwnerRecord:
    account_id: str
    requested_at: datetime | None
    session_id: str | None


@dataclass(frozen=True, slots=True)
class ConversationListSummary:
    conversation_id: str
    last_requested_at: datetime
    account_count: int
    total_tokens: int
    cached_input_tokens: int | None
    cost_usd: float


@dataclass(frozen=True, slots=True)
class ConversationFacet:
    conversation_id: str
    value: str
    request_count: int


@dataclass(frozen=True, slots=True)
class ConversationListResult:
    summaries: list[ConversationListSummary]
    account_facets: list[ConversationFacet]
    api_key_facets: list[ConversationFacet]
    model_facets: list[ConversationFacet]
    total: int


@dataclass(frozen=True, slots=True)
class ConversationModelStatRow:
    model: str
    reasoning_effort: str | None
    request_count: int
    total_elapsed_ms: int
    input_tokens: int
    cached_input_tokens: int | None
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True, slots=True)
class ConversationDetailsResult:
    conversation_id: str
    started_at: datetime
    last_requested_at: datetime
    account_count: int
    total_elapsed_ms: int
    useragent_group: str | None
    model_stats: list[ConversationModelStatRow]


class RequestLogsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _exclude_warmup_clause() -> ColumnElement[bool]:
        return RequestLog.request_kind.not_in((RequestKind.WARMUP.value, "limit_warmup"))

    @staticmethod
    def _conversation_id_expr() -> ColumnElement:
        trimmed = func.ltrim(
            func.rtrim(RequestLog.conversation_id, _CONVERSATION_WHITESPACE),
            _CONVERSATION_WHITESPACE,
        )
        return func.nullif(trimmed, "")

    def _conversation_output_expr(self) -> ColumnElement:
        return func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)

    def _conversation_cached_expr(self) -> ColumnElement:
        dialect = self._session.get_bind().dialect.name
        least = func.least if dialect == "postgresql" else func.min
        greatest = func.greatest if dialect == "postgresql" else func.max
        return case(
            (RequestLog.cached_input_tokens.is_(None), None),
            (RequestLog.input_tokens.is_(None), greatest(0, RequestLog.cached_input_tokens)),
            else_=greatest(0, least(RequestLog.cached_input_tokens, RequestLog.input_tokens)),
        )

    def _conversation_conditions(self) -> list[ColumnElement[bool]]:
        return [
            RequestLog.deleted_at.is_(None),
            self._exclude_warmup_clause(),
            self._conversation_id_expr().is_not(None),
        ]

    def _reasoning_effort_sort_key(self) -> list[ColumnElement]:
        rank = case(
            (RequestLog.reasoning_effort.is_(None), 0),
            (RequestLog.reasoning_effort == "", 1),
            else_=2,
        )
        return [rank, func.coalesce(RequestLog.reasoning_effort, "")]

    async def list_conversations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        since: datetime | None = None,
    ) -> ConversationListResult:
        conversation_id = self._conversation_id_expr()
        conditions = self._conversation_conditions()
        if search and search.strip():
            pattern = f"%{_escape_like(search.strip())}%"
            matching_ids = (
                select(conversation_id.label("conversation_id"))
                .where(
                    *conditions,
                    or_(
                        conversation_id.ilike(pattern, escape="\\"),
                        RequestLog.useragent_group.ilike(pattern, escape="\\"),
                    ),
                )
                .distinct()
                .subquery()
            )
            conditions = [*conditions, conversation_id.in_(select(matching_ids.c.conversation_id))]

        output = self._conversation_output_expr()
        cached = self._conversation_cached_expr()
        summary_stmt = (
            select(
                conversation_id.label("conversation_id"),
                func.max(RequestLog.requested_at).label("last_requested_at"),
                func.count(func.distinct(RequestLog.account_id)).label("account_count"),
                func.coalesce(func.sum(func.coalesce(RequestLog.input_tokens, 0) + output), 0).label("total_tokens"),
                func.sum(cached).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            )
            .where(*conditions)
            .group_by(conversation_id)
        )
        if since is not None:
            summary_stmt = summary_stmt.having(func.min(RequestLog.requested_at) >= since)
        summary_subquery = summary_stmt.subquery()
        ttl_seconds = _COUNT_CACHE_TTL_SECONDS
        if ttl_seconds <= 0:
            total = int((await self._session.execute(select(func.count()).select_from(summary_subquery))).scalar_one())
        else:
            cache_key = (search, since)
            total = _cached_recent_count(cache_key)
            if total is None:
                total = int(
                    (await self._session.execute(select(func.count()).select_from(summary_subquery))).scalar_one()
                )
                _store_recent_count(cache_key, total, ttl_seconds)
        page_rows = (
            await self._session.execute(
                select(summary_subquery)
                .order_by(summary_subquery.c.last_requested_at.desc(), summary_subquery.c.conversation_id.asc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        summaries = [
            ConversationListSummary(
                conversation_id=row.conversation_id,
                last_requested_at=row.last_requested_at,
                account_count=int(row.account_count),
                total_tokens=int(row.total_tokens),
                cached_input_tokens=(int(row.cached_input_tokens) if row.cached_input_tokens is not None else None),
                cost_usd=float(row.cost_usd or 0.0),
            )
            for row in page_rows
        ]

        page_ids = [summary.conversation_id for summary in summaries]
        account_facets: list[ConversationFacet] = []
        api_key_facets: list[ConversationFacet] = []
        model_facets: list[ConversationFacet] = []
        if page_ids:
            account_facets = await self._conversation_facets(conditions, page_ids, RequestLog.account_id)
            api_key_facets = await self._conversation_facets(conditions, page_ids, RequestLog.api_key_id)
            model_facets = await self._conversation_facets(conditions, page_ids, RequestLog.model)
        return ConversationListResult(
            summaries=summaries,
            account_facets=account_facets,
            api_key_facets=api_key_facets,
            model_facets=model_facets,
            total=total,
        )

    async def _conversation_facets(
        self,
        conditions: list[ColumnElement[bool]],
        page_ids: list[str],
        value_column: InstrumentedAttribute[str | None] | InstrumentedAttribute[str],
    ) -> list[ConversationFacet]:
        conversation_id = self._conversation_id_expr()
        facet_conditions = [*conditions, conversation_id.in_(page_ids), value_column.is_not(None)]
        if getattr(value_column, "key", None) == RequestLog.api_key_id.key:
            facet_conditions.append(value_column.in_(select(ApiKey.id)))
        stmt = (
            select(
                conversation_id.label("conversation_id"),
                value_column.label("value"),
                func.count().label("request_count"),
            )
            .where(*facet_conditions)
            .group_by(conversation_id, value_column)
            .order_by(
                conversation_id.asc(),
                func.count().desc(),
                func.max(RequestLog.requested_at).desc(),
                value_column.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            ConversationFacet(
                conversation_id=row.conversation_id,
                value=row.value,
                request_count=int(row.request_count),
            )
            for row in rows
        ]

    async def get_conversation_details(self, conversation_id: str) -> ConversationDetailsResult | None:
        target = _normalize_conversation_id(conversation_id)
        if not target:
            return None
        normalized_id = self._conversation_id_expr()
        conditions = [*self._conversation_conditions(), normalized_id == target]
        summary = (
            await self._session.execute(
                select(
                    func.min(RequestLog.requested_at).label("started_at"),
                    func.max(RequestLog.requested_at).label("last_requested_at"),
                    func.count(func.distinct(RequestLog.account_id)).label("account_count"),
                    func.coalesce(func.sum(func.coalesce(RequestLog.latency_ms, 0)), 0).label("total_elapsed_ms"),
                ).where(*conditions)
            )
        ).one()
        if summary.started_at is None:
            return None

        dominant = (
            await self._session.execute(
                select(RequestLog.useragent_group)
                .where(*conditions, RequestLog.useragent_group.is_not(None))
                .group_by(RequestLog.useragent_group)
                .order_by(
                    func.count().desc(),
                    func.max(RequestLog.requested_at).desc(),
                    RequestLog.useragent_group.asc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        output = self._conversation_output_expr()
        cached = self._conversation_cached_expr()
        model_rows = (
            await self._session.execute(
                select(
                    RequestLog.model.label("model"),
                    RequestLog.reasoning_effort.label("reasoning_effort"),
                    func.count().label("request_count"),
                    func.coalesce(func.sum(func.coalesce(RequestLog.latency_ms, 0)), 0).label("total_elapsed_ms"),
                    func.coalesce(func.sum(func.coalesce(RequestLog.input_tokens, 0)), 0).label("input_tokens"),
                    func.sum(cached).label("cached_input_tokens"),
                    func.coalesce(func.sum(output), 0).label("output_tokens"),
                    func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                )
                .where(*conditions)
                .group_by(RequestLog.model, RequestLog.reasoning_effort)
                .order_by(
                    func.count().desc(),
                    func.max(RequestLog.requested_at).desc(),
                    RequestLog.model.asc(),
                    *self._reasoning_effort_sort_key(),
                )
            )
        ).all()
        return ConversationDetailsResult(
            conversation_id=target,
            started_at=summary.started_at,
            last_requested_at=summary.last_requested_at,
            account_count=int(summary.account_count),
            total_elapsed_ms=int(summary.total_elapsed_ms),
            useragent_group=dominant,
            model_stats=[
                ConversationModelStatRow(
                    model=row.model,
                    reasoning_effort=row.reasoning_effort,
                    request_count=int(row.request_count),
                    total_elapsed_ms=int(row.total_elapsed_ms),
                    input_tokens=int(row.input_tokens),
                    cached_input_tokens=(int(row.cached_input_tokens) if row.cached_input_tokens is not None else None),
                    output_tokens=int(row.output_tokens),
                    cost_usd=float(row.cost_usd or 0.0),
                )
                for row in model_rows
            ],
        )

    def _bucket_epoch_expr(self, bucket_seconds: int) -> ColumnElement:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            return func.floor(func.extract("epoch", RequestLog.requested_at) / bucket_seconds) * bucket_seconds
        # Use explicit integer division for SQLite: CAST(epoch / N AS INTEGER) * N
        epoch_col = cast(func.strftime("%s", RequestLog.requested_at), Integer)
        return cast(epoch_col / bucket_seconds, Integer) * bucket_seconds

    async def list_since(self, since: datetime) -> list[RequestLog]:
        result = await self._session.execute(
            select(RequestLog).where(
                RequestLog.requested_at >= since,
                self._exclude_warmup_clause(),
            )
        )
        return list(result.scalars().all())

    async def find_latest_owner_record_for_response_id(
        self,
        *,
        response_id: str,
        api_key_id: str | None,
        session_id: str | None = None,
    ) -> PreviousResponseOwnerRecord | None:
        response_id_value = response_id.strip()
        if not response_id_value:
            return None

        base_conditions = [
            RequestLog.request_id == response_id_value,
            RequestLog.status == "success",
            RequestLog.account_id.is_not(None),
        ]
        if api_key_id is not None:
            base_conditions.append(RequestLog.api_key_id == api_key_id)

        async def _lookup_owner_record(
            conditions: list[ColumnElement[bool]],
        ) -> PreviousResponseOwnerRecord | None:
            stmt = (
                select(RequestLog.account_id, RequestLog.requested_at, RequestLog.session_id)
                .where(and_(*conditions))
                .order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
                .limit(1)
            )
            result = await self._session.execute(stmt)
            row = result.one_or_none()
            if row is None:
                return None
            account_id, requested_at, owner_session_id = row
            if not isinstance(account_id, str):
                return None
            stripped = account_id.strip()
            if not stripped:
                return None
            normalized_owner_session_id = (
                owner_session_id.strip() if isinstance(owner_session_id, str) and owner_session_id.strip() else None
            )
            return PreviousResponseOwnerRecord(
                account_id=stripped,
                requested_at=requested_at if isinstance(requested_at, datetime) else None,
                session_id=normalized_owner_session_id,
            )

        session_id_value = session_id.strip() if isinstance(session_id, str) else ""
        if session_id_value:
            scoped_owner = await _lookup_owner_record([*base_conditions, RequestLog.session_id == session_id_value])
            if scoped_owner is not None:
                return scoped_owner

        return await _lookup_owner_record(base_conditions)

    async def find_latest_account_id_for_response_id(
        self,
        *,
        response_id: str,
        api_key_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        owner = await self.find_latest_owner_record_for_response_id(
            response_id=response_id,
            api_key_id=api_key_id,
            session_id=session_id,
        )
        return owner.account_id if owner is not None else None

    async def aggregate_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
    ) -> list[BucketModelAggregate]:
        # Folded history comes from the hourly rollups; only the un-folded
        # complement scans raw request_logs. Every display bucket the
        # dashboard uses (3600/21600/86400) is a whole multiple of the rollup
        # hour, so a folded bucket is never split across the merge; any other
        # granularity degrades to the full raw scan.
        merged: dict[tuple[int, str, str | None], list[float]] = {}

        def _add(key: tuple[int, str, str | None], values: tuple[int, int, int, int, int, int, float]) -> None:
            entry = merged.setdefault(key, [0, 0, 0, 0, 0, 0, 0.0])
            for index, value in enumerate(values):
                entry[index] += value

        raw_windows: list[RawWindow] = [(since, None)]
        if bucket_seconds > 0 and bucket_seconds % HOURLY_BUCKET_SECONDS == 0:
            rollup_rows, raw_windows = await read_hourly_window(
                self._session,
                since,
                filters=(RequestUsageHourlyRollup.request_kind.not_in(WARMUP_REQUEST_KINDS),),
            )
            for rollup in rollup_rows:
                _add(
                    (
                        rollup.bucket_epoch // bucket_seconds * bucket_seconds,
                        rollup.model,
                        from_dimension(rollup.service_tier),
                    ),
                    (
                        rollup.request_count,
                        rollup.error_count,
                        rollup.input_tokens,
                        rollup.output_tokens,
                        rollup.cached_input_tokens,
                        rollup.reasoning_tokens,
                        rollup.cost_usd,
                    ),
                )
        if raw_windows:
            bucket_col = self._bucket_epoch_expr(bucket_seconds).label("bucket_epoch")
            stmt = (
                select(
                    bucket_col,
                    RequestLog.model,
                    RequestLog.service_tier,
                    func.count().label("request_count"),
                    func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)).label("error_count"),
                    func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                    func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                    func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                    func.coalesce(func.sum(RequestLog.reasoning_tokens), 0).label("reasoning_tokens"),
                    func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                )
                .where(raw_windows_clause(raw_windows))
                .where(self._exclude_warmup_clause())
                .group_by(bucket_col, RequestLog.model, RequestLog.service_tier)
            )
            for row in (await self._session.execute(stmt)).all():
                _add(
                    (int(row.bucket_epoch), row.model, row.service_tier),
                    (
                        int(row.request_count),
                        int(row.error_count),
                        int(row.input_tokens),
                        int(row.output_tokens),
                        int(row.cached_input_tokens),
                        int(row.reasoning_tokens),
                        float(row.cost_usd or 0.0),
                    ),
                )
        return [
            BucketModelAggregate(
                bucket_epoch=key[0],
                model=key[1],
                service_tier=key[2],
                request_count=int(entry[0]),
                error_count=int(entry[1]),
                input_tokens=int(entry[2]),
                output_tokens=int(entry[3]),
                cached_input_tokens=int(entry[4]),
                reasoning_tokens=int(entry[5]),
                cost_usd=float(entry[6]),
            )
            for key, entry in sorted(merged.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or ""))
        ]

    async def aggregate_conversations_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
    ) -> list[BucketConversationAggregate]:
        bucket_expr = self._bucket_epoch_expr(bucket_seconds)
        bucket_col = bucket_expr.label("bucket_epoch")
        conversation_id = self._conversation_id_expr()
        stmt = (
            select(
                bucket_col,
                func.count(func.distinct(conversation_id)).label("conversation_count"),
            )
            .where(
                RequestLog.requested_at >= since,
                self._exclude_warmup_clause(),
                conversation_id.is_not(None),
            )
            .group_by(bucket_col)
            .order_by(bucket_col)
        )
        result = await self._session.execute(stmt)
        return [
            BucketConversationAggregate(
                bucket_epoch=int(row.bucket_epoch),
                conversation_count=int(row.conversation_count),
            )
            for row in result.all()
        ]

    async def aggregate_activity_since(self, since: datetime) -> RequestActivityAggregate:
        return await self._aggregate_activity(since, None)

    async def aggregate_activity_between(self, since: datetime, until: datetime) -> RequestActivityAggregate:
        return await self._aggregate_activity(since, until)

    async def _aggregate_activity(self, since: datetime, until: datetime | None) -> RequestActivityAggregate:
        rollup_rows, raw_windows = await read_hourly_window(
            self._session,
            since,
            until,
            filters=(RequestUsageHourlyRollup.request_kind.not_in(WARMUP_REQUEST_KINDS),),
        )
        request_count = error_count = input_tokens = output_tokens = cached_input_tokens = 0
        cost_usd = 0.0
        for rollup in rollup_rows:
            request_count += rollup.request_count
            error_count += rollup.error_count
            input_tokens += rollup.input_tokens
            output_tokens += rollup.output_tokens
            cached_input_tokens += rollup.cached_input_tokens
            cost_usd += rollup.cost_usd
        if raw_windows:
            totals_stmt = select(
                func.count().label("request_count"),
                func.coalesce(
                    func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)),
                    0,
                ).label("error_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            ).where(
                raw_windows_clause(raw_windows),
                self._exclude_warmup_clause(),
            )
            row = (await self._session.execute(totals_stmt)).one()
            request_count += int(row.request_count)
            error_count += int(row.error_count)
            input_tokens += int(row.input_tokens)
            output_tokens += int(row.output_tokens)
            cached_input_tokens += int(row.cached_input_tokens)
            cost_usd += float(row.cost_usd or 0.0)

        # Distinct conversation counts are not additive across the fold
        # boundary, so they always come from raw over the FULL window (a
        # documented non-goal: they only reach as far back as retention keeps
        # raw rows). This splits the legacy single-statement read in two —
        # totals and conversation metrics can straddle a concurrent insert,
        # which the periodically-polled dashboard tolerates.
        conversation_stmt = select(
            func.count(func.distinct(self._conversation_id_expr())).label("conversation_count"),
            func.count(self._conversation_id_expr()).label("conversation_request_count"),
        ).where(
            RequestLog.requested_at >= since,
            self._exclude_warmup_clause(),
        )
        if until is not None:
            conversation_stmt = conversation_stmt.where(RequestLog.requested_at < until)
        conversation_row = (await self._session.execute(conversation_stmt)).one()

        return RequestActivityAggregate(
            request_count=request_count,
            error_count=error_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cost_usd=cost_usd,
            conversation_count=int(conversation_row.conversation_count or 0),
            conversation_request_count=int(conversation_row.conversation_request_count or 0),
        )

    async def top_error_since(self, since: datetime) -> str | None:
        return await self._top_error(since, None)

    async def aggregate_usage_metrics_since(self, since: datetime) -> UsageSummaryLogsAggregate:
        """Aggregate the usage-summary window in SQL instead of hydrating
        every RequestLog row (the secondary window is typically 7 days).

        Matches the Python log helpers exactly: output tokens fall back to
        reasoning tokens, cached tokens clamp per-row to [0, input_tokens],
        and models whose costs are all NULL are omitted from per-model cost.
        """
        dialect = self._session.get_bind().dialect.name
        # SQLite's two-argument min()/max() scalar functions are its
        # least()/greatest().
        least = func.least if dialect == "postgresql" else func.min
        greatest = func.greatest if dialect == "postgresql" else func.max

        window = [RequestLog.requested_at >= since, self._exclude_warmup_clause()]
        output_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
        tokens_expr = func.coalesce(RequestLog.input_tokens, 0) + output_expr
        cached_expr = case(
            (RequestLog.cached_input_tokens.is_(None), 0),
            (RequestLog.input_tokens.is_(None), greatest(0, RequestLog.cached_input_tokens)),
            else_=greatest(0, least(RequestLog.cached_input_tokens, RequestLog.input_tokens)),
        )
        # ONE statement = one snapshot: totals, top error, and per-model cost
        # must describe the same committed row set (the legacy path read one
        # SELECT and reduced it in Python, which was internally consistent;
        # separate statements under READ COMMITTED are not). The grouped
        # result stays tiny (models x error codes) and everything derives
        # from it in Python.
        is_error_expr = (RequestLog.status != literal_column("'success'")).label("is_error")
        rows = (
            await self._session.execute(
                select(
                    RequestLog.model,
                    is_error_expr,
                    RequestLog.error_code,
                    func.count().label("request_count"),
                    func.coalesce(func.sum(tokens_expr), 0).label("total_tokens"),
                    func.coalesce(func.sum(cached_expr), 0).label("cached_input_tokens"),
                    func.sum(RequestLog.cost_usd).label("cost_usd"),
                    func.count(RequestLog.cost_usd).label("cost_count"),
                )
                .where(*window)
                .group_by(RequestLog.model, is_error_expr, RequestLog.error_code)
            )
        ).all()

        request_count = 0
        error_count = 0
        total_tokens = 0
        cached_input_tokens = 0
        error_code_counts: dict[str, int] = {}
        cost_sums: dict[str, float] = {}
        cost_counts: dict[str, int] = {}
        for model, is_error, error_code, group_count, group_tokens, group_cached, group_cost, cost_count in rows:
            group_count = int(group_count or 0)
            request_count += group_count
            total_tokens += int(group_tokens or 0)
            cached_input_tokens += int(group_cached or 0)
            if is_error:
                error_count += group_count
                if error_code:
                    error_code_counts[error_code] = error_code_counts.get(error_code, 0) + group_count
            cost_sums[model] = cost_sums.get(model, 0.0) + float(group_cost or 0.0)
            cost_counts[model] = cost_counts.get(model, 0) + int(cost_count or 0)

        top_error = None
        if error_code_counts:
            # Deterministic tie-break: highest count, then code ascending
            # (the same rule the dashboard top-error read uses).
            top_error = min(error_code_counts, key=lambda code: (-error_code_counts[code], code))

        return UsageSummaryLogsAggregate(
            request_count=request_count,
            error_count=error_count,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
            top_error=top_error,
            # Models whose costs are all NULL stay out, matching the legacy
            # per-row skip of None costs.
            cost_by_model=sorted((model, cost_sums[model]) for model, count in cost_counts.items() if count > 0),
        )

    async def top_error_between(self, since: datetime, until: datetime) -> str | None:
        return await self._top_error(since, until)

    async def _top_error(self, since: datetime, until: datetime | None) -> str | None:
        # The error satellite was folded with this exact filter set (warmup
        # kinds excluded, soft-deleted rows INCLUDED, error_code NOT NULL);
        # only the account dimension needs summing over here.
        error_rows, raw_windows = await read_errors_window(self._session, since, until)
        counts: dict[str, int] = {}
        for error in error_rows:
            counts[error.error_code] = counts.get(error.error_code, 0) + error.error_count
        if raw_windows:
            stmt = (
                select(RequestLog.error_code, func.count(RequestLog.id).label("error_count"))
                .where(
                    raw_windows_clause(raw_windows),
                    self._exclude_warmup_clause(),
                    RequestLog.status != "success",
                    RequestLog.error_code.is_not(None),
                )
                .group_by(RequestLog.error_code)
            )
            for error_code, error_count in (await self._session.execute(stmt)).all():
                counts[error_code] = counts.get(error_code, 0) + int(error_count)
        if not counts:
            return None
        # Deterministic tie-break: highest count, then code ascending — the
        # rule the legacy single-statement ORDER BY used. The legacy reader
        # also coerced a falsy winner (an empty-string error_code, which the
        # nullable column permits) to None; `or None` reproduces that.
        return min(counts, key=lambda code: (-counts[code], code)) or None

    async def earliest_activity_at(self) -> datetime | None:
        stmt = select(func.min(RequestLog.requested_at)).where(self._exclude_warmup_clause())
        result = await self._session.execute(stmt)
        value = result.scalar_one_or_none()
        raw_earliest = value if isinstance(value, datetime) else None
        # Raw wins while it survives (sub-hour precision); the whole-hour
        # rollup fallback (keeps history-dependent UI like canCompare
        # working) applies only when the earliest folded bucket lies STRICTLY
        # below raw's own bucket — i.e. retention has pruned earlier raw
        # rows. A folded bucket that merely floors the still-present earliest
        # raw row must not round the result down to the hour.
        rollup_earliest = await earliest_hourly_bucket_at(self._session)
        if rollup_earliest is not None and (raw_earliest is None or rollup_earliest < floor_to_hour(raw_earliest)):
            return rollup_earliest
        return raw_earliest

    async def add_log(
        self,
        account_id: str | None,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        latency_first_token_ms: int | None = None,
        latency_queue_ms: int | None = None,
        latency_response_created_ms: int | None = None,
        latency_first_upstream_event_ms: int | None = None,
        latency_response_create_gate_wait_ms: int | None = None,
        latency_bridge_queue_wait_ms: int | None = None,
        prewarm_status: str | None = None,
        prewarm_latency_ms: int | None = None,
        session_previous_gap_ms: int | None = None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        transport: str | None = None,
        upstream_transport: str | None = None,
        api_key_id: str | None = None,
        session_id: str | None = None,
        plan_type: str | None = None,
        source: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
        conversation_id: str | None = None,
        client_ip: str | None = None,
        failure_phase: str | None = None,
        failure_detail: str | None = None,
        failure_exception_type: str | None = None,
        upstream_status_code: int | None = None,
        upstream_error_code: str | None = None,
        model_source_id: str | None = None,
        model_source_kind: str | None = None,
        cost_usd: float | None = None,
        bridge_stage: str | None = None,
        request_kind: str = RequestKind.NORMAL.value,
        upstream_proxy_route_mode: str | None = None,
        upstream_proxy_pool_id: str | None = None,
        upstream_proxy_endpoint_id: str | None = None,
        upstream_proxy_fallback_used: bool | None = None,
        upstream_proxy_fail_closed_reason: str | None = None,
        archive_request_id: str | None = None,
    ) -> RequestLog:
        async with sqlite_writer_section():
            resolved_request_id = ensure_request_id(request_id)
            resolved_archive_request_id = (archive_request_id or "").strip() or resolved_request_id
            resolved_plan_type = plan_type
            if resolved_plan_type is None and account_id:
                resolved_plan_type = await self._resolve_account_plan_type(account_id)
            resolved_useragent = useragent if not isinstance(useragent, str) or useragent.strip() else None
            resolved_useragent_group = (
                useragent_group if not isinstance(useragent_group, str) or useragent_group.strip() else None
            )
            resolved_conversation_id = _normalize_conversation_id(conversation_id)
            resolved_client_ip = client_ip if not isinstance(client_ip, str) or client_ip.strip() else None
            log = RequestLog(
                account_id=account_id,
                model_source_id=model_source_id,
                model_source_kind=model_source_kind,
                api_key_id=api_key_id,
                session_id=session_id,
                request_id=resolved_request_id,
                archive_request_id=resolved_archive_request_id,
                model=model,
                plan_type=resolved_plan_type,
                source=source,
                transport=transport,
                upstream_transport=upstream_transport,
                request_kind=request_kind,
                useragent=resolved_useragent,
                useragent_group=resolved_useragent_group,
                conversation_id=resolved_conversation_id,
                client_ip=resolved_client_ip,
                service_tier=service_tier,
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                cost_usd=None,
                reasoning_effort=reasoning_effort,
                latency_ms=latency_ms,
                latency_first_token_ms=latency_first_token_ms,
                latency_queue_ms=latency_queue_ms,
                latency_response_created_ms=latency_response_created_ms,
                latency_first_upstream_event_ms=latency_first_upstream_event_ms,
                latency_response_create_gate_wait_ms=latency_response_create_gate_wait_ms,
                latency_bridge_queue_wait_ms=latency_bridge_queue_wait_ms,
                prewarm_status=prewarm_status,
                prewarm_latency_ms=prewarm_latency_ms,
                session_previous_gap_ms=session_previous_gap_ms,
                status=status,
                error_code=error_code,
                error_message=error_message,
                failure_phase=failure_phase,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
                upstream_status_code=upstream_status_code,
                upstream_error_code=upstream_error_code,
                bridge_stage=bridge_stage,
                upstream_proxy_route_mode=upstream_proxy_route_mode,
                upstream_proxy_pool_id=upstream_proxy_pool_id,
                upstream_proxy_endpoint_id=upstream_proxy_endpoint_id,
                upstream_proxy_fallback_used=upstream_proxy_fallback_used,
                upstream_proxy_fail_closed_reason=upstream_proxy_fail_closed_reason,
                requested_at=requested_at or utcnow(),
            )
            log.cost_usd = (
                cost_usd
                if cost_usd is not None
                else 0.0
                if model_source_id is not None
                else calculated_cost_from_log(typing_cast(RequestLogLike, log))
            )
            self._session.add(log)
            try:
                await self._session.commit()
                # No refresh: every column is set explicitly before insert and
                # expire_on_commit=False, so the round trip was pure overhead
                # on every request's log write.
                return log
            except sa_exc.ResourceClosedError:
                return log
            except BaseException:
                await _safe_rollback(self._session)
                raise

    async def update_model_for_request(self, request_id: str, model: str) -> int:
        """Override the ``model`` field of any logs matching ``request_id``.

        Used by route handlers that translate a public request shape (e.g.
        ``/v1/images/generations``) into an internal Responses request: the
        first-pass log row stores the internal host model used for routing,
        and we rewrite it here once the public effective model is known so
        the dashboard and usage views surface the user-visible model.

        Only rows in the un-folded live tail — strictly above the lifetime
        watermark (its fold interval is ``(start, end]``) AND at or above the
        hourly watermark (half-open ``[start, end)``) — are rewritten:
        ``model`` is a rollup dimension and ``cost_usd`` a folded measure, so
        mutating a row either rollup already captured would silently diverge
        that rollup from raw (and the divergence becomes unrepairable once
        retention prunes the raw row).
        A matching row below the watermarks can only be a client-reused
        request id colliding with unrelated old traffic — the rewrite's
        target is the row the caller inserted moments ago. The fold-state
        lock serializes this against an in-flight fold slice.

        Returns the number of rows that were updated.
        """
        async with sqlite_writer_section():
            resolved_request_id = ensure_request_id(request_id)
            try:
                await lock_fold_state(self._session)
                watermarks = (
                    await self._session.execute(
                        select(
                            AccountUsageRollupState.folded_through,
                            AccountUsageRollupState.hourly_folded_through,
                        ).where(AccountUsageRollupState.id == 1)
                    )
                ).first()
                # Fetch the affected rows so we can recompute ``cost_usd``
                # from the new model. ``add_log`` derives the cost at insert
                # time from the original (host) model; without recomputing
                # here, dashboards would mix the public ``gpt-image-*`` model
                # label with host-model pricing and report inaccurate cost.
                stmt = select(RequestLog).where(RequestLog.request_id == resolved_request_id)
                if watermarks is not None:
                    # A row is un-folded by EVERY rollup only when it clears
                    # both bounds, each matching its fold's own interval
                    # convention: the lifetime fold is `(start, end]`
                    # (inclusive end — a row AT the watermark is folded), the
                    # hourly fold is `[start, end)`.
                    folded_through, hourly_folded_through = watermarks
                    stmt = stmt.where(
                        RequestLog.requested_at > folded_through,
                        RequestLog.requested_at >= hourly_folded_through,
                    )
                result_rows = await self._session.execute(stmt)
                logs = list(result_rows.scalars())
                if not logs:
                    # End the transaction: the fold-state row lock (and the
                    # bootstrap insert behind it) must not outlive this call.
                    await _safe_rollback(self._session)
                    return 0
                for log in logs:
                    log.model = model
                    log.cost_usd = calculated_cost_from_log(typing_cast(RequestLogLike, log))
                await self._session.commit()
            except sa_exc.ResourceClosedError:
                return 0
            except BaseException:
                await _safe_rollback(self._session)
                raise
            return len(logs)

    async def list_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        conversation_id: str | None = None,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
    ) -> RequestLogsResult:
        filters = self._build_filters(
            search=search,
            since=since,
            until=until,
            conversation_id=conversation_id,
            account_ids=account_ids,
            api_key_ids=api_key_ids,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=include_success,
            include_error_other=include_error_other,
            error_codes_in=error_codes_in,
            error_codes_excluding=error_codes_excluding,
            exclude_soft_deleted=True,
        )

        stmt = select(RequestLog).order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
        stmt = self._apply_related_search_joins(stmt, filters.needs_related_search_joins)
        if filters.conditions:
            stmt = stmt.where(and_(*filters.conditions))
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        logs = list(result.scalars().all())

        if conversation_id is not None:
            total, aggregated_cost_usd = await self._count_and_sum_recent(filters)
            return RequestLogsResult(logs=logs, total=total, aggregated_cost_usd=aggregated_cost_usd)

        ttl_seconds = _COUNT_CACHE_TTL_SECONDS
        if ttl_seconds <= 0:
            return RequestLogsResult(logs=logs, total=await self._count_recent(filters))
        cache_key = (
            search,
            since,
            until,
            conversation_id,
            tuple(account_ids or ()),
            tuple(api_key_ids or ()),
            tuple(model_options or ()),
            tuple(models or ()),
            tuple(reasoning_efforts or ()),
            include_success,
            include_error_other,
            tuple(sorted(error_codes_in)) if error_codes_in else None,
            tuple(sorted(error_codes_excluding)) if error_codes_excluding else None,
        )
        total = _cached_recent_count(cache_key)
        if total is None:
            total = await self._count_recent(filters)
            _store_recent_count(cache_key, total, ttl_seconds)
        return RequestLogsResult(logs=logs, total=total)

    async def _count_and_sum_recent(self, filters: _RequestLogFilters) -> tuple[int, float]:
        aggregate_stmt = select(
            func.count(),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
        ).select_from(RequestLog)
        aggregate_stmt = self._apply_related_search_joins(aggregate_stmt, filters.needs_related_search_joins)
        if filters.conditions:
            aggregate_stmt = aggregate_stmt.where(and_(*filters.conditions))
        result = await self._session.execute(aggregate_stmt)
        request_count, aggregated_cost_usd = result.one()
        return int(request_count), float(aggregated_cost_usd)

    async def _count_recent(self, filters: _RequestLogFilters) -> int:
        count_stmt = select(func.count(RequestLog.id)).select_from(RequestLog)
        count_stmt = self._apply_related_search_joins(count_stmt, filters.needs_related_search_joins)
        if filters.conditions:
            count_stmt = count_stmt.where(and_(*filters.conditions))
        result = await self._session.execute(count_stmt)
        return int(result.scalar_one())

    async def _resolve_account_plan_type(self, account_id: str) -> str | None:
        result = await self._session.execute(select(Account.plan_type).where(Account.id == account_id).limit(1))
        return result.scalar_one_or_none()

    async def list_filter_options(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
    ) -> tuple[list[str], list[tuple[str, str | None]], list[str], list[tuple[str, str | None]]]:
        filters = self._build_filters(
            since=since,
            until=until,
            account_ids=account_ids,
            api_key_ids=api_key_ids,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
            exclude_soft_deleted=True,
        )
        api_key_facet_filters = self._build_filters(
            since=since,
            until=until,
            account_ids=account_ids,
            api_key_ids=None,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
            exclude_soft_deleted=True,
        )

        unfiltered = not any((since, until, account_ids, api_key_ids, model_options, models, reasoning_efforts))
        if unfiltered:
            # PostgreSQL has no loose index scan: with no user filters each
            # DISTINCT below is a full pass over request_logs, four times per
            # filter-panel load. Emulate the skip scan instead — one indexed
            # probe per distinct value.
            return (
                [value for value in await self._distinct_skip_scan(RequestLog.account_id, filters.conditions) if value],
                await self._pair_facet_skip_scan(RequestLog.model, RequestLog.reasoning_effort, filters.conditions),
                [
                    value
                    for value in await self._distinct_skip_scan(RequestLog.api_key_id, api_key_facet_filters.conditions)
                    if value
                ],
                await self._pair_facet_skip_scan(RequestLog.status, RequestLog.error_code, filters.conditions),
            )

        account_stmt = select(RequestLog.account_id).distinct().order_by(RequestLog.account_id.asc())
        model_stmt = (
            select(RequestLog.model, RequestLog.reasoning_effort)
            .distinct()
            .order_by(RequestLog.model.asc(), RequestLog.reasoning_effort.asc())
        )
        api_key_stmt = select(RequestLog.api_key_id).distinct().order_by(RequestLog.api_key_id.asc())
        status_stmt = (
            select(RequestLog.status, RequestLog.error_code)
            .distinct()
            .order_by(RequestLog.status.asc(), RequestLog.error_code.asc())
        )
        if filters.conditions:
            clause = and_(*filters.conditions)
            account_stmt = account_stmt.where(clause)
            model_stmt = model_stmt.where(clause)
            status_stmt = status_stmt.where(clause)
        if api_key_facet_filters.conditions:
            api_key_stmt = api_key_stmt.where(and_(*api_key_facet_filters.conditions))

        account_rows = await self._session.execute(account_stmt)
        model_rows = await self._session.execute(model_stmt)
        api_key_rows = await self._session.execute(api_key_stmt)
        status_rows = await self._session.execute(status_stmt)

        account_ids = [row[0] for row in account_rows.all() if row[0]]
        model_options = [(row[0], row[1]) for row in model_rows.all() if row[0]]
        api_key_ids = [row[0] for row in api_key_rows.all() if row[0]]
        status_values = [(row[0], row[1]) for row in status_rows.all() if row[0]]
        return account_ids, model_options, api_key_ids, status_values

    async def _distinct_skip_scan(
        self,
        column: InstrumentedAttribute[str] | InstrumentedAttribute[str | None],
        conditions: list,
    ) -> list[str]:
        """Loose-index-scan emulation: seed min(column), then min(column) >
        previous, one btree probe per distinct value. NULLs never seed or
        chain (min() skips them); empty strings are preserved — the legacy
        DISTINCT path only drops falsy values per facet, in the callers."""
        seed = select(func.min(column).label("val")).where(*conditions)
        skip = seed.cte("facet_skip", recursive=True)
        successor = select(func.min(column)).where(*conditions, column > skip.c.val).scalar_subquery()
        skip = skip.union_all(select(successor).where(skip.c.val.is_not(None)))
        stmt = select(skip.c.val).where(skip.c.val.is_not(None)).order_by(skip.c.val.asc())
        rows = await self._session.execute(stmt)
        return [value for (value,) in rows.all() if value is not None]

    async def _pair_facet_skip_scan(
        self,
        leading: InstrumentedAttribute[str] | InstrumentedAttribute[str | None],
        second: InstrumentedAttribute[str] | InstrumentedAttribute[str | None],
        conditions: list,
    ) -> list[tuple[str, str | None]]:
        """(leading, second) facet: skip-scan the leading column, then per
        value probe a `(value, NULL)` pair and skip-scan the non-NULL second
        values. NULL pair placement follows the backend's ORDER BY ASC NULL
        ordering (SQLite: first, PostgreSQL: last) so results match the
        legacy DISTINCT path exactly."""
        nulls_first = self._session.get_bind().dialect.name == "sqlite"
        pairs: list[tuple[str, str | None]] = []
        for value in await self._distinct_skip_scan(leading, conditions):
            if not value:
                # Legacy DISTINCT drops falsy leading values in Python.
                continue
            value_conditions = [*conditions, leading == value]
            null_probe = select(RequestLog.id).where(*value_conditions, second.is_(None)).limit(1)
            has_null = (await self._session.execute(null_probe)).scalar_one_or_none() is not None
            second_values = await self._distinct_skip_scan(second, value_conditions)
            if has_null and nulls_first:
                pairs.append((value, None))
            pairs.extend((value, second_value) for second_value in second_values)
            if has_null and not nulls_first:
                pairs.append((value, None))
        return pairs

    async def get_api_key_names_by_ids(self, api_key_ids: list[str]) -> dict[str, str]:
        unique_ids = sorted({key_id for key_id in api_key_ids if key_id})
        if not unique_ids:
            return {}
        result = await self._session.execute(select(ApiKey.id, ApiKey.name).where(ApiKey.id.in_(unique_ids)))
        return {key_id: name for key_id, name in result.all() if key_id and name}

    async def get_api_key_details_by_ids(self, api_key_ids: list[str]) -> dict[str, tuple[str, str | None]]:
        unique_ids = sorted({key_id for key_id in api_key_ids if key_id})
        if not unique_ids:
            return {}
        result = await self._session.execute(
            select(ApiKey.id, ApiKey.name, ApiKey.key_prefix).where(ApiKey.id.in_(unique_ids))
        )
        return {key_id: (name, key_prefix) for key_id, name, key_prefix in result.all() if key_id and name}

    def _build_filters(
        self,
        *,
        search: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        conversation_id: str | None = None,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
        exclude_soft_deleted: bool = False,
    ) -> _RequestLogFilters:
        conditions = []
        if exclude_soft_deleted:
            conditions.append(RequestLog.deleted_at.is_(None))
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)
        if conversation_id is not None:
            conditions.append(RequestLog.conversation_id == conversation_id)
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if api_key_ids:
            conditions.append(RequestLog.api_key_id.in_(api_key_ids))

        if model_options:
            pair_conditions = []
            for model, effort in model_options:
                base = (model or "").strip()
                if not base:
                    continue
                if effort is None:
                    pair_conditions.append(and_(RequestLog.model == base, RequestLog.reasoning_effort.is_(None)))
                else:
                    pair_conditions.append(and_(RequestLog.model == base, RequestLog.reasoning_effort == effort))
            if pair_conditions:
                conditions.append(or_(*pair_conditions))
        else:
            if models:
                conditions.append(RequestLog.model.in_(models))
            if reasoning_efforts:
                conditions.append(RequestLog.reasoning_effort.in_(reasoning_efforts))

        status_conditions = []
        if include_success:
            status_conditions.append(RequestLog.status == "success")
        if error_codes_in:
            status_conditions.append(and_(RequestLog.status == "error", RequestLog.error_code.in_(error_codes_in)))
        if include_error_other:
            error_clause = [RequestLog.status == "error"]
            if error_codes_excluding:
                error_clause.append(
                    or_(
                        RequestLog.error_code.is_(None),
                        ~RequestLog.error_code.in_(error_codes_excluding),
                    )
                )
            status_conditions.append(and_(*error_clause))
        if status_conditions:
            conditions.append(or_(*status_conditions))
        if search:
            search_pattern = f"%{search}%"
            conditions.append(
                or_(
                    RequestLog.account_id.ilike(search_pattern),
                    Account.email.ilike(search_pattern),
                    RequestLog.request_id.ilike(search_pattern),
                    RequestLog.model.ilike(search_pattern),
                    RequestLog.reasoning_effort.ilike(search_pattern),
                    RequestLog.source.ilike(search_pattern),
                    RequestLog.client_ip.ilike(search_pattern),
                    RequestLog.status.ilike(search_pattern),
                    RequestLog.error_code.ilike(search_pattern),
                    RequestLog.error_message.ilike(search_pattern),
                    RequestLog.api_key_id.ilike(search_pattern),
                    ApiKey.name.ilike(search_pattern),
                    cast(RequestLog.requested_at, String).ilike(search_pattern),
                    cast(RequestLog.input_tokens, String).ilike(search_pattern),
                    cast(RequestLog.output_tokens, String).ilike(search_pattern),
                    cast(RequestLog.cached_input_tokens, String).ilike(search_pattern),
                    cast(RequestLog.reasoning_tokens, String).ilike(search_pattern),
                    cast(RequestLog.latency_ms, String).ilike(search_pattern),
                )
            )
            return _RequestLogFilters(conditions=conditions, needs_related_search_joins=True)
        return _RequestLogFilters(conditions=conditions, needs_related_search_joins=False)

    def _apply_related_search_joins(self, stmt, include_related_search_joins: bool):
        if not include_related_search_joins:
            return stmt
        return stmt.outerjoin(Account, Account.id == RequestLog.account_id).outerjoin(
            ApiKey,
            ApiKey.id == RequestLog.api_key_id,
        )


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        with anyio.CancelScope(shield=True):
            await session.rollback()
    except BaseException:
        return
