from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.db.models import DashboardRoleRecord


class DashboardRolesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_roles(self) -> Sequence[DashboardRoleRecord]:
        stmt = (
            select(DashboardRoleRecord)
            .options(selectinload(DashboardRoleRecord.grants))
            .order_by(DashboardRoleRecord.kind.desc(), DashboardRoleRecord.slug.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get_role(self, role_id: str) -> DashboardRoleRecord | None:
        stmt = (
            select(DashboardRoleRecord)
            .options(selectinload(DashboardRoleRecord.grants))
            .where(DashboardRoleRecord.id == role_id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_role_by_slug(self, slug: str) -> DashboardRoleRecord | None:
        stmt = (
            select(DashboardRoleRecord)
            .options(selectinload(DashboardRoleRecord.grants))
            .where(DashboardRoleRecord.slug == slug)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_preset_role(self, slug: PresetRoleSlug) -> DashboardRoleRecord | None:
        return await self.get_role(PRESET_ROLE_IDS[slug])
