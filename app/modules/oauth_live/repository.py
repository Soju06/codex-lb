from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Account, AccountStatus, OAuthLivePolicy, OAuthLivePolicyAccount


class OAuthLivePolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def account_exists(self, account_id: str) -> bool:
        return await self._session.scalar(select(Account.id).where(Account.id == account_id).limit(1)) is not None

    async def existing_account_ids(self, account_ids: list[str]) -> frozenset[str]:
        if not account_ids:
            return frozenset()
        rows = await self._session.scalars(select(Account.id).where(Account.id.in_(account_ids)))
        return frozenset(rows.all())

    async def get_policy(self, caller_account_id: str) -> OAuthLivePolicy | None:
        result = await self._session.execute(
            select(OAuthLivePolicy)
            .options(selectinload(OAuthLivePolicy.allowed_accounts))
            .where(OAuthLivePolicy.caller_account_id == caller_account_id)
        )
        return result.scalar_one_or_none()

    async def replace_policy(
        self,
        caller_account_id: str,
        *,
        is_active: bool,
        allowed_account_ids: list[str],
    ) -> OAuthLivePolicy:
        policy = await self._session.get(OAuthLivePolicy, caller_account_id)
        if policy is None:
            policy = OAuthLivePolicy(caller_account_id=caller_account_id, is_active=is_active)
            self._session.add(policy)
            await self._session.flush()
        else:
            policy.is_active = is_active
            policy.updated_at = datetime.now(timezone.utc)

        await self._session.execute(
            delete(OAuthLivePolicyAccount).where(OAuthLivePolicyAccount.caller_account_id == caller_account_id)
        )
        self._session.add_all(
            OAuthLivePolicyAccount(
                caller_account_id=caller_account_id,
                allowed_account_id=allowed_account_id,
            )
            for allowed_account_id in allowed_account_ids
        )
        await self._session.commit()
        refreshed = await self.get_policy(caller_account_id)
        assert refreshed is not None
        return refreshed

    async def get_active_allowed_account_ids(self, caller_account_id: str) -> frozenset[str]:
        rows = await self._session.scalars(
            select(OAuthLivePolicyAccount.allowed_account_id)
            .join(
                OAuthLivePolicy,
                OAuthLivePolicy.caller_account_id == OAuthLivePolicyAccount.caller_account_id,
            )
            .join(Account, Account.id == OAuthLivePolicyAccount.allowed_account_id)
            .where(
                OAuthLivePolicy.caller_account_id == caller_account_id,
                OAuthLivePolicy.is_active.is_(True),
                Account.status == AccountStatus.ACTIVE,
            )
        )
        return frozenset(rows.all())

    async def rollback(self) -> None:
        await self._session.rollback()
