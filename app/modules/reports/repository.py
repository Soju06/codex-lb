from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import batched
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, literal, or_, select, union, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, RequestLog, RequestLogDailyAggregate

_INTERNAL_LIMIT_WARMUP_SOURCE = "limit_warmup"
_INTERNAL_WARMUP_REQUEST_KINDS = ("warmup", "limit_warmup")
_SQLITE_COMPOUND_SELECT_LIMIT = 500
MAX_DAILY_REPORT_DAYS = 730


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


@dataclass(frozen=True)
class AccountAggregateRow:
    account_id: str | None
    alias: str | None
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
            stmt = _daily_rows_stmt(list(day_ranges_batch), account_ids, model)
            result = await self._session.execute(stmt)
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
                )
                for row in result.all()
            )
        aggregate_rows = await self._aggregate_daily_rollup_rows(start_date, end_date, account_ids, model)
        return _merge_daily_rows([*rows, *aggregate_rows])

    async def aggregate_summary(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> SummaryAggregateRow:
        conditions = _report_conditions(start_date, end_date, account_ids, model)

        raw_result = await self._session.execute(
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
            ).where(and_(*conditions))
        )
        raw_row = raw_result.one()
        aggregate_row = await self._aggregate_summary_rollups(start_date, end_date, account_ids, model)
        return SummaryAggregateRow(
            total_cost_usd=float(raw_row.total_cost_usd or 0.0) + aggregate_row.total_cost_usd,
            total_input_tokens=int(raw_row.total_input_tokens or 0) + aggregate_row.total_input_tokens,
            total_output_tokens=int(raw_row.total_output_tokens or 0) + aggregate_row.total_output_tokens,
            total_cached_tokens=int(raw_row.total_cached_tokens or 0) + aggregate_row.total_cached_tokens,
            total_requests=int(raw_row.total_requests or 0) + aggregate_row.total_requests,
            total_errors=int(raw_row.total_errors or 0) + aggregate_row.total_errors,
            active_accounts=await self._count_active_accounts_with_rollups(start_date, end_date, account_ids, model),
        )

    async def aggregate_by_model(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[ModelAggregateRow]:
        conditions = [
            *_report_conditions(start_date, end_date, account_ids, model),
            RequestLog.model.is_not(None),
        ]

        stmt = (
            select(
                RequestLog.model,
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            )
            .where(and_(*conditions))
            .group_by(RequestLog.model)
            .order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0.0).desc())
        )
        result = await self._session.execute(stmt)
        rows = [
            ModelAggregateRow(
                model=row.model,
                cost_usd=float(row.cost_usd),
            )
            for row in result.all()
        ]
        rows.extend(await self._aggregate_model_rollups(start_date, end_date, account_ids, model))
        return _merge_model_rows(rows)

    async def aggregate_by_account(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[AccountAggregateRow]:
        conditions = _report_conditions(start_date, end_date, account_ids, model)

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
        rollup_rows = await self._aggregate_account_rollups(start_date, end_date, account_ids, model)

        account_ids_found = [row.account_id for row in rows if row.account_id]
        account_ids_found.extend(row.account_id for row in rollup_rows if row.account_id)
        alias_map: dict[str | None, str | None] = {}
        if account_ids_found:
            alias_result = await self._session.execute(
                select(Account.id, Account.alias).where(Account.id.in_(account_ids_found))
            )
            alias_map = {account_id: alias for account_id, alias in alias_result.all()}

        merged = [
            AccountAggregateRow(
                account_id=row.account_id,
                alias=alias_map.get(row.account_id),
                cost_usd=float(row.cost_usd),
                request_count=int(row.request_count),
            )
            for row in rows
        ]
        merged.extend(
            AccountAggregateRow(
                account_id=row.account_id,
                alias=alias_map.get(row.account_id),
                cost_usd=row.cost_usd,
                request_count=row.request_count,
            )
            for row in rollup_rows
        )
        return _merge_account_rows(merged)

    async def count_active_accounts(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> int:
        return await self._count_active_accounts_with_rollups(start_date, end_date, account_ids, model)

    async def earliest_report_activity_at(
        self,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> datetime | None:
        conditions = [_normal_traffic_clause()]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        result = await self._session.execute(select(func.min(RequestLog.requested_at)).where(and_(*conditions)))
        raw_value = result.scalar_one_or_none()
        aggregate_conditions = [_normal_traffic_rollup_clause()]
        if account_ids:
            aggregate_conditions.append(RequestLogDailyAggregate.account_id.in_(account_ids))
        if model:
            aggregate_conditions.append(RequestLogDailyAggregate.model == model)
        aggregate_result = await self._session.execute(
            select(func.min(RequestLogDailyAggregate.bucket_date)).where(and_(*aggregate_conditions))
        )
        aggregate_value = aggregate_result.scalar_one_or_none()
        aggregate_datetime = (
            datetime.combine(aggregate_value, datetime.min.time()) if isinstance(aggregate_value, date) else None
        )
        candidates = [value for value in (raw_value, aggregate_datetime) if isinstance(value, datetime)]
        return min(candidates) if candidates else None

    async def _aggregate_daily_rollup_rows(
        self,
        start_date: date,
        end_date: date,
        account_ids: list[str] | None,
        model: str | None,
    ) -> list[DailyReportAggregateRow]:
        conditions = _report_rollup_conditions_for_dates(start_date, end_date, account_ids, model)
        result = await self._session.execute(
            select(
                RequestLogDailyAggregate.bucket_date,
                func.coalesce(func.sum(RequestLogDailyAggregate.request_count), 0).label("requests"),
                func.coalesce(func.sum(RequestLogDailyAggregate.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLogDailyAggregate.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLogDailyAggregate.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLogDailyAggregate.cost_usd), 0.0).label("cost_usd"),
                func.count(func.distinct(RequestLogDailyAggregate.account_id)).label("active_accounts"),
                func.coalesce(func.sum(RequestLogDailyAggregate.error_count), 0).label("error_count"),
            )
            .where(and_(*conditions))
            .group_by(RequestLogDailyAggregate.bucket_date)
            .order_by(RequestLogDailyAggregate.bucket_date)
        )
        return [
            DailyReportAggregateRow(
                date=row.bucket_date.isoformat(),
                requests=int(row.requests or 0),
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cached_input_tokens=int(row.cached_input_tokens or 0),
                cost_usd=float(row.cost_usd or 0.0),
                active_accounts=int(row.active_accounts or 0),
                error_count=int(row.error_count or 0),
            )
            for row in result.all()
        ]

    async def _aggregate_summary_rollups(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None,
        model: str | None,
    ) -> SummaryAggregateRow:
        conditions = _report_rollup_conditions(start_date, end_date, account_ids, model)
        result = await self._session.execute(
            select(
                func.coalesce(func.sum(RequestLogDailyAggregate.cost_usd), 0.0).label("total_cost_usd"),
                func.coalesce(func.sum(RequestLogDailyAggregate.input_tokens), 0).label("total_input_tokens"),
                func.coalesce(func.sum(RequestLogDailyAggregate.output_tokens), 0).label("total_output_tokens"),
                func.coalesce(func.sum(RequestLogDailyAggregate.cached_input_tokens), 0).label("total_cached_tokens"),
                func.coalesce(func.sum(RequestLogDailyAggregate.request_count), 0).label("total_requests"),
                func.coalesce(func.sum(RequestLogDailyAggregate.error_count), 0).label("total_errors"),
                func.count(func.distinct(RequestLogDailyAggregate.account_id)).label("active_accounts"),
            ).where(and_(*conditions))
        )
        row = result.one()
        return SummaryAggregateRow(
            total_cost_usd=float(row.total_cost_usd or 0.0),
            total_input_tokens=int(row.total_input_tokens or 0),
            total_output_tokens=int(row.total_output_tokens or 0),
            total_cached_tokens=int(row.total_cached_tokens or 0),
            total_requests=int(row.total_requests or 0),
            total_errors=int(row.total_errors or 0),
            active_accounts=int(row.active_accounts or 0),
        )

    async def _aggregate_model_rollups(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None,
        model: str | None,
    ) -> list[ModelAggregateRow]:
        conditions = [
            *_report_rollup_conditions(start_date, end_date, account_ids, model),
            RequestLogDailyAggregate.model.is_not(None),
        ]
        result = await self._session.execute(
            select(
                RequestLogDailyAggregate.model,
                func.coalesce(func.sum(RequestLogDailyAggregate.cost_usd), 0.0).label("cost_usd"),
            )
            .where(and_(*conditions))
            .group_by(RequestLogDailyAggregate.model)
        )
        return [ModelAggregateRow(model=row.model, cost_usd=float(row.cost_usd or 0.0)) for row in result.all()]

    async def _aggregate_account_rollups(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None,
        model: str | None,
    ) -> list[AccountAggregateRow]:
        conditions = _report_rollup_conditions(start_date, end_date, account_ids, model)
        result = await self._session.execute(
            select(
                RequestLogDailyAggregate.account_id,
                func.coalesce(func.sum(RequestLogDailyAggregate.cost_usd), 0.0).label("cost_usd"),
                func.coalesce(func.sum(RequestLogDailyAggregate.request_count), 0).label("request_count"),
            )
            .where(and_(*conditions))
            .group_by(RequestLogDailyAggregate.account_id)
        )
        return [
            AccountAggregateRow(
                account_id=row.account_id,
                alias=None,
                cost_usd=float(row.cost_usd or 0.0),
                request_count=int(row.request_count or 0),
            )
            for row in result.all()
        ]

    async def _count_active_accounts_with_rollups(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None,
        model: str | None,
    ) -> int:
        raw_conditions = [
            *_report_conditions(start_date, end_date, account_ids, model),
            RequestLog.account_id.is_not(None),
        ]
        rollup_conditions = [
            *_report_rollup_conditions(start_date, end_date, account_ids, model),
            RequestLogDailyAggregate.account_id.is_not(None),
        ]
        raw_accounts = select(RequestLog.account_id.label("account_id")).where(and_(*raw_conditions))
        rollup_accounts = select(RequestLogDailyAggregate.account_id.label("account_id")).where(
            and_(*rollup_conditions)
        )
        account_union = union(raw_accounts, rollup_accounts).subquery()
        result = await self._session.execute(select(func.count()).select_from(account_union))
        return int(result.scalar_one() or 0)


def _report_conditions(
    start_date: datetime,
    end_date: datetime,
    account_ids: list[str] | None,
    model: str | None,
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
    return conditions


def _report_rollup_conditions(
    start_date: datetime,
    end_date: datetime,
    account_ids: list[str] | None,
    model: str | None,
) -> list:
    start_bucket, end_bucket = _rollup_bucket_date_range(start_date, end_date)
    return _report_rollup_conditions_for_dates(start_bucket, end_bucket, account_ids, model)


def _report_rollup_conditions_for_dates(
    start_date: date,
    end_date: date,
    account_ids: list[str] | None,
    model: str | None,
) -> list:
    conditions = [
        RequestLogDailyAggregate.bucket_date >= start_date,
        RequestLogDailyAggregate.bucket_date <= end_date,
        _normal_traffic_rollup_clause(),
    ]
    if account_ids:
        conditions.append(RequestLogDailyAggregate.account_id.in_(account_ids))
    if model:
        conditions.append(RequestLogDailyAggregate.model == model)
    return conditions


def _rollup_bucket_date_range(start_date: datetime, end_date: datetime) -> tuple[date, date]:
    start_bucket = start_date.date()
    end_bucket = end_date.date()
    if end_date.time() == datetime.min.time():
        end_bucket = end_bucket - timedelta(days=1)
    return start_bucket, end_bucket


def _normal_traffic_clause():
    return and_(
        or_(RequestLog.source.is_(None), RequestLog.source != _INTERNAL_LIMIT_WARMUP_SOURCE),
        or_(
            RequestLog.request_kind.is_(None),
            RequestLog.request_kind.not_in(_INTERNAL_WARMUP_REQUEST_KINDS),
        ),
    )


def _normal_traffic_rollup_clause():
    return and_(
        or_(
            RequestLogDailyAggregate.source.is_(None),
            RequestLogDailyAggregate.source != _INTERNAL_LIMIT_WARMUP_SOURCE,
        ),
        or_(
            RequestLogDailyAggregate.request_kind.is_(None),
            RequestLogDailyAggregate.request_kind.not_in(_INTERNAL_WARMUP_REQUEST_KINDS),
        ),
    )


def _merge_daily_rows(rows: list[DailyReportAggregateRow]) -> list[DailyReportAggregateRow]:
    merged: dict[str, DailyReportAggregateRow] = {}
    for row in rows:
        existing = merged.get(row.date)
        if existing is None:
            merged[row.date] = row
            continue
        merged[row.date] = DailyReportAggregateRow(
            date=row.date,
            requests=existing.requests + row.requests,
            input_tokens=existing.input_tokens + row.input_tokens,
            output_tokens=existing.output_tokens + row.output_tokens,
            cached_input_tokens=existing.cached_input_tokens + row.cached_input_tokens,
            cost_usd=existing.cost_usd + row.cost_usd,
            active_accounts=existing.active_accounts + row.active_accounts,
            error_count=existing.error_count + row.error_count,
        )
    return [merged[key] for key in sorted(merged)]


def _merge_model_rows(rows: list[ModelAggregateRow]) -> list[ModelAggregateRow]:
    totals: dict[str, float] = {}
    for row in rows:
        totals[row.model] = totals.get(row.model, 0.0) + row.cost_usd
    return [
        ModelAggregateRow(model=model, cost_usd=cost_usd)
        for model, cost_usd in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


def _merge_account_rows(rows: list[AccountAggregateRow]) -> list[AccountAggregateRow]:
    totals: dict[str | None, AccountAggregateRow] = {}
    for row in rows:
        existing = totals.get(row.account_id)
        if existing is None:
            totals[row.account_id] = row
            continue
        totals[row.account_id] = AccountAggregateRow(
            account_id=row.account_id,
            alias=existing.alias or row.alias,
            cost_usd=existing.cost_usd + row.cost_usd,
            request_count=existing.request_count + row.request_count,
        )
    return sorted(totals.values(), key=lambda item: item.cost_usd, reverse=True)


def _daily_rows_stmt(
    day_ranges: list[tuple[str, datetime, datetime]],
    account_ids: list[str] | None,
    model: str | None,
):
    day_range_rows = [
        select(
            literal(report_date).label("report_date"),
            literal(day_start).label("day_start"),
            literal(day_end).label("day_end"),
        )
        for report_date, day_start, day_end in day_ranges
    ]
    day_ranges_query = day_range_rows[0] if len(day_range_rows) == 1 else union_all(*day_range_rows)
    day_ranges_cte = day_ranges_query.cte("report_days")
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
