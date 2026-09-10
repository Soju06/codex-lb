from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, insert, literal, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccountLimitWarmup
from app.db.session import sqlite_writer_section


class LimitWarmupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _dialect_name(self) -> str:
        bind = self._session.get_bind()
        return bind.dialect.name if bind else "sqlite"

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

    async def try_create_attempt(
        self,
        *,
        account_id: str,
        window: str,
        reset_at: int,
        model: str,
        attempted_at: datetime,
        status: str = "pending",
        reset_at_tolerance_seconds: int = 0,
        require_no_prior_attempt: bool = False,
    ) -> AccountLimitWarmup | None:
        tolerance = max(0, reset_at_tolerance_seconds)
        table = AccountLimitWarmup.__table__
        # Single atomic conditional insert: the tolerance-window existence guard
        # and the insert execute as one statement, so the dedup is effective
        # across processes and replicas. On SQLite the statement is atomic under
        # the database-level single-writer lock (the process-local
        # sqlite_writer_section is kept only as a local write throttle). On
        # PostgreSQL a single INSERT .. SELECT is not self-sufficient under READ
        # COMMITTED, so an advisory transaction lock keyed on account serializes
        # both ordinary claims and the cross-window initial-attempt guard.
        duplicate_in_tolerance_window = (
            select(AccountLimitWarmup.id)
            .where(
                AccountLimitWarmup.account_id == account_id,
                AccountLimitWarmup.window == window,
                AccountLimitWarmup.reset_at.between(reset_at - tolerance, reset_at + tolerance),
            )
            .exists()
        )
        insert_conditions = [~duplicate_in_tolerance_window]
        if require_no_prior_attempt:
            prior_account_attempt = (
                select(AccountLimitWarmup.id).where(AccountLimitWarmup.account_id == account_id).exists()
            )
            insert_conditions.append(~prior_account_attempt)
        insert_stmt = (
            insert(AccountLimitWarmup)
            .from_select(
                ["account_id", "window", "reset_at", "status", "model", "attempted_at"],
                select(
                    literal(account_id, type_=table.c.account_id.type),
                    literal(window, type_=table.c.window.type),
                    literal(reset_at, type_=table.c.reset_at.type),
                    literal(status, type_=table.c.status.type),
                    literal(model, type_=table.c.model.type),
                    literal(attempted_at, type_=table.c.attempted_at.type),
                ).where(*insert_conditions),
            )
            .returning(AccountLimitWarmup.id)
        )
        try:
            async with sqlite_writer_section():
                if self._dialect_name() == "postgresql":
                    await self._session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"limit_warmup:{account_id}"},
                    )
                    # Older replicas use only window locks. Take every window
                    # for the account-wide guard, in a fixed order, so their
                    # in-flight inserts are visible before our next statement.
                    lock_windows = (
                        ("monthly", "primary", "primary_idle", "secondary") if require_no_prior_attempt else (window,)
                    )
                    for lock_window in lock_windows:
                        await self._session.execute(
                            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                            {"key": f"limit_warmup:{account_id}:{lock_window}"},
                        )
                inserted_id = await self._session.scalar(insert_stmt)
                await self._session.commit()
        except IntegrityError:
            # Backstop: the exact-tuple unique constraint
            # uq_account_limit_warmups_account_window_reset still catches any
            # duplicate the guard could not see.
            await self._session.rollback()
            return None
        if inserted_id is None:
            return None
        row = await self._session.get(AccountLimitWarmup, inserted_id)
        if row is not None:
            await self._session.refresh(row)
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
