from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentProviderAccount


class AgentProviderAccountConflictError(Exception):
    pass


class AgentProviderAccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_provider(self, provider_id: str) -> list[AgentProviderAccount]:
        result = await self._session.execute(
            select(AgentProviderAccount)
            .where(AgentProviderAccount.provider_id == provider_id)
            .order_by(AgentProviderAccount.display_name, AgentProviderAccount.id)
        )
        return list(result.scalars().all())

    async def get_for_provider(self, provider_id: str, account_id: str) -> AgentProviderAccount | None:
        result = await self._session.execute(
            select(AgentProviderAccount).where(
                AgentProviderAccount.provider_id == provider_id,
                AgentProviderAccount.id == account_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, row: AgentProviderAccount) -> AgentProviderAccount:
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AgentProviderAccountConflictError from exc
        await self._session.refresh(row)
        return row

    async def save(self, row: AgentProviderAccount) -> AgentProviderAccount:
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AgentProviderAccountConflictError from exc
        await self._session.refresh(row)
        return row
