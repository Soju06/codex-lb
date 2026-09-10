from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import sqlite_writer_section
from app.modules.accounts.probe_recovery import ProbeHold


class AccountProbeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, account_id: str, token: str) -> bool:
        now = utcnow()
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id, Account.delete_requested_at.is_(None))
                .where(Account.status.not_in((AccountStatus.PAUSED, AccountStatus.DEACTIVATED)))
                .where(or_(Account.probe_claim_expires_at.is_(None), Account.probe_claim_expires_at <= now))
                .values(probe_claim_token=token, probe_claim_expires_at=now + timedelta(seconds=60))
                .returning(Account.id)
            )
            claimed = result.scalar_one_or_none() is not None
            await self._session.commit()
            return claimed

    async def renew(self, account_id: str, token: str) -> bool:
        now = utcnow()
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id, Account.probe_claim_token == token)
                .where(Account.probe_claim_expires_at > now, Account.delete_requested_at.is_(None))
                .where(Account.status.not_in((AccountStatus.PAUSED, AccountStatus.DEACTIVATED)))
                .values(probe_claim_expires_at=now + timedelta(seconds=60))
                .returning(Account.id)
            )
            renewed = result.scalar_one_or_none() is not None
            await self._session.commit()
            return renewed

    async def release(self, account_id: str, token: str) -> None:
        async with sqlite_writer_section():
            await self._session.execute(
                update(Account)
                .where(Account.id == account_id, Account.probe_claim_token == token)
                .values(probe_claim_token=None, probe_claim_expires_at=None)
            )
            await self._session.commit()

    async def recover(self, account_id: str, token: str, hold: ProbeHold) -> bool:
        if not hold.matches(hold.model or "", hold.service_tier):
            return False
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id, Account.delete_requested_at.is_(None))
                .where(Account.probe_claim_token == token, Account.probe_claim_expires_at > utcnow())
                .where(Account.status == hold.status, Account.block_generation == hold.generation)
                .where(Account.reset_at == hold.reset_at, Account.blocked_at == hold.blocked_at)
                .where(Account.deactivation_reason == hold.reason)
                .where(Account.refresh_token_encrypted == hold.refresh_token)
                .where(Account.access_token_encrypted == hold.access_token)
                .where(Account.id_token_encrypted == hold.id_token)
                .where(
                    Account.chatgpt_account_id == hold.account_identity, Account.chatgpt_user_id == hold.user_identity
                )
                .where(Account.rejected_model == hold.model, Account.rejected_service_tier == hold.service_tier)
                .values(
                    status=AccountStatus.ACTIVE,
                    reset_at=None,
                    blocked_at=None,
                    deactivation_reason=None,
                    block_generation=Account.block_generation + 1,
                    rejected_model=None,
                    rejected_service_tier=None,
                )
                .returning(Account.id)
            )
            recovered = result.scalar_one_or_none() is not None
            await self._session.commit()
            return recovered
