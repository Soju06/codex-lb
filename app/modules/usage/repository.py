from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import Integer, cast, delete, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.types import UsageAggregateRow, UsageTrendBucket
from app.core.utils.time import utcnow
from app.db.models import AdditionalUsageHistory, UsageHistory

_PRIMARY_WINDOW_LITERAL = literal_column("'primary'")


def _normalized_window_expr():
    return func.coalesce(UsageHistory.window, _PRIMARY_WINDOW_LITERAL)


def _window_clause(window: str | None):
    if not window or window == "primary":
        return _normalized_window_expr() == "primary"
    return UsageHistory.window == window


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
            conditions.append(_window_clause(window))
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
        conditions = _window_clause(window)
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

    async def history_since(
        self,
        account_id: str,
        window: str,
        since: datetime,
    ) -> list[UsageHistory]:
        stmt = (
            select(UsageHistory)
            .where(
                UsageHistory.account_id == account_id,
                _window_clause(window),
                UsageHistory.recorded_at >= since,
            )
            .order_by(UsageHistory.recorded_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def bulk_history_since(
        self,
        account_ids: list[str],
        window: str,
        since: datetime,
    ) -> dict[str, list[UsageHistory]]:
        """Fetch usage history for multiple accounts in a single query."""
        if not account_ids:
            return {}
        stmt = (
            select(UsageHistory)
            .where(
                UsageHistory.account_id.in_(account_ids),
                _window_clause(window),
                UsageHistory.recorded_at >= since,
            )
            .order_by(UsageHistory.account_id, UsageHistory.recorded_at.asc())
        )
        result = await self._session.execute(stmt)
        grouped: dict[str, list[UsageHistory]] = {}
        for row in result.scalars().all():
            grouped.setdefault(row.account_id, []).append(row)
        return grouped

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
            conditions.append(_window_clause(window))
        if account_id:
            conditions.append(UsageHistory.account_id == account_id)

        window_expr = _normalized_window_expr()
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
        conditions = _window_clause(window)
        result = await self._session.execute(select(func.max(UsageHistory.window_minutes)).where(conditions))
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None


class AdditionalUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_entry(
        self,
        account_id: str,
        limit_name: str,
        metered_feature: str,
        window: str,
        used_percent: float,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        recorded_at: datetime | None = None,
    ) -> None:
        entry = AdditionalUsageHistory(
            account_id=account_id,
            limit_name=limit_name,
            metered_feature=metered_feature,
            window=window,
            used_percent=used_percent,
            reset_at=reset_at,
            window_minutes=window_minutes,
            recorded_at=recorded_at or utcnow(),
        )
        self._session.add(entry)
        await self._session.commit()

    async def delete_for_account(self, account_id: str) -> None:
        stmt = delete(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == account_id)
        await self._session.execute(stmt)
        await self._session.commit()

    async def delete_for_account_and_limit(self, account_id: str, limit_name: str) -> None:
        stmt = delete(AdditionalUsageHistory).where(
            AdditionalUsageHistory.account_id == account_id,
            AdditionalUsageHistory.limit_name == limit_name,
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def delete_for_account_limit_window(
        self,
        account_id: str,
        limit_name: str,
        window: str,
    ) -> None:
        stmt = delete(AdditionalUsageHistory).where(
            AdditionalUsageHistory.account_id == account_id,
            AdditionalUsageHistory.limit_name == limit_name,
            AdditionalUsageHistory.window == window,
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def latest_by_account(
        self,
        limit_name: str,
        window: str,
    ) -> dict[str, AdditionalUsageHistory]:
        """Returns the most recent entry per account for a given limit_name + window."""
        subq = (
            select(
                AdditionalUsageHistory.id.label("usage_id"),
                func.row_number()
                .over(
                    partition_by=AdditionalUsageHistory.account_id,
                    order_by=(AdditionalUsageHistory.recorded_at.desc(), AdditionalUsageHistory.id.desc()),
                )
                .label("row_number"),
            )
            .where(
                AdditionalUsageHistory.limit_name == limit_name,
                AdditionalUsageHistory.window == window,
            )
            .subquery()
        )
        stmt = (
            select(AdditionalUsageHistory)
            .join(subq, AdditionalUsageHistory.id == subq.c.usage_id)
            .where(subq.c.row_number == 1)
        )
        result = await self._session.execute(stmt)
        return {entry.account_id: entry for entry in result.scalars().all()}

    async def list_limit_names(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        stmt = select(AdditionalUsageHistory.limit_name).distinct()
        if account_ids is not None:
            stmt = stmt.where(AdditionalUsageHistory.account_id.in_(account_ids))
        if since is not None:
            stmt = stmt.where(AdditionalUsageHistory.recorded_at >= since)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def history_since(
        self,
        account_id: str,
        limit_name: str,
        window: str,
        since: datetime,
    ) -> list[AdditionalUsageHistory]:
        """Returns time-series entries for EWMA computation."""
        stmt = (
            select(AdditionalUsageHistory)
            .where(
                AdditionalUsageHistory.account_id == account_id,
                AdditionalUsageHistory.limit_name == limit_name,
                AdditionalUsageHistory.window == window,
                AdditionalUsageHistory.recorded_at >= since,
            )
            .order_by(AdditionalUsageHistory.recorded_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def latest_recorded_at_for_account(self, account_id: str) -> datetime | None:
        """Return the most recent recorded_at for any additional usage entry of this account."""
        stmt = select(func.max(AdditionalUsageHistory.recorded_at)).where(
            AdditionalUsageHistory.account_id == account_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def latest_recorded_at(self) -> datetime | None:
        """Return the most recent recorded_at across all additional usage entries."""
        stmt = select(func.max(AdditionalUsageHistory.recorded_at))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
