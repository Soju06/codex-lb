from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StickySession


class StickySessionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account_id(self, key: str) -> str | None:
        if not key:
            return None
        result = await self._session.execute(select(StickySession.account_id).where(StickySession.key == key))
        return result.scalar_one_or_none()

    async def upsert(self, key: str, account_id: str) -> StickySession:
        existing = await self._session.get(StickySession, key)
        if existing:
            existing.account_id = account_id
            await self._session.commit()
            await self._session.refresh(existing)
            return existing
        row = StickySession(key=key, account_id=account_id)
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, key: str) -> bool:
        if not key:
            return False
        result = await self._session.execute(delete(StickySession).where(StickySession.key == key))
        await self._session.commit()
        return bool(result.rowcount)

