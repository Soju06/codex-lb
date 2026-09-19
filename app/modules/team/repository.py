from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, RequestLog, TeamMember


@dataclass(frozen=True, slots=True)
class TeamUsageTotals:
    cost_usd: float
    tokens: int


@dataclass(frozen=True, slots=True)
class TeamUsageModelRow:
    model: str
    cost_usd: float
    tokens: int
    requests: int


@dataclass(frozen=True, slots=True)
class TeamUsageDayRow:
    day: date
    cost_usd: float
    tokens: int


def _total_tokens_expression():
    return func.coalesce(RequestLog.input_tokens, 0) + func.coalesce(RequestLog.output_tokens, 0)


class TeamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Member CRUD ──

    async def create(self, row: TeamMember, *, commit: bool = True) -> TeamMember:
        self._session.add(row)
        if commit:
            await self._session.commit()
            await self._session.refresh(row)
        return row

    async def get_by_id(self, member_id: str) -> TeamMember | None:
        result = await self._session.execute(
            select(TeamMember).execution_options(populate_existing=True).where(TeamMember.id == member_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> TeamMember | None:
        result = await self._session.execute(select(TeamMember).where(TeamMember.name == name))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[TeamMember]:
        result = await self._session.execute(select(TeamMember).order_by(TeamMember.name.asc()))
        return list(result.scalars().all())

    async def delete(self, member_id: str) -> bool:
        row = await self.get_by_id(member_id)
        if row is None:
            return False
        await self._session.execute(
            ApiKey.__table__.update().where(ApiKey.member_id == member_id).values(member_id=None)
        )
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    # ── Keys ──

    async def list_keys_by_member(self, member_id: str) -> list[ApiKey]:
        result = await self._session.execute(
            select(ApiKey).where(ApiKey.member_id == member_id).order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_keys_by_members(self, member_ids: list[str]) -> dict[str, list[ApiKey]]:
        if not member_ids:
            return {}
        result = await self._session.execute(
            select(ApiKey).where(ApiKey.member_id.in_(member_ids)).order_by(ApiKey.created_at.desc())
        )
        grouped: dict[str, list[ApiKey]] = {member_id: [] for member_id in member_ids}
        for row in result.scalars().all():
            if row.member_id is not None:
                grouped.setdefault(row.member_id, []).append(row)
        return grouped

    # ── Usage aggregates ──

    async def aggregate_usage(self, member_id: str, *, since: datetime) -> TeamUsageTotals:
        member_key_ids = select(ApiKey.id).where(ApiKey.member_id == member_id)
        stmt = select(
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
            func.coalesce(func.sum(_total_tokens_expression()), 0),
        ).where(
            RequestLog.api_key_id.in_(member_key_ids),
            RequestLog.requested_at >= since,
        )
        cost_usd, tokens = (await self._session.execute(stmt)).one()
        return TeamUsageTotals(cost_usd=float(cost_usd or 0.0), tokens=int(tokens or 0))

    async def aggregate_usage_by_model(self, member_id: str, *, since: datetime) -> list[TeamUsageModelRow]:
        member_key_ids = select(ApiKey.id).where(ApiKey.member_id == member_id)
        stmt = (
            select(
                RequestLog.model,
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                func.coalesce(func.sum(_total_tokens_expression()), 0),
                func.count(RequestLog.id),
            )
            .where(
                RequestLog.api_key_id.in_(member_key_ids),
                RequestLog.requested_at >= since,
            )
            .group_by(RequestLog.model)
            .order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0.0).desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            TeamUsageModelRow(
                model=str(model),
                cost_usd=float(cost_usd or 0.0),
                tokens=int(tokens or 0),
                requests=int(requests or 0),
            )
            for model, cost_usd, tokens, requests in rows
        ]

    async def aggregate_usage_by_day(self, member_id: str, *, since: datetime) -> list[TeamUsageDayRow]:
        member_key_ids = select(ApiKey.id).where(ApiKey.member_id == member_id)
        day_expression = func.date(RequestLog.requested_at)
        stmt = (
            select(
                day_expression,
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                func.coalesce(func.sum(_total_tokens_expression()), 0),
            )
            .where(
                RequestLog.api_key_id.in_(member_key_ids),
                RequestLog.requested_at >= since,
            )
            .group_by(day_expression)
            .order_by(day_expression.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            TeamUsageDayRow(
                day=_coerce_date(day_value),
                cost_usd=float(cost_usd or 0.0),
                tokens=int(tokens or 0),
            )
            for day_value, cost_usd, tokens in rows
        ]


def _coerce_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
