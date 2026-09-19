"""Queries and writes over ``dashboard_role_mappings``.

Priorities are server-owned: the rows of one ``(provider, provider_key)`` are
always the contiguous integers ``N..1`` with ``N`` the winner. Every write that
changes the order goes through :meth:`RoleMappingsRepository.renumber`, which
parks the affected rows on disjoint negative values before writing their final
ones, so ``UNIQUE(provider, provider_key, priority)`` never trips mid-update
(SQLite has no deferred constraints).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DashboardAuthProvider, DashboardRoleMapping

#: Rules are evaluated on every sign-in, so the list stays trivially bounded.
MAX_MAPPINGS_PER_PROVIDER = 100


class RoleMappingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- reads ---

    async def list_for_provider(self, provider: str, provider_key: str) -> Sequence[DashboardRoleMapping]:
        """The provider's rules, winner first."""

        stmt = (
            select(DashboardRoleMapping)
            .where(DashboardRoleMapping.provider == provider)
            .where(DashboardRoleMapping.provider_key == provider_key)
            .order_by(DashboardRoleMapping.priority.desc())
            # A renumber writes priorities behind the ORM's back; always read the row as it is.
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def list_mappings(
        self, *, provider: str | None = None, provider_key: str | None = None
    ) -> Sequence[DashboardRoleMapping]:
        stmt = select(DashboardRoleMapping)
        if provider is not None:
            stmt = stmt.where(DashboardRoleMapping.provider == provider)
        if provider_key is not None:
            stmt = stmt.where(DashboardRoleMapping.provider_key == provider_key)
        stmt = stmt.order_by(
            DashboardRoleMapping.provider.asc(),
            DashboardRoleMapping.provider_key.asc(),
            DashboardRoleMapping.priority.desc(),
        ).execution_options(populate_existing=True)
        return (await self._session.execute(stmt)).scalars().all()

    async def get_mapping(self, mapping_id: str) -> DashboardRoleMapping | None:
        return await self._session.get(DashboardRoleMapping, mapping_id, populate_existing=True)

    async def count_mappings(self) -> int:
        return int((await self._session.execute(select(func.count()).select_from(DashboardRoleMapping))).scalar_one())

    async def provider_exists(self, provider: str, provider_key: str) -> bool:
        stmt = (
            select(DashboardAuthProvider.id)
            .where(DashboardAuthProvider.kind == provider)
            .where(DashboardAuthProvider.provider_key == provider_key)
            .limit(1)
        )
        return (await self._session.execute(stmt)).first() is not None

    # --- writes (no commit; the service owns the transaction) ---

    def add(self, mapping: DashboardRoleMapping) -> None:
        self._session.add(mapping)

    async def delete_mapping(self, mapping_id: str) -> None:
        await self._session.execute(delete(DashboardRoleMapping).where(DashboardRoleMapping.id == mapping_id))

    async def renumber(self, ordered_ids: Sequence[str]) -> None:
        """Write ``len..1`` onto ``ordered_ids`` (winner first) without a mid-update collision."""

        for index, mapping_id in enumerate(ordered_ids):
            await self._set_priority(mapping_id, -(index + 1))
        total = len(ordered_ids)
        for index, mapping_id in enumerate(ordered_ids):
            await self._set_priority(mapping_id, total - index)

    async def _set_priority(self, mapping_id: str, priority: int) -> None:
        await self._session.execute(
            update(DashboardRoleMapping)
            .where(DashboardRoleMapping.id == mapping_id)
            .values(priority=priority)
            .execution_options(synchronize_session=False)
        )

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

    async def rollback(self) -> None:
        await self._session.rollback()
