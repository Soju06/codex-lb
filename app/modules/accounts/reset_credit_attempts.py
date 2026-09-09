"""Commit reset intent before network I/O and serialize competing redemptions."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import ResetCreditAttempt
from app.db.session import sqlite_writer_section

RESET_ATTEMPT_LEASE_SECONDS = 120


class ResetCreditAttemptsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active(self) -> ResetCreditAttempt | None:
        result = await self._session.execute(
            select(ResetCreditAttempt)
            .where(ResetCreditAttempt.active_slot == 1)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def latest_applied_at(self) -> datetime | None:
        result = await self._session.execute(
            select(func.max(ResetCreditAttempt.applied_at)).where(ResetCreditAttempt.trigger == "auto")
        )
        return result.scalar_one()

    async def create(self, account_id: str, credit_id: str | None, trigger: str) -> ResetCreditAttempt | None:
        now = utcnow()
        row = ResetCreditAttempt(
            id=str(uuid4()),
            active_slot=1,
            account_id=account_id,
            credit_id=credit_id,
            trigger=trigger,
            state="pending",
            created_at=now,
            lease_until=now + timedelta(seconds=RESET_ATTEMPT_LEASE_SECONDS),
            windows_reset=0,
            lease_owner=str(uuid4()),
        )
        self._session.add(row)
        try:
            async with sqlite_writer_section():
                await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            return None
        return row

    async def acquire(self, attempt: ResetCreditAttempt) -> bool:
        now = utcnow()
        owner = str(uuid4())
        stmt = (
            update(ResetCreditAttempt)
            .where(
                ResetCreditAttempt.id == attempt.id,
                ResetCreditAttempt.active_slot == 1,
                ResetCreditAttempt.lease_until <= now,
            )
            .values(lease_until=now + timedelta(seconds=RESET_ATTEMPT_LEASE_SECONDS), lease_owner=owner)
            .returning(ResetCreditAttempt.id)
            .execution_options(synchronize_session="fetch")
        )
        async with sqlite_writer_section():
            result = await self._session.execute(stmt)
            claimed = result.scalar_one_or_none() is not None
            await self._session.commit()
        return claimed

    async def applied(self, attempt: ResetCreditAttempt, code: str, windows_reset: int) -> None:
        stmt = (
            update(ResetCreditAttempt)
            .where(
                ResetCreditAttempt.id == attempt.id,
                ResetCreditAttempt.active_slot == 1,
                ResetCreditAttempt.lease_owner == attempt.lease_owner,
            )
            .values(state="applied", result_code=code, windows_reset=windows_reset, applied_at=utcnow())
            .returning(ResetCreditAttempt.id)
        )
        async with sqlite_writer_section():
            result = await self._session.execute(stmt)
            if result.scalar_one_or_none() is None:
                await self._session.rollback()
                raise RuntimeError("Reset attempt lease was superseded; reconciliation required")
            await self._session.commit()

    async def settle(self, attempt: ResetCreditAttempt, code: str) -> None:
        stmt = (
            update(ResetCreditAttempt)
            .where(
                ResetCreditAttempt.id == attempt.id,
                ResetCreditAttempt.active_slot == 1,
                ResetCreditAttempt.lease_owner == attempt.lease_owner,
            )
            .values(state="settled", result_code=code, active_slot=None)
            .returning(ResetCreditAttempt.id)
        )
        async with sqlite_writer_section():
            result = await self._session.execute(stmt)
            if result.scalar_one_or_none() is None:
                await self._session.rollback()
                raise RuntimeError("Reset attempt lease was superseded; reconciliation required")
            await self._session.commit()
