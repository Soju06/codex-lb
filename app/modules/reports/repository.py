from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import batched
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, RequestLog
from app.modules.reports.filters import (
    MISSING_USERAGENT_GROUP,
    _normal_traffic_clause,
    _useragent_group_filter_clause,
)
from app.modules.reports.rollup import MEASURES
from app.modules.reports.rollup_read import report_source
from app.modules.reports.thread_identity import ThreadIdentityFacetRow, aggregate_thread_identity

_SQLITE_COMPOUND_SELECT_LIMIT = 500
MAX_DAILY_REPORT_DAYS = 730
MAX_SPEED_REPORT_DAYS = 7


class DailyReportRangeTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class DailyReportAggregateRow:
    date: str
    requests: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None
    cached_input_tokens: int
    cost_usd: float
    active_accounts: int
    error_count: int
    median_ttft_ms: float
    median_tps: float
    median_queue_ms: float
    conversation_count: int = 0
    cancelled_count: int = 0


@dataclass(frozen=True)
class SummaryAggregateRow:
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int
    reasoning_usage_known_requests: int
    total_cached_tokens: int
    total_requests: int
    total_errors: int
    active_accounts: int
    conversation_count: int = 0
    total_cancelled: int = 0


@dataclass(frozen=True)
class ModelAggregateRow:
    model: str
    cost_usd: float
    request_count: int


@dataclass(frozen=True)
class AccountAggregateRow:
    account_id: str | None
    alias: str | None
    cost_usd: float
    request_count: int


@dataclass(frozen=True)
class UserAgentAggregateRow:
    useragent_group: str
    cost_usd: float
    request_count: int


class ReportsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def aggregate_daily_rows(
        self,
        start_date: date,
        end_date: date,
        timezone_info: ZoneInfo | timezone,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
        api_key_ids: list[str] | None = None,
    ) -> list[DailyReportAggregateRow]:
        window_days = (end_date - start_date).days + 1
        if window_days > MAX_DAILY_REPORT_DAYS:
            raise DailyReportRangeTooLargeError(f"report date range must be {MAX_DAILY_REPORT_DAYS} days or less")
        rows: list[DailyReportAggregateRow] = []
        for batch in batched(_daily_bucket_ranges(start_date, end_date, timezone_info), _SQLITE_COMPOUND_SELECT_LIMIT):
            windows = list(batch)
            speed_values: dict[str, tuple[float, float, float]] = {}
            if window_days <= MAX_SPEED_REPORT_DAYS:
                speeds = await self._session.execute(
                    _daily_speed_medians_stmt(windows, account_ids, model, useragent_group, api_key_ids)
                )
                speed_values = {
                    row.report_date: (
                        float(row.median_ttft_ms or 0),
                        float(row.median_tps or 0),
                        float(row.median_queue_ms or 0),
                    )
                    for row in speeds
                }
            source = report_source(self._session, windows, account_ids, model, useragent_group, api_key_ids)
            result = await self._session.execute(
                select(source.c.report_date, *_aggregate_columns(source))
                .group_by(source.c.report_date)
                .order_by(source.c.report_date)
            )
            for row in result:
                speed = speed_values.get(row.report_date, (0.0, 0.0, 0.0))
                rows.append(
                    DailyReportAggregateRow(
                        date=row.report_date,
                        requests=int(row.request_count),
                        input_tokens=int(row.input_tokens),
                        output_tokens=int(row.output_tokens),
                        reasoning_tokens=int(row.reasoning_tokens) if row.reasoning_usage_known_requests else None,
                        cached_input_tokens=int(row.cached_input_tokens),
                        cost_usd=float(row.cost_usd),
                        active_accounts=int(row.active_accounts),
                        error_count=int(row.error_count),
                        cancelled_count=int(row.cancelled_count),
                        conversation_count=int(row.conversation_count),
                        median_ttft_ms=speed[0],
                        median_tps=speed[1],
                        median_queue_ms=speed[2],
                    )
                )
        return rows

    async def aggregate_summary(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
        api_key_ids: list[str] | None = None,
    ) -> SummaryAggregateRow:
        source = report_source(
            self._session, [("summary", start_date, end_date)], account_ids, model, useragent_group, api_key_ids
        )
        row = (await self._session.execute(select(*_aggregate_columns(source)))).one()
        return SummaryAggregateRow(
            total_cost_usd=float(row.cost_usd),
            total_input_tokens=int(row.input_tokens),
            total_output_tokens=int(row.output_tokens),
            total_reasoning_tokens=int(row.reasoning_tokens),
            reasoning_usage_known_requests=int(row.reasoning_usage_known_requests),
            total_cached_tokens=int(row.cached_input_tokens),
            total_requests=int(row.request_count),
            total_errors=int(row.error_count),
            total_cancelled=int(row.cancelled_count),
            active_accounts=int(row.active_accounts),
            conversation_count=int(row.conversation_count),
        )

    async def aggregate_by_model(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
        api_key_ids: list[str] | None = None,
    ) -> list[ModelAggregateRow]:
        source = report_source(
            self._session, [("models", start_date, end_date)], account_ids, model, useragent_group, api_key_ids
        )
        result = await self._session.execute(
            select(
                source.c.model,
                func.sum(source.c.cost_usd).label("cost_usd"),
                func.sum(source.c.request_count).label("request_count"),
            )
            .group_by(source.c.model)
            .order_by(func.sum(source.c.cost_usd).desc(), source.c.model)
        )
        return [ModelAggregateRow(row.model, float(row.cost_usd), int(row.request_count)) for row in result]

    async def aggregate_by_account(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
        api_key_ids: list[str] | None = None,
    ) -> list[AccountAggregateRow]:
        source = report_source(
            self._session, [("accounts", start_date, end_date)], account_ids, model, useragent_group, api_key_ids
        )
        result = await self._session.execute(
            select(
                source.c.account_id,
                Account.alias,
                func.sum(source.c.cost_usd).label("cost_usd"),
                func.sum(source.c.request_count).label("request_count"),
            )
            .outerjoin(Account, Account.id == source.c.account_id)
            .group_by(source.c.account_id, Account.alias)
            .order_by(func.sum(source.c.cost_usd).desc(), source.c.account_id)
        )
        return [
            AccountAggregateRow(row.account_id, row.alias, float(row.cost_usd), int(row.request_count))
            for row in result
        ]

    async def aggregate_by_useragent(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
        api_key_ids: list[str] | None = None,
    ) -> list[UserAgentAggregateRow]:
        source = report_source(
            self._session, [("useragents", start_date, end_date)], account_ids, model, useragent_group, api_key_ids
        )
        group = func.coalesce(source.c.useragent_group, MISSING_USERAGENT_GROUP)
        result = await self._session.execute(
            select(
                group.label("useragent_group"),
                func.sum(source.c.cost_usd).label("cost_usd"),
                func.sum(source.c.request_count).label("request_count"),
            )
            .where(or_(source.c.useragent_group.is_(None), func.trim(source.c.useragent_group) != ""))
            .group_by(group)
            .order_by(func.sum(source.c.cost_usd).desc(), group)
        )
        return [
            UserAgentAggregateRow(row.useragent_group, float(row.cost_usd), int(row.request_count)) for row in result
        ]

    async def count_active_accounts(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
        api_key_ids: list[str] | None = None,
    ) -> int:
        source = report_source(
            self._session, [("active", start_date, end_date)], account_ids, model, useragent_group, api_key_ids
        )
        return int((await self._session.execute(select(func.count(func.distinct(source.c.account_id))))).scalar_one())

    async def aggregate_thread_identity(
        self,
        start_at: datetime,
        end_at: datetime,
    ) -> dict[bool, ThreadIdentityFacetRow]:
        """Keyed and unkeyed thread-identity counters for one bounded window.

        Deliberately unfiltered by account, API key, model or user agent: an
        account filter would force every conversation to one account and make
        the accounts-per-conversation factor read 1.0 by construction.
        """
        return await aggregate_thread_identity(self._session, start_at, end_at)

    async def earliest_report_activity_at(
        self,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
        api_key_ids: list[str] | None = None,
    ) -> datetime | None:
        source = report_source(
            self._session,
            [("earliest", datetime(1970, 1, 1), datetime(9998, 1, 1))],
            account_ids,
            model,
            useragent_group,
            api_key_ids,
        )
        return (await self._session.execute(select(func.min(source.c.first_requested_at)))).scalar_one_or_none()

    async def list_filter_options(
        self,
        start_at: datetime,
        end_at: datetime,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        source = report_source(
            self._session, [("options", start_at, end_at)], account_ids, api_key_ids=api_key_ids, catalog=True
        )
        pairs = (await self._session.execute(select(source.c.model, source.c.useragent_group).distinct())).all()
        return sorted({row.model for row in pairs}), sorted(
            {
                row.useragent_group if row.useragent_group is not None else MISSING_USERAGENT_GROUP
                for row in pairs
                if row.useragent_group is None or row.useragent_group.strip()
            }
        )


def _aggregate_columns(source) -> list:
    return [
        *(func.coalesce(func.sum(getattr(source.c, name)), 0).label(name) for name in MEASURES),
        func.count(func.distinct(source.c.account_id)).label("active_accounts"),
        func.count(func.distinct(source.c.conversation_id)).label("conversation_count"),
    ]


def _report_conditions(
    start_date: datetime,
    end_date: datetime,
    account_ids: list[str] | None,
    model: str | None,
    useragent_group: str | None,
    api_key_ids: list[str] | None = None,
) -> list:
    conditions = [
        RequestLog.requested_at >= start_date,
        RequestLog.requested_at < end_date,
        _normal_traffic_clause(),
    ]
    if account_ids:
        conditions.append(RequestLog.account_id.in_(account_ids))
    if model:
        conditions.append(RequestLog.model == model)
    useragent_group_clause = _useragent_group_filter_clause(useragent_group)
    if useragent_group_clause is not None:
        conditions.append(useragent_group_clause)
    if api_key_ids:
        conditions.append(RequestLog.api_key_id.in_(api_key_ids))
    return conditions


def _day_ranges_cte(day_ranges: list[tuple[str, datetime, datetime]]):
    day_range_rows = [
        select(
            literal(report_date).label("report_date"),
            literal(day_start).label("day_start"),
            literal(day_end).label("day_end"),
        )
        for report_date, day_start, day_end in day_ranges
    ]
    day_ranges_query = day_range_rows[0] if len(day_range_rows) == 1 else union_all(*day_range_rows)
    return day_ranges_query.cte("report_days")


def _daily_speed_medians_stmt(
    day_ranges: list[tuple[str, datetime, datetime]],
    account_ids: list[str] | None,
    model: str | None,
    useragent_group: str | None,
    api_key_ids: list[str] | None = None,
):
    useragent_group_clause = _useragent_group_filter_clause(useragent_group)
    day_ranges_cte = _day_ranges_cte(day_ranges)
    traffic_join = day_ranges_cte.join(
        RequestLog,
        and_(
            RequestLog.requested_at >= day_ranges_cte.c.day_start,
            RequestLog.requested_at < day_ranges_cte.c.day_end,
            _normal_traffic_clause(),
            *([RequestLog.account_id.in_(account_ids)] if account_ids else []),
            *([RequestLog.model == model] if model else []),
            *([useragent_group_clause] if useragent_group_clause is not None else []),
            *([RequestLog.api_key_id.in_(api_key_ids)] if api_key_ids else []),
        ),
    )
    token_count = RequestLog.output_tokens - func.coalesce(RequestLog.reasoning_tokens, 0)
    ttft_values_cte = (
        select(
            day_ranges_cte.c.report_date,
            RequestLog.latency_first_token_ms.label("ttft_ms"),
        )
        .select_from(traffic_join)
        .where(RequestLog.latency_first_token_ms.is_not(None))
        .cte("daily_ttft_values")
    )
    tps_values_cte = (
        select(
            day_ranges_cte.c.report_date,
            (token_count * 1000.0 / (RequestLog.latency_ms - RequestLog.latency_first_token_ms)).label("tps"),
        )
        .select_from(traffic_join)
        .where(
            token_count.is_not(None),
            token_count > 0,
            RequestLog.latency_ms.is_not(None),
            RequestLog.latency_first_token_ms.is_not(None),
            RequestLog.latency_ms > RequestLog.latency_first_token_ms,
        )
        .cte("daily_tps_values")
    )
    queue_values_cte = (
        select(
            day_ranges_cte.c.report_date,
            RequestLog.latency_queue_ms.label("queue_ms"),
        )
        .select_from(traffic_join)
        .where(RequestLog.latency_queue_ms.is_not(None))
        .cte("daily_queue_values")
    )
    ttft_count = func.count().over(partition_by=ttft_values_cte.c.report_date)
    ttft_ranked_cte = select(
        ttft_values_cte.c.report_date,
        ttft_values_cte.c.ttft_ms,
        ttft_count.label("sample_count"),
        func.row_number()
        .over(partition_by=ttft_values_cte.c.report_date, order_by=ttft_values_cte.c.ttft_ms)
        .label("ttft_rank"),
    ).cte("daily_ttft_ranks")
    tps_count = func.count().over(partition_by=tps_values_cte.c.report_date)
    tps_ranked_cte = select(
        tps_values_cte.c.report_date,
        tps_values_cte.c.tps,
        tps_count.label("sample_count"),
        func.row_number()
        .over(partition_by=tps_values_cte.c.report_date, order_by=tps_values_cte.c.tps)
        .label("tps_rank"),
    ).cte("daily_tps_ranks")
    queue_count = func.count().over(partition_by=queue_values_cte.c.report_date)
    queue_ranked_cte = select(
        queue_values_cte.c.report_date,
        queue_values_cte.c.queue_ms,
        queue_count.label("sample_count"),
        func.row_number()
        .over(partition_by=queue_values_cte.c.report_date, order_by=queue_values_cte.c.queue_ms)
        .label("queue_rank"),
    ).cte("daily_queue_ranks")

    # A median contains the one center row for odd samples and both center rows
    # for even samples. Multiplication avoids dialect-specific integer division.
    ttft_is_middle = and_(
        ttft_ranked_cte.c.ttft_rank * 2 >= ttft_ranked_cte.c.sample_count,
        ttft_ranked_cte.c.ttft_rank * 2 <= ttft_ranked_cte.c.sample_count + 2,
    )
    tps_is_middle = and_(
        tps_ranked_cte.c.tps_rank * 2 >= tps_ranked_cte.c.sample_count,
        tps_ranked_cte.c.tps_rank * 2 <= tps_ranked_cte.c.sample_count + 2,
    )
    queue_is_middle = and_(
        queue_ranked_cte.c.queue_rank * 2 >= queue_ranked_cte.c.sample_count,
        queue_ranked_cte.c.queue_rank * 2 <= queue_ranked_cte.c.sample_count + 2,
    )
    ttft_medians_cte = (
        select(
            ttft_ranked_cte.c.report_date,
            func.avg(case((ttft_is_middle, ttft_ranked_cte.c.ttft_ms), else_=None)).label("median_ttft_ms"),
        )
        .group_by(ttft_ranked_cte.c.report_date)
        .cte("daily_ttft_medians")
    )
    tps_medians_cte = (
        select(
            tps_ranked_cte.c.report_date,
            func.avg(case((tps_is_middle, tps_ranked_cte.c.tps), else_=None)).label("median_tps"),
        )
        .group_by(tps_ranked_cte.c.report_date)
        .cte("daily_tps_medians")
    )
    queue_medians_cte = (
        select(
            queue_ranked_cte.c.report_date,
            func.avg(case((queue_is_middle, queue_ranked_cte.c.queue_ms), else_=None)).label("median_queue_ms"),
        )
        .group_by(queue_ranked_cte.c.report_date)
        .cte("daily_queue_medians")
    )
    return (
        select(
            day_ranges_cte.c.report_date,
            func.coalesce(ttft_medians_cte.c.median_ttft_ms, 0.0).label("median_ttft_ms"),
            func.coalesce(tps_medians_cte.c.median_tps, 0.0).label("median_tps"),
            func.coalesce(queue_medians_cte.c.median_queue_ms, 0.0).label("median_queue_ms"),
        )
        .select_from(
            day_ranges_cte.outerjoin(
                ttft_medians_cte,
                ttft_medians_cte.c.report_date == day_ranges_cte.c.report_date,
            )
            .outerjoin(
                tps_medians_cte,
                tps_medians_cte.c.report_date == day_ranges_cte.c.report_date,
            )
            .outerjoin(
                queue_medians_cte,
                queue_medians_cte.c.report_date == day_ranges_cte.c.report_date,
            )
        )
        .order_by(day_ranges_cte.c.report_date)
    )


def _daily_bucket_ranges(
    start_date: date,
    end_date: date,
    timezone_info: ZoneInfo | timezone,
) -> list[tuple[str, datetime, datetime]]:
    ranges: list[tuple[str, datetime, datetime]] = []
    current_date = start_date
    while current_date <= end_date:
        day_start = datetime.combine(current_date, datetime.min.time(), tzinfo=timezone_info)
        next_day_start = datetime.combine(current_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone_info)
        ranges.append(
            (
                current_date.isoformat(),
                day_start.astimezone(timezone.utc).replace(tzinfo=None),
                next_day_start.astimezone(timezone.utc).replace(tzinfo=None),
            )
        )
        current_date += timedelta(days=1)
    return ranges
