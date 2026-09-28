from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Insert

from app.db.dialect_sql import is_mysql
from app.db.models import CapabilityLineageMarker
from app.db.session import sqlite_writer_section
from app.modules.proxy.capability_lineage import (
    CapabilityLineageAlias,
    capability_lineage_marker_hashes,
)


class CapabilityLineageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_required(
        self,
        *,
        capability: str,
        api_key_scope: str,
        aliases: Collection[CapabilityLineageAlias],
    ) -> bool:
        marker_hashes = capability_lineage_marker_hashes(
            capability=capability,
            api_key_scope=api_key_scope,
            aliases=aliases,
        )
        if not marker_hashes:
            return False
        statement = (
            select(CapabilityLineageMarker.marker_hash)
            .where(CapabilityLineageMarker.marker_hash.in_(marker_hashes))
            .limit(1)
        )
        required = (await self._session.execute(statement)).scalar_one_or_none() is not None
        # End SQLite's read snapshot before a caller upgrades this dedicated
        # repository session into a writer while another connection commits.
        await self._session.commit()
        return required

    async def require(
        self,
        *,
        capability: str,
        api_key_scope: str,
        aliases: Collection[CapabilityLineageAlias],
    ) -> tuple[str, ...]:
        marker_hashes = capability_lineage_marker_hashes(
            capability=capability,
            api_key_scope=api_key_scope,
            aliases=aliases,
        )
        if not marker_hashes:
            return ()
        async with sqlite_writer_section():
            # Overlapping alias sets must lock rows in one global order on
            # PostgreSQL so concurrent reconnects cannot deadlock A->B/B->A.
            for marker_hash in sorted(marker_hashes):
                await self._session.execute(self._upsert_statement(marker_hash))
            await self._session.commit()
        return marker_hashes

    def _upsert_statement(self, marker_hash: str) -> Insert:
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_fn = pg_insert
        elif dialect == "sqlite":
            insert_fn = sqlite_insert
        elif is_mysql(dialect):
            insert_fn = mysql_insert
        else:
            raise RuntimeError(f"Capability lineage persistence unsupported for dialect={dialect!r}")
        statement = insert_fn(CapabilityLineageMarker).values(marker_hash=marker_hash)
        if is_mysql(dialect):
            return statement.on_duplicate_key_update(last_seen_at=func.now())
        return statement.on_conflict_do_update(
            index_elements=[CapabilityLineageMarker.marker_hash],
            set_={"last_seen_at": func.now()},
        )
