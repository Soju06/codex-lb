from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.types import UsageAggregateRow, UsageTrendBucket
from app.core.utils.time import utcnow
from app.db.models import UsageHistory


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_entry(
        self,
        account_id: str,
        used_percent: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        recorded_at: datetime | None = None,
        window: str | None = None,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        credits_has: bool | None = None,
        credits_unlimited: bool | None = None,
        credits_balance: float | None = None,
    ) -> UsageHistory:
        entry = UsageHistory(
            account_id=account_id,
            used_percent=used_percent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            window=window,
            reset_at=reset_at,
            window_minutes=window_minutes,
            credits_has=credits_has,
            credits_unlimited=credits_unlimited,
            credits_balance=credits_balance,
            recorded_at=recorded_at or utcnow(),
        )
        self._session.add(entry)
        await self._session.commit()
        await self._session.refresh(entry)
        return entry

    async def aggregate_since(
        self,
        since: datetime,
        window: str | None = None,
    ) -> list[UsageAggregateRow]:
        conditions = [UsageHistory.recorded_at >= since]
        if window:
            if window == "primary":
                conditions.append(or_(UsageHistory.window == "primary", UsageHistory.window.is_(None)))
            else:
                conditions.append(UsageHistory.window == window)
        stmt = (
            select(
                UsageHistory.account_id,
                func.avg(UsageHistory.used_percent).label("used_percent_avg"),
                func.sum(UsageHistory.input_tokens).label("input_tokens_sum"),
                func.sum(UsageHistory.output_tokens).label("output_tokens_sum"),
                func.count(UsageHistory.id).label("samples"),
                func.max(UsageHistory.recorded_at).label("last_recorded_at"),
                func.max(UsageHistory.reset_at).label("reset_at_max"),
                func.max(UsageHistory.window_minutes).label("window_minutes_max"),
            )
            .where(*conditions)
            .group_by(UsageHistory.account_id)
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        return [
            UsageAggregateRow(
                account_id=row.account_id,
                used_percent_avg=float(row.used_percent_avg) if row.used_percent_avg is not None else None,
                input_tokens_sum=int(row.input_tokens_sum) if row.input_tokens_sum is not None else None,
                output_tokens_sum=int(row.output_tokens_sum) if row.output_tokens_sum is not None else None,
                samples=int(row.samples),
                last_recorded_at=row.last_recorded_at,
                reset_at_max=int(row.reset_at_max) if row.reset_at_max is not None else None,
                window_minutes_max=int(row.window_minutes_max) if row.window_minutes_max is not None else None,
            )
            for row in rows
        ]

    async def latest_by_account(self, window: str | None = None) -> dict[str, UsageHistory]:
        if window:
            if window == "primary":
                conditions = or_(UsageHistory.window == "primary", UsageHistory.window.is_(None))
            else:
                conditions = UsageHistory.window == window
        else:
            conditions = or_(UsageHistory.window == "primary", UsageHistory.window.is_(None))
        subq = (
            select(
                UsageHistory.id.label("usage_id"),
                func.row_number()
                .over(
                    partition_by=UsageHistory.account_id,
                    order_by=(UsageHistory.recorded_at.desc(), UsageHistory.id.desc()),
                )
                .label("row_number"),
            )
            .where(conditions)
            .subquery()
        )
        stmt = select(UsageHistory).join(subq, UsageHistory.id == subq.c.usage_id).where(subq.c.row_number == 1)
        result = await self._session.execute(stmt)
        return {entry.account_id: entry for entry in result.scalars().all()}

    async def trends_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
        window: str | None = None,
        account_id: str | None = None,
    ) -> list[UsageTrendBucket]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            bucket_expr = func.floor(func.extract("epoch", UsageHistory.recorded_at) / bucket_seconds) * bucket_seconds
        else:
            epoch_col = cast(func.strftime("%s", UsageHistory.recorded_at), Integer)
            bucket_expr = cast(epoch_col / bucket_seconds, Integer) * bucket_seconds
        bucket_col = bucket_expr.label("bucket_epoch")

        conditions: list = [UsageHistory.recorded_at >= since]
        if window:
            if window == "primary":
                conditions.append(or_(UsageHistory.window == "primary", UsageHistory.window.is_(None)))
            else:
                conditions.append(UsageHistory.window == window)
        if account_id:
            conditions.append(UsageHistory.account_id == account_id)

        window_expr = func.coalesce(UsageHistory.window, "primary")
        stmt = (
            select(
                bucket_col,
                UsageHistory.account_id,
                window_expr.label("window"),
                func.avg(UsageHistory.used_percent).label("avg_used_percent"),
                func.count(UsageHistory.id).label("samples"),
            )
            .where(*conditions)
            .group_by(
                bucket_col,
                UsageHistory.account_id,
                window_expr,
            )
            .order_by(bucket_col)
        )
        result = await self._session.execute(stmt)
        return [
            UsageTrendBucket(
                bucket_epoch=int(row.bucket_epoch),
                account_id=row.account_id,
                window=row.window,
                avg_used_percent=float(row.avg_used_percent) if row.avg_used_percent is not None else 0.0,
                samples=int(row.samples),
            )
            for row in result.all()
        ]

    async def latest_window_minutes(self, window: str) -> int | None:
        if window == "primary":
            conditions = or_(UsageHistory.window == "primary", UsageHistory.window.is_(None))
        else:
            conditions = UsageHistory.window == window
        result = await self._session.execute(select(func.max(UsageHistory.window_minutes)).where(conditions))
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None
