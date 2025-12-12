from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountStatus


class AccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_accounts(self) -> list[Account]:
        result = await self._session.execute(select(Account).order_by(Account.email))
        return list(result.scalars().all())

    async def upsert(self, account: Account) -> Account:
        existing = await self._session.get(Account, account.id)
        if existing:
            existing.email = account.email
            existing.plan_type = account.plan_type
            existing.access_token_encrypted = account.access_token_encrypted
            existing.refresh_token_encrypted = account.refresh_token_encrypted
            existing.id_token_encrypted = account.id_token_encrypted
            existing.last_refresh = account.last_refresh
            existing.status = account.status
            existing.deactivation_reason = account.deactivation_reason
            await self._session.commit()
            await self._session.refresh(existing)
            return existing

        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
    ) -> bool:
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(status=status, deactivation_reason=deactivation_reason)
        )
        await self._session.commit()
        return bool(result.rowcount)

    async def delete(self, account_id: str) -> bool:
        result = await self._session.execute(delete(Account).where(Account.id == account_id))
        await self._session.commit()
        return bool(result.rowcount)

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
    ) -> bool:
        values = {
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "id_token_encrypted": id_token_encrypted,
            "last_refresh": last_refresh,
        }
        if plan_type is not None:
            values["plan_type"] = plan_type
        if email is not None:
            values["email"] = email
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(**values)
        )
        await self._session.commit()
        return bool(result.rowcount)
