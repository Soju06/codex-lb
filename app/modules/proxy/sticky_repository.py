from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Insert

from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, OpenAIPlatformIdentity, StickySession, StickySessionKind
from app.modules.sticky_sessions.schemas import StickySessionSortBy, StickySessionSortDir
from app.modules.upstream_identities.types import CHATGPT_WEB_PROVIDER_KIND, OPENAI_PLATFORM_PROVIDER_KIND


@dataclass(frozen=True, slots=True)
class StickySessionListEntryRecord:
    sticky_session: StickySession
    display_name: str


@dataclass(frozen=True, slots=True)
class StickyRoutingTarget:
    provider_kind: str
    routing_subject_id: str
    account_id: str | None


class StickySessionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_target(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        provider_kind: str,
        max_age_seconds: int | None = None,
    ) -> StickyRoutingTarget | None:
        if not key:
            return None
        row = await self.get_scoped_entry(key, kind=kind, provider_kind=provider_kind)
        if row is None:
            return None
        if max_age_seconds is not None:
            cutoff = utcnow() - timedelta(seconds=max_age_seconds)
            if to_utc_naive(row.updated_at) < cutoff:
                await self.delete_scoped(key, kind=kind, provider_kind=provider_kind)
                return None
        target = self._target_from_row(row)
        if target is None:
            await self.delete_scoped(key, kind=kind, provider_kind=provider_kind)
            return None
        return target

    async def get_account_id(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        max_age_seconds: int | None = None,
    ) -> str | None:
        target = await self.get_target(
            key,
            kind=kind,
            provider_kind=CHATGPT_WEB_PROVIDER_KIND,
            max_age_seconds=max_age_seconds,
        )
        return target.account_id if target is not None else None

    async def get_entry(self, key: str, *, kind: StickySessionKind) -> StickySession | None:
        return await self.get_scoped_entry(key, kind=kind, provider_kind=CHATGPT_WEB_PROVIDER_KIND)

    async def get_scoped_entry(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        provider_kind: str,
    ) -> StickySession | None:
        if not key:
            return None
        statement = select(StickySession).where(
            StickySession.key == key,
            StickySession.kind == kind,
            StickySession.provider_kind == provider_kind,
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def upsert(self, key: str, account_id: str, *, kind: StickySessionKind) -> StickySession:
        return await self.upsert_target(
            key,
            kind=kind,
            provider_kind=CHATGPT_WEB_PROVIDER_KIND,
            routing_subject_id=account_id,
            account_id=account_id,
        )

    async def upsert_target(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        provider_kind: str,
        routing_subject_id: str,
        account_id: str | None = None,
    ) -> StickySession:
        statement = self._build_upsert_statement(
            key,
            kind=kind,
            provider_kind=provider_kind,
            routing_subject_id=routing_subject_id,
            account_id=account_id if provider_kind == CHATGPT_WEB_PROVIDER_KIND else None,
        )
        await self._session.execute(statement)
        await self._session.commit()
        row = await self.get_scoped_entry(key, kind=kind, provider_kind=provider_kind)
        if row is None:
            raise RuntimeError(
                f"StickySession upsert failed for provider_kind={provider_kind!r} key={key!r} kind={kind.value!r}"
            )
        await self._session.refresh(row)
        return row

    async def delete(self, key: str, *, kind: StickySessionKind) -> bool:
        return await self.delete_scoped(key, kind=kind, provider_kind=CHATGPT_WEB_PROVIDER_KIND)

    async def delete_scoped(self, key: str, *, kind: StickySessionKind, provider_kind: str) -> bool:
        if not key:
            return False
        statement = delete(StickySession).where(
            StickySession.key == key,
            StickySession.kind == kind,
            StickySession.provider_kind == provider_kind,
        )
        result = await self._session.execute(statement.returning(StickySession.key))
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def delete_entries(
        self,
        entries: Sequence[tuple[str, StickySessionKind]],
    ) -> list[tuple[str, StickySessionKind]]:
        deleted = await self.delete_entries_scoped([(key, kind, CHATGPT_WEB_PROVIDER_KIND) for key, kind in entries])
        return [(key, kind) for key, kind, _provider_kind in deleted]

    async def delete_entries_scoped(
        self,
        entries: Sequence[tuple[str, StickySessionKind, str]],
    ) -> list[tuple[str, StickySessionKind, str]]:
        targets = {(key, kind, provider_kind) for key, kind, provider_kind in entries if key}
        if not targets:
            return []
        statement = delete(StickySession).where(
            or_(
                *(
                    and_(
                        StickySession.key == key,
                        StickySession.kind == kind,
                        StickySession.provider_kind == provider_kind,
                    )
                    for key, kind, provider_kind in targets
                )
            )
        )
        result = await self._session.execute(
            statement.returning(StickySession.key, StickySession.kind, StickySession.provider_kind)
        )
        await self._session.commit()
        return [(key, kind, provider_kind) for key, kind, provider_kind in result.all()]

    async def delete_by_routing_subject(
        self,
        *,
        provider_kind: str,
        routing_subject_id: str,
    ) -> int:
        if not routing_subject_id:
            return 0
        statement = delete(StickySession).where(
            StickySession.provider_kind == provider_kind,
            StickySession.routing_subject_id == routing_subject_id,
        )
        result = await self._session.execute(statement.returning(StickySession.key))
        await self._session.commit()
        return len(result.all())

    async def list_entry_identifiers(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
        provider_kind: str | None = None,
    ) -> list[tuple[str, StickySessionKind]]:
        rows = await self.list_scoped_entry_identifiers(
            kind=kind,
            updated_before=updated_before,
            account_query=account_query,
            key_query=key_query,
            provider_kind=provider_kind,
        )
        return [(key, kind) for key, kind, _provider_kind in rows]

    async def list_scoped_entry_identifiers(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
        provider_kind: str | None = None,
    ) -> list[tuple[str, StickySessionKind, str]]:
        account_alias, platform_alias, display_name = self._display_name_context()
        statement = (
            self._apply_filters(
                select(StickySession.key, StickySession.kind, StickySession.provider_kind),
                kind=kind,
                updated_before=updated_before,
                account_query=account_query,
                key_query=key_query,
                provider_kind=provider_kind,
                display_name=display_name,
            )
            .outerjoin(
                account_alias,
                and_(
                    StickySession.provider_kind == CHATGPT_WEB_PROVIDER_KIND,
                    account_alias.id == StickySession.account_id,
                ),
            )
            .outerjoin(
                platform_alias,
                and_(
                    StickySession.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND,
                    platform_alias.id == StickySession.routing_subject_id,
                ),
            )
            .order_by(
                StickySession.provider_kind.asc(),
                StickySession.updated_at.desc(),
                StickySession.created_at.desc(),
                StickySession.key.asc(),
            )
        )
        result = await self._session.execute(statement)
        return [(key, kind, provider_kind) for key, kind, provider_kind in result.all()]

    async def list_entries(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
        provider_kind: str | None = None,
        sort_by: StickySessionSortBy = "updated_at",
        sort_dir: StickySessionSortDir = "desc",
        offset: int = 0,
        limit: int | None = None,
    ) -> Sequence[StickySessionListEntryRecord]:
        account_alias, platform_alias, display_name = self._display_name_context()
        order_by = self._build_order_by(sort_by=sort_by, sort_dir=sort_dir, display_name=display_name)
        statement = (
            self._apply_filters(
                select(StickySession, display_name.label("display_name")),
                kind=kind,
                updated_before=updated_before,
                account_query=account_query,
                key_query=key_query,
                provider_kind=provider_kind,
                display_name=display_name,
            )
            .outerjoin(
                account_alias,
                and_(
                    StickySession.provider_kind == CHATGPT_WEB_PROVIDER_KIND,
                    account_alias.id == StickySession.account_id,
                ),
            )
            .outerjoin(
                platform_alias,
                and_(
                    StickySession.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND,
                    platform_alias.id == StickySession.routing_subject_id,
                ),
            )
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
        provider_kind: str | None = None,
    ) -> int:
        account_alias, platform_alias, display_name = self._display_name_context()
        statement = self._apply_filters(
            select(func.count())
            .select_from(StickySession)
            .outerjoin(
                account_alias,
                and_(
                    StickySession.provider_kind == CHATGPT_WEB_PROVIDER_KIND,
                    account_alias.id == StickySession.account_id,
                ),
            )
            .outerjoin(
                platform_alias,
                and_(
                    StickySession.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND,
                    platform_alias.id == StickySession.routing_subject_id,
                ),
            ),
            kind=kind,
            updated_before=updated_before,
            account_query=account_query,
            key_query=key_query,
            provider_kind=provider_kind,
            display_name=display_name,
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

    def _build_upsert_statement(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        provider_kind: str,
        routing_subject_id: str,
        account_id: str | None,
    ) -> Insert:
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_fn = pg_insert
        elif dialect == "sqlite":
            insert_fn = sqlite_insert
        else:
            raise RuntimeError(f"StickySession upsert unsupported for dialect={dialect!r}")
        statement = insert_fn(StickySession).values(
            key=key,
            kind=kind,
            provider_kind=provider_kind,
            routing_subject_id=routing_subject_id,
            account_id=account_id,
        )
        return statement.on_conflict_do_update(
            index_elements=[StickySession.provider_kind, StickySession.kind, StickySession.key],
            set_={
                "account_id": account_id,
                "routing_subject_id": routing_subject_id,
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
        provider_kind: str | None,
        display_name,
    ):
        if kind is not None:
            statement = statement.where(StickySession.kind == kind)
        if updated_before is not None:
            statement = statement.where(StickySession.updated_at < to_utc_naive(updated_before))
        if provider_kind:
            statement = statement.where(StickySession.provider_kind == provider_kind)
        if account_query:
            statement = statement.where(func.lower(display_name).contains(account_query.lower()))
        if key_query:
            statement = statement.where(func.lower(StickySession.key).contains(key_query.lower()))
        return statement

    @staticmethod
    def _build_order_by(
        *,
        sort_by: StickySessionSortBy,
        sort_dir: StickySessionSortDir,
        display_name,
    ):
        sort_column_map = {
            "updated_at": StickySession.updated_at,
            "created_at": StickySession.created_at,
            "account": display_name,
            "key": StickySession.key,
        }
        primary = sort_column_map[sort_by]
        primary_order = primary.asc() if sort_dir == "asc" else primary.desc()
        if sort_by == "updated_at":
            return (
                StickySession.provider_kind.asc(),
                primary_order,
                StickySession.created_at.desc(),
                StickySession.key.asc(),
            )
        if sort_by == "created_at":
            return (
                StickySession.provider_kind.asc(),
                primary_order,
                StickySession.updated_at.desc(),
                StickySession.key.asc(),
            )
        if sort_by == "account":
            return (
                StickySession.provider_kind.asc(),
                primary_order,
                StickySession.updated_at.desc(),
                StickySession.key.asc(),
            )
        return (
            StickySession.provider_kind.asc(),
            primary_order,
            StickySession.updated_at.desc(),
            StickySession.created_at.desc(),
        )

    @staticmethod
    def _display_name_context():
        account_alias = aliased(Account)
        platform_alias = aliased(OpenAIPlatformIdentity)
        display_name = func.coalesce(
            account_alias.email,
            platform_alias.label,
            StickySession.routing_subject_id,
        )
        return account_alias, platform_alias, display_name

    @staticmethod
    def _target_from_row(row: StickySession) -> StickyRoutingTarget | None:
        routing_subject_id = row.routing_subject_id.strip() if row.routing_subject_id else None
        if not routing_subject_id:
            return None
        return StickyRoutingTarget(
            provider_kind=row.provider_kind,
            routing_subject_id=routing_subject_id,
            account_id=row.account_id,
        )
