from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, and_, cast, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, RequestLog

_INTERNAL_LIMIT_WARMUP_SOURCE = "limit_warmup"
_INTERNAL_WARMUP_REQUEST_KINDS = ("warmup", "limit_warmup")


@dataclass(frozen=True)
class DailyAggregateRow:
    date: str
    request_count: int
    error_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
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

    async def aggregate_daily(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[DailyAggregateRow]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            date_expr = func.date(RequestLog.requested_at)
        else:
            date_expr = func.strftime("%Y-%m-%d", RequestLog.requested_at)
        date_col = date_expr.label("date")

        conditions = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            _normal_traffic_clause(),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        stmt = (
            select(
                date_col,
                func.count().label("request_count"),
                func.coalesce(
                    func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)),
                    0,
                ).label("error_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.count(func.distinct(RequestLog.account_id)).label("active_accounts"),
            )
            .where(and_(*conditions))
            .group_by(date_col)
            .order_by(date_col)
        )
        result = await self._session.execute(stmt)
        return [
            DailyAggregateRow(
                date=str(row.date),
                request_count=int(row.request_count),
                error_count=int(row.error_count),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                cached_input_tokens=int(row.cached_input_tokens),
                cost_usd=float(row.cost_usd),
                active_accounts=int(row.active_accounts),
            )
            for row in result.all()
        ]

    async def aggregate_by_model(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[ModelAggregateRow]:
        conditions = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            _normal_traffic_clause(),
            RequestLog.model.is_not(None),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

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
        conditions = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            _normal_traffic_clause(),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

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
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            _normal_traffic_clause(),
            RequestLog.account_id.is_not(None),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        result = await self._session.execute(
            select(func.count(func.distinct(RequestLog.account_id))).where(and_(*conditions))
        )
        return int(result.scalar_one() or 0)


def _normal_traffic_clause():
    return and_(
        or_(RequestLog.source.is_(None), RequestLog.source != _INTERNAL_LIMIT_WARMUP_SOURCE),
        or_(
            RequestLog.request_kind.is_(None),
            RequestLog.request_kind.not_in(_INTERNAL_WARMUP_REQUEST_KINDS),
        ),
    )
