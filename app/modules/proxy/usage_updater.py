from __future__ import annotations

import logging
import math
from typing import Mapping

from app.core.crypto import TokenEncryptor
from app.core.auth.refresh import RefreshError
from app.core.config.settings import get_settings
from app.core.utils.request_id import get_request_id
from app.core.utils.time import utcnow
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.db.models import Account, AccountStatus, UsageHistory
from app.core.usage.models import UsagePayload
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.auth_manager import AuthManager
from app.modules.usage.repository import UsageRepository

logger = logging.getLogger(__name__)


class UsageUpdater:
    def __init__(
        self,
        usage_repo: UsageRepository,
        accounts_repo: AccountsRepository | None = None,
    ) -> None:
        self._usage_repo = usage_repo
        self._encryptor = TokenEncryptor()
        self._auth_manager = AuthManager(accounts_repo) if accounts_repo else None

    async def refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: Mapping[str, UsageHistory],
    ) -> None:
        settings = get_settings()
        if not settings.usage_refresh_enabled:
            return

        now = utcnow()
        interval = settings.usage_refresh_interval_seconds
        for account in accounts:
            if account.status == AccountStatus.DEACTIVATED:
                continue
            latest = latest_usage.get(account.id)
            if latest and (now - latest.recorded_at).total_seconds() < interval:
                continue
            # NOTE: AsyncSession is not safe for concurrent use. Run sequentially
            # within the request-scoped session to avoid PK collisions and
            # flush-time warnings (SAWarning: Session.add during flush).
            try:
                await self._refresh_account(account)
            except Exception as exc:
                logger.warning(
                    "Usage refresh failed account_id=%s request_id=%s error=%s",
                    account.id,
                    get_request_id(),
                    exc,
                    exc_info=True,
                )
                # swallow per-account failures so the whole refresh loop keeps going
                continue

    async def _refresh_account(self, account: Account) -> None:
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        try:
            payload = await fetch_usage(
                access_token=access_token,
                account_id=account.id,
            )
        except UsageFetchError as exc:
            if exc.status_code != 401 or not self._auth_manager:
                return
            try:
                account = await self._auth_manager.ensure_fresh(account, force=True)
            except RefreshError:
                return
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            try:
                payload = await fetch_usage(
                    access_token=access_token,
                    account_id=account.id,
                )
            except UsageFetchError:
                return

        rate_limit = payload.rate_limit
        primary = rate_limit.primary_window if rate_limit else None
        credits_has, credits_unlimited, credits_balance = _credits_snapshot(payload)
        primary_window_minutes = _window_minutes(primary.limit_window_seconds) if primary else None
        secondary = rate_limit.secondary_window if rate_limit else None
        secondary_window_minutes = _window_minutes(secondary.limit_window_seconds) if secondary else None

        if primary and primary.used_percent is not None:
            await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=primary.used_percent,
                input_tokens=None,
                output_tokens=None,
                window="primary",
                reset_at=primary.reset_at,
                window_minutes=primary_window_minutes,
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
            )

        if secondary and secondary.used_percent is not None:
            await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=secondary.used_percent,
                input_tokens=None,
                output_tokens=None,
                window="secondary",
                reset_at=secondary.reset_at,
                window_minutes=secondary_window_minutes,
            )


def _credits_snapshot(payload: UsagePayload) -> tuple[bool | None, bool | None, float | None]:
    credits = payload.credits
    if credits is None:
        return None, None, None
    credits_has = credits.has_credits
    credits_unlimited = credits.unlimited
    balance_value = credits.balance
    return credits_has, credits_unlimited, _parse_credits_balance(balance_value)


def _parse_credits_balance(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _window_minutes(limit_seconds: int | None) -> int | None:
    if not limit_seconds or limit_seconds <= 0:
        return None
    return max(1, math.ceil(limit_seconds / 60))
