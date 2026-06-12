from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, RequestLog

_INTERNAL_LIMIT_WARMUP_SOURCE = "limit_warmup"
_INTERNAL_WARMUP_REQUEST_KINDS = ("warmup", "limit_warmup")


@dataclass(frozen=True)
class DailyReportLogRow:
    requested_at: datetime
    account_id: str | None
    is_error: bool
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float


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

    async def list_daily_report_logs(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[DailyReportLogRow]:
        conditions = _report_conditions(start_date, end_date, account_ids, model)

        stmt = (
            select(
                RequestLog.requested_at,
                RequestLog.account_id,
                RequestLog.status,
                RequestLog.input_tokens,
                RequestLog.output_tokens,
                RequestLog.cached_input_tokens,
                RequestLog.cost_usd,
            )
            .where(and_(*conditions))
            .order_by(RequestLog.requested_at, RequestLog.request_id)
        )
        result = await self._session.execute(stmt)
        return [
            DailyReportLogRow(
                requested_at=row.requested_at,
                account_id=row.account_id,
                is_error=row.status != "success",
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cached_input_tokens=int(row.cached_input_tokens or 0),
                cost_usd=float(row.cost_usd or 0.0),
            )
            for row in result.all()
        ]

    async def aggregate_summary(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> SummaryAggregateRow:
        conditions = _report_conditions(start_date, end_date, account_ids, model)

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
        return [
            ModelAggregateRow(
                model=row.model,
                cost_usd=float(row.cost_usd),
            )
            for row in result.all()
        ]

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

    async def count_active_accounts(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> int:
        conditions = [
            *_report_conditions(start_date, end_date, account_ids, model),
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
    ) -> datetime | None:
        conditions = [_normal_traffic_clause()]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        result = await self._session.execute(
            select(func.min(RequestLog.requested_at)).where(and_(*conditions))
        )
        value = result.scalar_one_or_none()
        return value if isinstance(value, datetime) else None


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


def _normal_traffic_clause():
    return and_(
        or_(RequestLog.source.is_(None), RequestLog.source != _INTERNAL_LIMIT_WARMUP_SOURCE),
        or_(
            RequestLog.request_kind.is_(None),
            RequestLog.request_kind.not_in(_INTERNAL_WARMUP_REQUEST_KINDS),
        ),
    )
