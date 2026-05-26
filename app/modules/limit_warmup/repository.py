from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccountLimitWarmup
from app.db.session import sqlite_writer_section


class LimitWarmupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]:
        if not account_ids:
            return {}
        subq = (
            select(
                AccountLimitWarmup.id.label("warmup_id"),
                func.row_number()
                .over(
                    partition_by=AccountLimitWarmup.account_id,
                    order_by=(AccountLimitWarmup.attempted_at.desc(), AccountLimitWarmup.id.desc()),
                )
                .label("row_number"),
            )
            .where(AccountLimitWarmup.account_id.in_(account_ids))
            .subquery()
        )
        stmt = (
            select(AccountLimitWarmup)
            .join(subq, AccountLimitWarmup.id == subq.c.warmup_id)
            .where(subq.c.row_number == 1)
        )
        result = await self._session.execute(stmt)
        return {entry.account_id: entry for entry in result.scalars().all()}

    async def latest_attempt_for_account(self, account_id: str) -> AccountLimitWarmup | None:
        stmt = (
            select(AccountLimitWarmup)
            .where(AccountLimitWarmup.account_id == account_id)
            .order_by(AccountLimitWarmup.attempted_at.desc(), AccountLimitWarmup.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def try_create_attempt(
        self,
        *,
        account_id: str,
        window: str,
        reset_at: int,
        model: str,
        attempted_at: datetime,
        status: str = "pending",
    ) -> AccountLimitWarmup | None:
        existing = await self._existing_attempt(account_id=account_id, window=window, reset_at=reset_at)
        if existing is not None:
            return None

        row = AccountLimitWarmup(
            account_id=account_id,
            window=window,
            reset_at=reset_at,
            status=status,
            model=model,
            attempted_at=attempted_at,
        )
        self._session.add(row)
        try:
            async with sqlite_writer_section():
                await self._session.commit()
                await self._session.refresh(row)
        except IntegrityError:
            await self._session.rollback()
            return None
        return row

    async def complete_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        completed_at: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AccountLimitWarmup | None:
        stmt = (
            update(AccountLimitWarmup)
            .where(AccountLimitWarmup.id == attempt_id)
            .values(
                status=status,
                completed_at=completed_at,
                error_code=error_code,
                error_message=error_message,
                updated_at=completed_at,
            )
            .returning(AccountLimitWarmup.id)
        )
        async with sqlite_writer_section():
            result = await self._session.execute(stmt)
            await self._session.commit()
        if result.scalar_one_or_none() is None:
            return None
        row = await self._session.get(AccountLimitWarmup, attempt_id)
        if row is not None:
            await self._session.refresh(row)
        return row

    async def _existing_attempt(self, *, account_id: str, window: str, reset_at: int) -> AccountLimitWarmup | None:
        stmt = (
            select(AccountLimitWarmup)
            .where(
                AccountLimitWarmup.account_id == account_id,
                AccountLimitWarmup.window == window,
                AccountLimitWarmup.reset_at == reset_at,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
