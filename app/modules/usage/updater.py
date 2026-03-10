from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from app.core.auth.refresh import RefreshError
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.usage.models import UsagePayload
from app.core.utils.request_id import get_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.auth_manager import AccountsRepositoryPort, AuthManager

logger = logging.getLogger(__name__)


class UsageRepositoryPort(Protocol):
    async def add_entry(
        self,
        account_id: str,
        used_percent: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        recorded_at: datetime | None = None,
        window: str | None = None,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        credits_has: bool | None = None,
        credits_unlimited: bool | None = None,
        credits_balance: float | None = None,
    ) -> UsageHistory | None: ...


class AdditionalUsageRepositoryPort(Protocol):
    async def add_entry(
        self,
        account_id: str,
        limit_name: str,
        metered_feature: str,
        window: str,
        used_percent: float,
        reset_at: int | None = None,
        window_minutes: int | None = None,
    ) -> None: ...

    async def delete_for_account(self, account_id: str) -> None: ...

    async def delete_for_account_and_limit(self, account_id: str, limit_name: str) -> None: ...

    async def delete_for_account_limit_window(
        self,
        account_id: str,
        limit_name: str,
        window: str,
    ) -> None: ...

    async def list_limit_names(self, *, account_ids: list[str] | None = None) -> list[str]: ...

    async def latest_recorded_at_for_account(self, account_id: str) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class AccountRefreshResult:
    usage_written: bool
    fetch_succeeded: bool = True


# Module-level freshness cache for additional-only accounts (no main UsageHistory
# entry). Used as a fast path to avoid DB queries on every pass within the same
# process. Updated only after a successful refresh that wrote data.
_last_successful_refresh: dict[str, datetime] = {}


