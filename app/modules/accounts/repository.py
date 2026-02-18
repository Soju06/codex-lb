from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountStatus, DashboardSettings, RequestLog, StickySession, UsageHistory

_SETTINGS_ROW_ID = 1
_DUPLICATE_ACCOUNT_SUFFIX = "__copy"


class AccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def list_accounts(self) -> list[Account]:
        result = await self._session.execute(select(Account).order_by(Account.email))
        return list(result.scalars().all())

    async def exists_active_chatgpt_account_id(self, chatgpt_account_id: str) -> bool:
        result = await self._session.execute(
            select(Account.id)
            .where(Account.chatgpt_account_id == chatgpt_account_id)
            .where(Account.status.notin_((AccountStatus.DEACTIVATED, AccountStatus.PAUSED)))
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def upsert(self, account: Account, *, merge_by_email: bool | None = None) -> Account:
        if merge_by_email is None:
            merge_by_email = await self._merge_by_email_enabled()

        existing = await self._session.get(Account, account.id)
        if existing:
            if merge_by_email:
                _apply_account_updates(existing, account)
                await self._session.commit()
                await self._session.refresh(existing)
                return existing
            account.id = await self._next_available_account_id(account.id)

        if merge_by_email:
            result = await self._session.execute(select(Account).where(Account.email == account.email))
            existing_by_email = result.scalar_one_or_none()
            if existing_by_email:
                _apply_account_updates(existing_by_email, account)
                await self._session.commit()
                await self._session.refresh(existing_by_email)
                return existing_by_email

        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
    ) -> bool:
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(status=status, deactivation_reason=deactivation_reason, reset_at=reset_at)
            .returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def delete(self, account_id: str) -> bool:
        await self._session.execute(delete(UsageHistory).where(UsageHistory.account_id == account_id))
        await self._session.execute(delete(RequestLog).where(RequestLog.account_id == account_id))
        await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
        result = await self._session.execute(delete(Account).where(Account.id == account_id).returning(Account.id))
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
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
        if chatgpt_account_id is not None:
            values["chatgpt_account_id"] = chatgpt_account_id
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def _merge_by_email_enabled(self) -> bool:
        settings = await self._session.get(DashboardSettings, _SETTINGS_ROW_ID)
        if settings is None:
            return True
        return not settings.import_without_overwrite

    async def _next_available_account_id(self, base_id: str) -> str:
        candidate = base_id
        sequence = 2
        while await self._session.get(Account, candidate) is not None:
            candidate = f"{base_id}{_DUPLICATE_ACCOUNT_SUFFIX}{sequence}"
            sequence += 1
        return candidate


def _apply_account_updates(target: Account, source: Account) -> None:
    target.chatgpt_account_id = source.chatgpt_account_id
    target.email = source.email
    target.plan_type = source.plan_type
    target.access_token_encrypted = source.access_token_encrypted
    target.refresh_token_encrypted = source.refresh_token_encrypted
    target.id_token_encrypted = source.id_token_encrypted
    target.last_refresh = source.last_refresh
    target.status = source.status
    target.deactivation_reason = source.deactivation_reason
