from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiFirewallAllowlist


class FirewallRepositoryConflictError(ValueError):
    pass


class FirewallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_entries(self) -> list[ApiFirewallAllowlist]:
        result = await self._session.execute(
            select(ApiFirewallAllowlist).order_by(ApiFirewallAllowlist.created_at, ApiFirewallAllowlist.ip_address)
        )
        return list(result.scalars().all())

    async def list_ip_addresses(self) -> set[str]:
        result = await self._session.execute(select(ApiFirewallAllowlist.ip_address))
        return {row[0] for row in result.all()}

    async def exists(self, ip_address: str) -> bool:
        row = await self._session.get(ApiFirewallAllowlist, ip_address)
        return row is not None

    async def add(self, ip_address: str) -> ApiFirewallAllowlist:
        row = ApiFirewallAllowlist(ip_address=ip_address)
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise FirewallRepositoryConflictError("IP address already exists") from exc
        await self._session.refresh(row)
        return row

    async def delete(self, ip_address: str) -> bool:
        row = await self._session.get(ApiFirewallAllowlist, ip_address)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
