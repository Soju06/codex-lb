from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Account, AccountStatus, OAuthLivePolicy, OAuthLivePolicyAccount

GLOBAL_POLICY_ID = 1


class OAuthLivePolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def existing_account_ids(self, account_ids: list[str]) -> frozenset[str]:
        if not account_ids:
            return frozenset()
        rows = await self._session.scalars(select(Account.id).where(Account.id.in_(account_ids)))
        return frozenset(rows.all())

    async def get_policy(self) -> OAuthLivePolicy | None:
        result = await self._session.execute(
            select(OAuthLivePolicy)
            .options(selectinload(OAuthLivePolicy.allowed_accounts))
            .where(OAuthLivePolicy.id == GLOBAL_POLICY_ID)
        )
        return result.scalar_one_or_none()

    async def replace_policy(
        self,
        *,
        is_active: bool,
        allowed_account_ids: list[str],
    ) -> OAuthLivePolicy:
        policy = await self._session.get(OAuthLivePolicy, GLOBAL_POLICY_ID)
        if policy is None:
            policy = OAuthLivePolicy(id=GLOBAL_POLICY_ID, is_active=is_active)
            self._session.add(policy)
            await self._session.flush()
        else:
            policy.is_active = is_active
            policy.updated_at = datetime.now(timezone.utc)

        await self._session.execute(
            delete(OAuthLivePolicyAccount).where(OAuthLivePolicyAccount.policy_id == GLOBAL_POLICY_ID)
        )
        self._session.add_all(
            OAuthLivePolicyAccount(
                policy_id=GLOBAL_POLICY_ID,
                allowed_account_id=allowed_account_id,
            )
            for allowed_account_id in allowed_account_ids
        )
        await self._session.commit()
        refreshed = await self.get_policy()
        assert refreshed is not None
        return refreshed

    async def get_active_allowed_account_ids(self) -> frozenset[str]:
        rows = await self._session.scalars(
            select(OAuthLivePolicyAccount.allowed_account_id)
            .join(
                OAuthLivePolicy,
                OAuthLivePolicy.id == OAuthLivePolicyAccount.policy_id,
            )
            .join(Account, Account.id == OAuthLivePolicyAccount.allowed_account_id)
            .where(
                OAuthLivePolicy.id == GLOBAL_POLICY_ID,
                OAuthLivePolicy.is_active.is_(True),
                Account.status == AccountStatus.ACTIVE,
            )
        )
        return frozenset(rows.all())

    async def rollback(self) -> None:
        await self._session.rollback()
