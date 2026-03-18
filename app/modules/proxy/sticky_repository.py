from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Insert, func

from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import StickySession, StickySessionKind


class StickySessionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account_id(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        max_age_seconds: int | None = None,
    ) -> str | None:
        if not key:
            return None
        row = await self.get_entry(key, kind=kind)
        if row is None:
            return None
        if max_age_seconds is not None:
            cutoff = utcnow() - timedelta(seconds=max_age_seconds)
            if to_utc_naive(row.updated_at) < cutoff:
                await self.delete(key, kind=kind)
                return None
        return row.account_id

    async def get_entry(self, key: str, *, kind: StickySessionKind) -> StickySession | None:
        if not key:
            return None
        statement = select(StickySession).where(
            StickySession.key == key,
            StickySession.kind == kind,
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def upsert(self, key: str, account_id: str, *, kind: StickySessionKind) -> StickySession:
        statement = self._build_upsert_statement(key, account_id, kind)
        await self._session.execute(statement)
        await self._session.commit()
        row = await self.get_entry(key, kind=kind)
        if row is None:
            raise RuntimeError(f"StickySession upsert failed for key={key!r} kind={kind.value!r}")
        await self._session.refresh(row)
        return row

    async def delete(self, key: str, *, kind: StickySessionKind) -> bool:
        if not key:
            return False
        statement = delete(StickySession).where(
            StickySession.key == key,
            StickySession.kind == kind,
        )
        result = await self._session.execute(statement.returning(StickySession.key))
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def list_entries(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[StickySession]:
        statement = self._apply_filters(
            select(StickySession),
            kind=kind,
            updated_before=updated_before,
        ).order_by(
            StickySession.updated_at.desc(),
            StickySession.created_at.desc(),
            StickySession.key.asc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def count_entries(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
    ) -> int:
        statement = self._apply_filters(
            select(func.count()).select_from(StickySession),
            kind=kind,
            updated_before=updated_before,
        )
        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def purge_prompt_cache_before(self, cutoff: datetime) -> int:
        return await self.purge_before(cutoff, kind=StickySessionKind.PROMPT_CACHE)

    async def purge_before(self, cutoff: datetime, *, kind: StickySessionKind | None = None) -> int:
        stmt = delete(StickySession).where(StickySession.updated_at < to_utc_naive(cutoff))
        if kind is not None:
            stmt = stmt.where(StickySession.kind == kind)
        result = await self._session.execute(stmt.returning(StickySession.key))
        deleted = len(result.scalars().all())
        await self._session.commit()
        return deleted

    def _build_upsert_statement(self, key: str, account_id: str, kind: StickySessionKind) -> Insert:
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_fn = pg_insert
        elif dialect == "sqlite":
            insert_fn = sqlite_insert
        else:
            raise RuntimeError(f"StickySession upsert unsupported for dialect={dialect!r}")
        statement = insert_fn(StickySession).values(key=key, account_id=account_id, kind=kind)
        return statement.on_conflict_do_update(
            index_elements=[StickySession.key, StickySession.kind],
            set_={
                "account_id": account_id,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _apply_filters(
        statement,
        *,
        kind: StickySessionKind | None,
        updated_before: datetime | None,
    ):
        if kind is not None:
            statement = statement.where(StickySession.kind == kind)
        if updated_before is not None:
            statement = statement.where(StickySession.updated_at < to_utc_naive(updated_before))
        return statement
