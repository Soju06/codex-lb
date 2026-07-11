from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import batched
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, RequestLog

_INTERNAL_LIMIT_WARMUP_SOURCE = "limit_warmup"
_INTERNAL_WARMUP_REQUEST_KINDS = ("warmup", "limit_warmup")
_SQLITE_COMPOUND_SELECT_LIMIT = 500
MAX_DAILY_REPORT_DAYS = 730
UNKNOWN_USERAGENT_GROUP = "Unknown"
MISSING_USERAGENT_GROUP = "Missing User-Agent"


class DailyReportRangeTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class DailyReportAggregateRow:
    date: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    active_accounts: int
    error_count: int
    median_ttft_ms: float
    median_tps: float


@dataclass(frozen=True)
class SummaryAggregateRow:
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    total_requests: int
    total_errors: int
    active_accounts: int


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
    ) -> list[DailyReportAggregateRow]:
        window_days = (end_date - start_date).days + 1
        if window_days > MAX_DAILY_REPORT_DAYS:
            raise DailyReportRangeTooLargeError(f"report date range must be {MAX_DAILY_REPORT_DAYS} days or less")
        day_ranges = list(_daily_bucket_ranges(start_date, end_date, timezone_info))
        if not day_ranges:
            return []

        rows: list[DailyReportAggregateRow] = []
        # SQLite caps compound SELECTs at 500 terms, so long report ranges are
        # executed in chunks instead of building a single oversized UNION ALL.
        for day_ranges_batch in batched(day_ranges, _SQLITE_COMPOUND_SELECT_LIMIT):
            day_ranges_list = list(day_ranges_batch)
            speed_result = await self._session.execute(
                _daily_speed_medians_stmt(day_ranges_list, account_ids, model, useragent_group)
            )
            speed_values = {
                speed_row.report_date: (
                    float(speed_row.median_ttft_ms or 0.0),
                    float(speed_row.median_tps or 0.0),
                )
                for speed_row in speed_result.all()
            }

            result = await self._session.execute(_daily_rows_stmt(day_ranges_list, account_ids, model, useragent_group))
            rows.extend(
                DailyReportAggregateRow(
                    date=row.report_date,
                    requests=int(row.requests or 0),
                    input_tokens=int(row.input_tokens or 0),
                    output_tokens=int(row.output_tokens or 0),
                    cached_input_tokens=int(row.cached_input_tokens or 0),
                    cost_usd=float(row.cost_usd or 0.0),
                    active_accounts=int(row.active_accounts or 0),
                    error_count=int(row.error_count or 0),
                    median_ttft_ms=speed_values.get(row.report_date, (0.0, 0.0))[0],
                    median_tps=speed_values.get(row.report_date, (0.0, 0.0))[1],
                )
                for row in result.all()
            )
        return rows

    async def aggregate_summary(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
    ) -> SummaryAggregateRow:
        conditions = _report_conditions(start_date, end_date, account_ids, model, useragent_group)

        result = await self._session.execute(
            select(
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("total_input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("total_output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("total_cached_tokens"),
                func.count().label("total_requests"),
                func.coalesce(
                    func.sum(case((RequestLog.status != "success", 1), else_=0)),
                    0,
                ).label("total_errors"),
                func.count(func.distinct(RequestLog.account_id)).label("active_accounts"),
            ).where(and_(*conditions))
        )
        row = result.one()
        return SummaryAggregateRow(
            total_cost_usd=float(row.total_cost_usd),
            total_input_tokens=int(row.total_input_tokens),
            total_output_tokens=int(row.total_output_tokens),
            total_cached_tokens=int(row.total_cached_tokens),
            total_requests=int(row.total_requests),
            total_errors=int(row.total_errors),
            active_accounts=int(row.active_accounts),
        )

    async def aggregate_by_model(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
    ) -> list[ModelAggregateRow]:
        conditions = [
            *_report_conditions(start_date, end_date, account_ids, model, useragent_group),
            RequestLog.model.is_not(None),
        ]

        stmt = (
            select(
                RequestLog.model,
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.count().label("request_count"),
            )
            .where(and_(*conditions))
            .group_by(RequestLog.model)
            .order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0.0).desc())
        )
        result = await self._session.execute(stmt)
        return [
            ModelAggregateRow(
                model=row.model,
                cost_usd=float(row.cost_usd),
                request_count=int(row.request_count),
            )
            for row in result.all()
        ]

    async def aggregate_by_account(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
    ) -> list[AccountAggregateRow]:
        conditions = _report_conditions(start_date, end_date, account_ids, model, useragent_group)

        stmt = (
            select(
                RequestLog.account_id,
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.count().label("request_count"),
            )
            .where(and_(*conditions))
            .group_by(RequestLog.account_id)
            .order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0.0).desc())
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        account_ids_found = [row.account_id for row in rows if row.account_id]
        alias_map: dict[str | None, str | None] = {}
        if account_ids_found:
            alias_result = await self._session.execute(
                select(Account.id, Account.alias).where(Account.id.in_(account_ids_found))
            )
            alias_map = {account_id: alias for account_id, alias in alias_result.all()}

        return [
            AccountAggregateRow(
                account_id=row.account_id,
                alias=alias_map.get(row.account_id),
                cost_usd=float(row.cost_usd),
                request_count=int(row.request_count),
            )
            for row in rows
        ]

    async def aggregate_by_useragent(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
    ) -> list[UserAgentAggregateRow]:
        useragent_group_bucket = _useragent_group_bucket_expr()
        conditions = [
            *_report_conditions(start_date, end_date, account_ids, model, useragent_group),
            or_(RequestLog.useragent_group.is_(None), func.trim(RequestLog.useragent_group) != ""),
        ]

        stmt = (
            select(
                useragent_group_bucket.label("useragent_group"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.count().label("request_count"),
            )
            .where(and_(*conditions))
            .group_by(useragent_group_bucket)
            .order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0.0).desc())
        )
        result = await self._session.execute(stmt)
        return [
            UserAgentAggregateRow(
                useragent_group=row.useragent_group,
                cost_usd=float(row.cost_usd),
                request_count=int(row.request_count),
            )
            for row in result.all()
        ]

    async def count_active_accounts(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
    ) -> int:
        conditions = [
            *_report_conditions(start_date, end_date, account_ids, model, useragent_group),
            RequestLog.account_id.is_not(None),
        ]

        result = await self._session.execute(
            select(func.count(func.distinct(RequestLog.account_id))).where(and_(*conditions))
        )
        return int(result.scalar_one() or 0)

    async def earliest_report_activity_at(
        self,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
    ) -> datetime | None:
        conditions = [_normal_traffic_clause()]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)
        useragent_group_clause = _useragent_group_filter_clause(useragent_group)
        if useragent_group_clause is not None:
            conditions.append(useragent_group_clause)

        result = await self._session.execute(select(func.min(RequestLog.requested_at)).where(and_(*conditions)))
        value = result.scalar_one_or_none()
        return value if isinstance(value, datetime) else None


