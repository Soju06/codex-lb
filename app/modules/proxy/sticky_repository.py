from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Insert

from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, StickySession, StickySessionKind
from app.modules.sticky_sessions.schemas import StickySessionSortBy, StickySessionSortDir


@dataclass(frozen=True, slots=True)
class StickySessionListEntryRecord:
    sticky_session: StickySession
    display_name: str


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

    async def delete_entries(
        self,
        entries: Sequence[tuple[str, StickySessionKind]],
    ) -> list[tuple[str, StickySessionKind]]:
        targets = {(key, kind) for key, kind in entries if key}
        if not targets:
            return []
        statement = delete(StickySession).where(
            or_(*(and_(StickySession.key == key, StickySession.kind == kind) for key, kind in targets))
        )
        result = await self._session.execute(statement.returning(StickySession.key, StickySession.kind))
        await self._session.commit()
        return [(key, kind) for key, kind in result.all()]

    async def list_entry_identifiers(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
    ) -> list[tuple[str, StickySessionKind]]:
        statement = (
            self._apply_filters(
                select(StickySession.key, StickySession.kind),
                kind=kind,
                updated_before=updated_before,
                account_query=account_query,
                key_query=key_query,
            )
            .join(Account, Account.id == StickySession.account_id)
            .order_by(
                StickySession.updated_at.desc(),
                StickySession.created_at.desc(),
                StickySession.key.asc(),
            )
        )
        result = await self._session.execute(statement)
        return [(key, kind) for key, kind in result.all()]

    async def list_entries(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
        sort_by: StickySessionSortBy = "updated_at",
        sort_dir: StickySessionSortDir = "desc",
        offset: int = 0,
        limit: int | None = None,
    ) -> Sequence[StickySessionListEntryRecord]:
        order_by = self._build_order_by(sort_by=sort_by, sort_dir=sort_dir)
        statement = (
            self._apply_filters(
                select(StickySession, Account.email),
                kind=kind,
                updated_before=updated_before,
                account_query=account_query,
                key_query=key_query,
            )
            .join(Account, Account.id == StickySession.account_id)
            .order_by(*order_by)
        )
        if offset > 0:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return [
            StickySessionListEntryRecord(sticky_session=sticky_session, display_name=display_name)
            for sticky_session, display_name in result.all()
        ]

    async def count_entries(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
    ) -> int:
        statement = self._apply_filters(
            select(func.count()).select_from(StickySession).join(Account, Account.id == StickySession.account_id),
            kind=kind,
            updated_before=updated_before,
            account_query=account_query,
            key_query=key_query,
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
        account_query: str | None,
        key_query: str | None,
    ):
        if kind is not None:
            statement = statement.where(StickySession.kind == kind)
        if updated_before is not None:
            statement = statement.where(StickySession.updated_at < to_utc_naive(updated_before))
        if account_query:
            statement = statement.where(func.lower(Account.email).contains(account_query.lower()))
        if key_query:
            statement = statement.where(func.lower(StickySession.key).contains(key_query.lower()))
        return statement

    @staticmethod
    def _build_order_by(
        *,
        sort_by: StickySessionSortBy,
        sort_dir: StickySessionSortDir,
    ):
        sort_column_map = {
            "updated_at": StickySession.updated_at,
            "created_at": StickySession.created_at,
            "account": Account.email,
            "key": StickySession.key,
        }
        primary = sort_column_map[sort_by]
        primary_order = primary.asc() if sort_dir == "asc" else primary.desc()
        if sort_by == "updated_at":
            return (
                primary_order,
                StickySession.created_at.desc(),
                StickySession.key.asc(),
            )
        if sort_by == "created_at":
            return (
                primary_order,
                StickySession.updated_at.desc(),
                StickySession.key.asc(),
            )
        if sort_by == "account":
            return (
                primary_order,
                StickySession.updated_at.desc(),
                StickySession.key.asc(),
            )
        return (
            primary_order,
            StickySession.updated_at.desc(),
            StickySession.created_at.desc(),
        )
