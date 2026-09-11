from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DashboardAuthProvider


class AuthProvidersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_providers(self) -> Sequence[DashboardAuthProvider]:
        stmt = select(DashboardAuthProvider).order_by(
            DashboardAuthProvider.created_at.asc(), DashboardAuthProvider.id.asc()
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get_provider(self, provider_id: str) -> DashboardAuthProvider | None:
        return await self._session.get(DashboardAuthProvider, provider_id)

    async def commit(self, provider: DashboardAuthProvider) -> DashboardAuthProvider:
        """Commit and reload the row (``expire_on_commit`` would otherwise lazy-load it outside the loop)."""

        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(provider)
        return provider