class UsageUpdater:
    def __init__(
        self,
        usage_repo: UsageRepositoryPort,
        accounts_repo: AccountsRepositoryPort | None = None,
        additional_usage_repo: AdditionalUsageRepositoryPort | None = None,
    ) -> None:
        self._usage_repo = usage_repo
        self._additional_usage_repo = additional_usage_repo
        self._encryptor = TokenEncryptor()
        self._auth_manager = AuthManager(accounts_repo) if accounts_repo else None

    async def refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: Mapping[str, UsageHistory],
    ) -> bool:
        """Refresh usage for all accounts. Returns True if usage rows were written."""
        settings = get_settings()
        if not settings.usage_refresh_enabled:
            return False

        refreshed = False
        now = utcnow()
        interval = settings.usage_refresh_interval_seconds
        for account in accounts:
            if account.status == AccountStatus.DEACTIVATED:
                continue
            latest = latest_usage.get(account.id)
            if latest and (now - latest.recorded_at).total_seconds() < interval:
                continue
            # Additional-only accounts have no main UsageHistory entry.
            # Check DB-backed freshness (works across workers/restarts)
            # with process-local cache as a fast path.
            # NOTE: When a successful fetch returns empty additional data
            # (all rows deleted), the DB has no timestamp to consult.
            # Cross-worker may re-fetch; process-local cache (line ~138)
            # prevents redundant calls within the same worker.
            if latest is None:
                last_ok = _last_successful_refresh.get(account.id)
                if last_ok and (now - last_ok).total_seconds() < interval:
                    continue
                if self._additional_usage_repo is not None:
                    additional_fresh_at = await self._additional_usage_repo.latest_recorded_at_for_account(
                        account.id,
                    )
                    if additional_fresh_at and (now - additional_fresh_at).total_seconds() < interval:
                        _last_successful_refresh[account.id] = additional_fresh_at
                        continue
            # NOTE: AsyncSession is not safe for concurrent use. Run sequentially
            # within the request-scoped session to avoid PK collisions and
            # flush-time warnings (SAWarning: Session.add during flush).
            try:
                result = await self._refresh_account(
                    account,
                    usage_account_id=account.chatgpt_account_id,
                )
                refreshed = refreshed or result.usage_written
                # Only cache when the upstream fetch actually succeeded.
                # Transient errors (401 retry failure, 5xx, etc.) must not
                # suppress retries within the interval.
                if result.fetch_succeeded:
                    _last_successful_refresh[account.id] = now
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
        return refreshed

    async def _refresh_account(
        self,
        account: Account,
        *,
        usage_account_id: str | None,
    ) -> AccountRefreshResult:
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        payload: UsagePayload | None = None
        try:
            payload = await fetch_usage(
                access_token=access_token,
                account_id=usage_account_id,
            )
        except UsageFetchError as exc:
            if _should_deactivate_for_usage_error(exc.status_code):
                await self._deactivate_for_client_error(account, exc)
                return AccountRefreshResult(usage_written=False, fetch_succeeded=False)
            if exc.status_code != 401 or not self._auth_manager:
                return AccountRefreshResult(usage_written=False, fetch_succeeded=False)
            try:
                account = await self._auth_manager.ensure_fresh(account, force=True)
            except RefreshError:
                return AccountRefreshResult(usage_written=False, fetch_succeeded=False)
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            try:
                payload = await fetch_usage(
                    access_token=access_token,
                    account_id=usage_account_id,
                )
            except UsageFetchError as retry_exc:
                if _should_deactivate_for_usage_error(retry_exc.status_code):
                    await self._deactivate_for_client_error(account, retry_exc)
                return AccountRefreshResult(usage_written=False, fetch_succeeded=False)

        if payload is None:
            return AccountRefreshResult(usage_written=False, fetch_succeeded=False)

        await self._sync_plan_type(account, payload)

        now_epoch = _now_epoch()
        if self._additional_usage_repo is not None:
            if payload.additional_rate_limits:
                current_entries: set[tuple[str, str]] = set()
                for additional in payload.additional_rate_limits:
                    if additional.rate_limit is None:
                        # Limit exists but upstream reports no window data; prune
                        # any previously stored rows so the dashboard doesn't show
                        # stale quota percentages.
                        await self._additional_usage_repo.delete_for_account_and_limit(
                            account.id,
                            additional.limit_name,
                        )
                        continue
                    add_primary = additional.rate_limit.primary_window
                    add_secondary = additional.rate_limit.secondary_window
                    if add_primary and add_primary.used_percent is not None:
                        current_entries.add((additional.limit_name, "primary"))
                        await self._additional_usage_repo.add_entry(
                            account_id=account.id,
                            limit_name=additional.limit_name,
                            metered_feature=additional.metered_feature,
                            window="primary",
                            used_percent=float(add_primary.used_percent),
                            reset_at=_reset_at(add_primary.reset_at, add_primary.reset_after_seconds, now_epoch),
                            window_minutes=_window_minutes(add_primary.limit_window_seconds),
                        )
                    if add_secondary and add_secondary.used_percent is not None:
                        current_entries.add((additional.limit_name, "secondary"))
                        await self._additional_usage_repo.add_entry(
                            account_id=account.id,
                            limit_name=additional.limit_name,
                            metered_feature=additional.metered_feature,
                            window="secondary",
                            used_percent=float(add_secondary.used_percent),
                            reset_at=_reset_at(add_secondary.reset_at, add_secondary.reset_after_seconds, now_epoch),
                            window_minutes=_window_minutes(add_secondary.limit_window_seconds),
                        )
                current_limit_names = {name for name, _ in current_entries}
                existing_names = await self._additional_usage_repo.list_limit_names(account_ids=[account.id])
                for stale_name in existing_names:
                    if stale_name not in current_limit_names:
                        await self._additional_usage_repo.delete_for_account_and_limit(account.id, stale_name)
                        continue
                    for window in ("primary", "secondary"):
                        if (stale_name, window) not in current_entries:
                            await self._additional_usage_repo.delete_for_account_limit_window(
                                account.id,
                                stale_name,
                                window,
                            )
            elif payload.additional_rate_limits is not None:
                await self._additional_usage_repo.delete_for_account(account.id)

        rate_limit = payload.rate_limit
        if rate_limit is None:
            additional_synced = self._additional_usage_repo is not None and payload.additional_rate_limits is not None
            return AccountRefreshResult(usage_written=additional_synced)
        # Treat both None and empty rate_limit (both windows absent) as
        # additional-only to avoid falling through to window processing.
        primary = rate_limit.primary_window
        secondary = rate_limit.secondary_window
        if primary is None and secondary is None:
            additional_synced = self._additional_usage_repo is not None and payload.additional_rate_limits is not None
            return AccountRefreshResult(usage_written=additional_synced)
        credits_has, credits_unlimited, credits_balance = _credits_snapshot(payload)
        usage_written = False

        if primary and primary.used_percent is not None:
            entry = await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(primary.used_percent),
                input_tokens=None,
                output_tokens=None,
                window="primary",
                reset_at=_reset_at(primary.reset_at, primary.reset_after_seconds, now_epoch),
                window_minutes=_window_minutes(primary.limit_window_seconds),
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
            )
            usage_written = usage_written or _usage_entry_written(entry)

        if secondary and secondary.used_percent is not None:
            entry = await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(secondary.used_percent),
                input_tokens=None,
                output_tokens=None,
                window="secondary",
                reset_at=_reset_at(secondary.reset_at, secondary.reset_after_seconds, now_epoch),
                window_minutes=_window_minutes(secondary.limit_window_seconds),
            )
            usage_written = usage_written or _usage_entry_written(entry)
        return AccountRefreshResult(usage_written=usage_written)

    async def _deactivate_for_client_error(self, account: Account, exc: UsageFetchError) -> None:
        if not self._auth_manager:
            return
        reason = f"Usage API error: HTTP {exc.status_code} - {exc.message}"
        logger.warning(
            "Deactivating account due to client error account_id=%s status=%s message=%s request_id=%s",
            account.id,
            exc.status_code,
            exc.message,
            get_request_id(),
        )
        await self._auth_manager._repo.update_status(account.id, AccountStatus.DEACTIVATED, reason)
        account.status = AccountStatus.DEACTIVATED
        account.deactivation_reason = reason

    async def _sync_plan_type(self, account: Account, payload: UsagePayload) -> None:
        next_plan_type = coerce_account_plan_type(payload.plan_type, account.plan_type or "free")
        if next_plan_type == account.plan_type:
            return

        account.plan_type = next_plan_type
        if not self._auth_manager:
            return

        await self._auth_manager._repo.update_tokens(
            account.id,
            access_token_encrypted=account.access_token_encrypted,
            refresh_token_encrypted=account.refresh_token_encrypted,
            id_token_encrypted=account.id_token_encrypted,
            last_refresh=account.last_refresh,
            plan_type=account.plan_type,
            email=account.email,
            chatgpt_account_id=account.chatgpt_account_id,
        )


def _credits_snapshot(payload: UsagePayload) -> tuple[bool | None, bool | None, float | None]:
    credits = payload.credits
    if credits is None:
        return None, None, None
    credits_has = credits.has_credits
    credits_unlimited = credits.unlimited
    balance_value = credits.balance
    return credits_has, credits_unlimited, _parse_credits_balance(balance_value)


def _usage_entry_written(entry: UsageHistory | None) -> bool:
    return entry is not None


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


def _now_epoch() -> int:
    return int(utcnow().replace(tzinfo=timezone.utc).timestamp())


def _reset_at(reset_at: int | None, reset_after_seconds: int | None, now_epoch: int) -> int | None:
    if reset_at is not None:
        return int(reset_at)
    if reset_after_seconds is None:
        return None
    return now_epoch + max(0, int(reset_after_seconds))


# The usage endpoint can return 403 for accounts that are still otherwise usable
# for proxy traffic, so treat it as a refresh failure instead of a permanent
# account-level deactivation signal.
_DEACTIVATING_USAGE_STATUS_CODES = {402, 404}


def _should_deactivate_for_usage_error(status_code: int) -> bool:
    return status_code in _DEACTIVATING_USAGE_STATUS_CODES
