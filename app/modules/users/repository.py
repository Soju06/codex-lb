from __future__ import annotations

from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DashboardUser, DashboardUserRole


class _Unset(Enum):
    UNSET = "UNSET"


_UNSET = _Unset.UNSET


class UsersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: str) -> DashboardUser | None:
        return await self._session.get(DashboardUser, user_id)

    async def get_by_username(self, username: str) -> DashboardUser | None:
        result = await self._session.execute(select(DashboardUser).where(DashboardUser.username == username))
        return result.scalar_one_or_none()

    async def list_users(
        self,
        *,
        search: str | None = None,
        role: str | None = None,
    ) -> list[DashboardUser]:
        stmt = select(DashboardUser).order_by(DashboardUser.created_at.asc(), DashboardUser.username.asc())
        if search:
            stmt = stmt.where(DashboardUser.username.ilike(f"%{search}%"))
        if role:
            stmt = stmt.where(DashboardUser.role == role)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_admins(self) -> int:
        result = await self._session.execute(
            select(func.count(DashboardUser.id))
            .where(DashboardUser.role == DashboardUserRole.ADMIN)
            .where(DashboardUser.is_active.is_(True))
        )
        return int(result.scalar_one())

    async def create(self, user: DashboardUser) -> DashboardUser:
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def update(
        self,
        user_id: str,
        *,
        username: str | _Unset = _UNSET,
        password_hash: str | _Unset = _UNSET,
        role: str | _Unset = _UNSET,
        is_active: bool | _Unset = _UNSET,
    ) -> DashboardUser | None:
        user = await self.get_by_id(user_id)
        if user is None:
            return None

        if username is not _UNSET:
            assert isinstance(username, str)
            user.username = username
        if password_hash is not _UNSET:
            assert isinstance(password_hash, str)
            user.password_hash = password_hash
        if role is not _UNSET:
            assert isinstance(role, str)
            user.role = role
        if is_active is not _UNSET:
            assert isinstance(is_active, bool)
            user.is_active = is_active

        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def delete(self, user_id: str) -> bool:
        user = await self.get_by_id(user_id)
        if user is None:
            return False
        await self._session.delete(user)
        await self._session.commit()
        return True
