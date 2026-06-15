from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsPayload, fetch_rate_limit_reset_credits
from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy import ResolvedUpstreamRoute, resolve_upstream_route
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountRateLimitResetCreditRecord

logger = logging.getLogger(__name__)


class ResetCreditAccountsRepositoryPort(Protocol):
    async def expire_rate_limit_reset_credits(
        self,
        *,
        now: datetime | None = None,
        account_id: str | None = None,
    ) -> int: ...

    async def insert_rate_limit_reset_credits_if_missing(
        self,
        credits: list[AccountRateLimitResetCreditRecord],
    ) -> int: ...


@dataclass(slots=True)
class ResetCreditUpdater:
    repo: ResetCreditAccountsRepositoryPort
    fetcher: Callable[..., Awaitable[RateLimitResetCreditsPayload]] = fetch_rate_limit_reset_credits
    now: Callable[[], datetime] = utcnow
    decrypt_token: Callable[[bytes], str] | None = None
    route_resolver: Callable[[Account], Awaitable[ResolvedUpstreamRoute | None]] | None = None

    def __post_init__(self) -> None:
        encryptor = TokenEncryptor()
        if self.decrypt_token is None:
            self.decrypt_token = encryptor.decrypt
        if self.route_resolver is None:
            self.route_resolver = _resolve_upstream_route_for_account

    async def refresh_accounts(self, accounts: list[Account]) -> int:
        inserted = 0
        for account in accounts:
            if account.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
                continue
            try:
                inserted += await self.refresh_account(account)
            except Exception as exc:
                logger.warning(
                    "Reset credit refresh failed account_id=%s error=%s",
                    account.id,
                    exc,
                    exc_info=True,
                )
        return inserted

    async def refresh_account(self, account: Account) -> int:
        await self.repo.expire_rate_limit_reset_credits(now=self.now(), account_id=account.id)
        assert self.decrypt_token is not None
        assert self.route_resolver is not None
        payload = await self.fetcher(
            access_token=self.decrypt_token(account.access_token_encrypted),
            account_id=account.chatgpt_account_id,
            route=await self.route_resolver(account),
            allow_direct_egress=True,
        )
        credits = [
            AccountRateLimitResetCreditRecord(
                account_id=account.id,
                credit_id=credit.id,
                status=credit.status,
                granted_at=to_utc_naive(credit.granted_at),
                expires_at=to_utc_naive(credit.expires_at),
                redeemed_at=to_utc_naive(credit.redeemed_at) if credit.redeemed_at is not None else None,
            )
            for credit in payload.credits
        ]
        return await self.repo.insert_rate_limit_reset_credits_if_missing(credits)


async def _resolve_upstream_route_for_account(account: Account) -> ResolvedUpstreamRoute | None:
    async with get_background_session() as session:
        return await resolve_upstream_route(
            session,
            account_id=account.id,
            operation="reset_credit_refresh",
            scope="account",
        )