def _report_conditions(
    start_date: datetime,
    end_date: datetime,
    account_ids: list[str] | None,
    model: str | None,
    useragent_group: str | None,
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
    return conditions


def _useragent_group_bucket_expr():
    return case(
        (RequestLog.useragent_group.is_(None), literal(MISSING_USERAGENT_GROUP)),
        else_=RequestLog.useragent_group,
    )


def _useragent_group_filter_clause(useragent_group: str | None):
    if not useragent_group:
        return None
    if useragent_group == MISSING_USERAGENT_GROUP:
        return RequestLog.useragent_group.is_(None)
    return RequestLog.useragent_group == useragent_group


def _normal_traffic_clause():
    return and_(
        or_(RequestLog.source.is_(None), RequestLog.source != _INTERNAL_LIMIT_WARMUP_SOURCE),
        or_(
            RequestLog.request_kind.is_(None),
            RequestLog.request_kind.not_in(_INTERNAL_WARMUP_REQUEST_KINDS),
        ),
    )


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
        ),
    )
    token_count = RequestLog.output_tokens
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
    return (
        select(
            day_ranges_cte.c.report_date,
            func.coalesce(ttft_medians_cte.c.median_ttft_ms, 0.0).label("median_ttft_ms"),
            func.coalesce(tps_medians_cte.c.median_tps, 0.0).label("median_tps"),
        )
        .select_from(
            day_ranges_cte.outerjoin(
                ttft_medians_cte,
                ttft_medians_cte.c.report_date == day_ranges_cte.c.report_date,
            ).outerjoin(
                tps_medians_cte,
                tps_medians_cte.c.report_date == day_ranges_cte.c.report_date,
            )
        )
        .order_by(day_ranges_cte.c.report_date)
    )


def _daily_rows_stmt(
    day_ranges: list[tuple[str, datetime, datetime]],
    account_ids: list[str] | None,
    model: str | None,
    useragent_group: str | None,
):
    useragent_group_clause = _useragent_group_filter_clause(useragent_group)
    day_ranges_cte = _day_ranges_cte(day_ranges)
    return (
        select(
            day_ranges_cte.c.report_date,
            func.count(RequestLog.id).label("requests"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            func.count(func.distinct(RequestLog.account_id)).label("active_accounts"),
            func.coalesce(
                func.sum(case((RequestLog.status != "success", 1), else_=0)),
                0,
            ).label("error_count"),
        )
        .select_from(
            day_ranges_cte.join(
                RequestLog,
                and_(
                    RequestLog.requested_at >= day_ranges_cte.c.day_start,
                    RequestLog.requested_at < day_ranges_cte.c.day_end,
                    _normal_traffic_clause(),
                    *([RequestLog.account_id.in_(account_ids)] if account_ids else []),
                    *([RequestLog.model == model] if model else []),
                    *([useragent_group_clause] if useragent_group_clause is not None else []),
                ),
            )
        )
        .group_by(day_ranges_cte.c.report_date)
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
