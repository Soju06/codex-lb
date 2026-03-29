from __future__ import annotations

import asyncio
import inspect
import logging
import math
from collections.abc import Awaitable, Callable, Collection
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
from app.modules.usage.additional_quota_keys import canonicalize_additional_quota_key
from app.modules.usage.repository import AdditionalUsageRepository

logger = logging.getLogger(__name__)


class UsageRepositoryPort(Protocol):
    async def latest_entry_for_account(
        self,
        account_id: str,
        *,
        window: str | None = None,
    ) -> UsageHistory | None: ...

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
        recorded_at: datetime | None = None,
        quota_key: str | None = None,
    ) -> None: ...

    async def delete_for_account(self, account_id: str) -> None: ...

    async def delete_for_account_and_quota_key(self, account_id: str, quota_key: str) -> None: ...

    async def delete_for_account_and_limit(self, account_id: str, limit_name: str) -> None: ...

    async def delete_for_account_quota_key_window(
        self,
        account_id: str,
        quota_key: str,
        window: str,
    ) -> None: ...

    async def delete_for_account_limit_window(
        self,
        account_id: str,
        limit_name: str,
        window: str,
    ) -> None: ...

    async def list_quota_keys(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]: ...

    async def list_limit_names(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]: ...

    async def latest_recorded_at_for_account(self, account_id: str) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class AccountRefreshResult:
    usage_written: bool
    fetch_succeeded: bool = True


@dataclass(frozen=True, slots=True)
class _MergedAdditionalWindow:
    limit_name: str
    metered_feature: str
    used_percent: float
    reset_at: int | None
    window_minutes: int | None


# Module-level freshness cache for additional-only accounts (no main UsageHistory
# entry). Used as a fast path to avoid DB queries on every pass within the same
# process. Updated only after a successful refresh that wrote data.
_last_successful_refresh: dict[str, datetime] = {}


class _UsageRefreshSingleflight:
    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[AccountRefreshResult]] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        account_id: str,
        factory: Callable[[], Awaitable[AccountRefreshResult]],
    ) -> AccountRefreshResult:
        async with self._lock:
            task = self._inflight.get(account_id)
            if task is None or task.done():
                task = asyncio.create_task(self._run_factory(factory))
                self._inflight[account_id] = task
                task.add_done_callback(
                    lambda done, *, key=account_id: self._clear_if_current(key, done),
                )
        return await asyncio.shield(task)

    async def _run_factory(
        self,
        factory: Callable[[], Awaitable[AccountRefreshResult]],
    ) -> AccountRefreshResult:
        return await factory()

    def _clear_if_current(self, account_id: str, task: asyncio.Task[AccountRefreshResult]) -> None:
        current = self._inflight.get(account_id)
        if current is task:
            self._inflight.pop(account_id, None)


_USAGE_REFRESH_SINGLEFLIGHT = _UsageRefreshSingleflight()


class UsageUpdater:
    def __init__(
        self,
        usage_repo: UsageRepositoryPort,
        accounts_repo: AccountsRepositoryPort | None = None,
        additional_usage_repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository | None = None,
    ) -> None:
        self._usage_repo = usage_repo
        self._accounts_repo = accounts_repo
        self._additional_usage_repo = additional_usage_repo
        self._accounts_repo = accounts_repo
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
            if _latest_usage_is_fresh(latest, now=now, interval_seconds=interval):
                # Still force a refresh when the account is blocked with an
                # expired reset — the fresh primary entry does not capture
                # that the upstream window has turned over.
                if not _account_needs_post_reset_refresh(account, now):
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
                result = await _USAGE_REFRESH_SINGLEFLIGHT.run(
                    account.id,
                    lambda account=account: self._refresh_account_if_stale(
                        account,
                        usage_account_id=account.chatgpt_account_id,
                        interval_seconds=interval,
                    ),
                )
                await self._sync_account_from_repo(account)
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

    async def _refresh_account_if_stale(
        self,
        account: Account,
        *,
        usage_account_id: str | None,
        interval_seconds: int,
    ) -> AccountRefreshResult:
        latest = await self._usage_repo.latest_entry_for_account(account.id, window="primary")
        if _latest_usage_is_fresh(latest, now=utcnow(), interval_seconds=interval_seconds):
            return AccountRefreshResult(usage_written=False)
        return await self._refresh_account(
            account,
            usage_account_id=usage_account_id,
        )

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
                merged_limits = _merge_additional_rate_limits(
                    payload.additional_rate_limits,
                    account_id=account.id,
                    now_epoch=now_epoch,
                )
                current_entries: set[tuple[str, str]] = set()
                for quota_key, windows in merged_limits.items():
                    for window, merged_window in windows.items():
                        current_entries.add((quota_key, window))
                        await _add_additional_usage_entry(
                            self._additional_usage_repo,
                            account_id=account.id,
                            limit_name=merged_window.limit_name,
                            metered_feature=merged_window.metered_feature,
                            quota_key=quota_key,
                            window=window,
                            used_percent=merged_window.used_percent,
                            reset_at=merged_window.reset_at,
                            window_minutes=merged_window.window_minutes,
                        )
                current_quota_keys = {name for name, _ in current_entries}
                existing_quota_keys = await _list_additional_usage_quota_keys(
                    self._additional_usage_repo,
                    account_ids=[account.id],
                )
                for stale_key in existing_quota_keys:
                    if stale_key not in current_quota_keys:
                        await _delete_additional_usage_quota_key(
                            self._additional_usage_repo,
                            account.id,
                            stale_key,
                        )
                        continue
                    for window in ("primary", "secondary"):
                        if (stale_key, window) not in current_entries:
                            await _delete_additional_usage_quota_key_window(
                                self._additional_usage_repo,
                                account.id,
                                stale_key,
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
        # This is a special case that if the account type is free (or probably go)
        # The 7d stat is in primary window instead of secondary window
        # (that is widely defined as 7d in the ui)
        # This will cause the account usage trend is "primary" instead of "secondary"
        if primary and primary.limit_window_seconds == 604800:
            secondary = rate_limit.primary_window
            primary = None
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

    async def _sync_account_from_repo(self, account: Account) -> None:
        if not self._accounts_repo:
            return
        stored = await self._accounts_repo.get_by_id(account.id)
        if stored is None:
            return
        account.chatgpt_account_id = stored.chatgpt_account_id
        account.email = stored.email
        account.plan_type = stored.plan_type
        account.access_token_encrypted = stored.access_token_encrypted
        account.refresh_token_encrypted = stored.refresh_token_encrypted
        account.id_token_encrypted = stored.id_token_encrypted
        account.last_refresh = stored.last_refresh
        account.status = stored.status
        account.deactivation_reason = stored.deactivation_reason
        account.reset_at = stored.reset_at


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


def _prefer_merged_additional_window(
    existing: _MergedAdditionalWindow,
    candidate: _MergedAdditionalWindow,
    *,
    quota_key: str,
    window: str,
) -> _MergedAdditionalWindow:
    if candidate.used_percent > existing.used_percent:
        logger.warning(
            "Additional usage refresh saw conflicting aliases for the same canonical quota window; "
            "keeping the higher usage sample account_quota=%s window=%s existing_limit=%s candidate_limit=%s "
            "request_id=%s",
            quota_key,
            window,
            existing.limit_name,
            candidate.limit_name,
            get_request_id(),
        )
        return candidate
    if candidate.used_percent < existing.used_percent:
        logger.warning(
            "Additional usage refresh saw conflicting aliases for the same canonical quota window; "
            "keeping the higher usage sample account_quota=%s window=%s existing_limit=%s candidate_limit=%s "
            "request_id=%s",
            quota_key,
            window,
            existing.limit_name,
            candidate.limit_name,
            get_request_id(),
        )
        return existing
    preferred = sorted(
        (existing, candidate),
        key=lambda entry: (entry.limit_name, entry.metered_feature),
    )[0]
    if preferred != existing or existing != candidate:
        logger.info(
            "Additional usage refresh coalesced duplicate aliases for canonical quota window "
            "account_quota=%s window=%s chosen_limit=%s request_id=%s",
            quota_key,
            window,
            preferred.limit_name,
            get_request_id(),
        )
    return preferred


def _merge_additional_rate_limits(
    additional_rate_limits: Collection[object],
    *,
    account_id: str,
    now_epoch: int,
) -> dict[str, dict[str, _MergedAdditionalWindow]]:
    merged: dict[str, dict[str, _MergedAdditionalWindow]] = {}
    for additional in additional_rate_limits:
        limit_name = getattr(additional, "limit_name", None)
        metered_feature = getattr(additional, "metered_feature", None)
        quota_key = canonicalize_additional_quota_key(
            limit_name=limit_name,
            metered_feature=metered_feature,
        )
        if quota_key is None:
            logger.warning(
                "Skipping additional usage item without resolvable quota key "
                "account_id=%s limit_name=%s metered_feature=%s request_id=%s",
                account_id,
                limit_name,
                metered_feature,
                get_request_id(),
            )
            continue
        rate_limit = getattr(additional, "rate_limit", None)
        if rate_limit is None:
            continue
        for window_name, usage_window in (
            ("primary", getattr(rate_limit, "primary_window", None)),
            ("secondary", getattr(rate_limit, "secondary_window", None)),
        ):
            if usage_window is None or usage_window.used_percent is None:
                continue
            candidate = _MergedAdditionalWindow(
                limit_name=str(limit_name),
                metered_feature=str(metered_feature),
                used_percent=float(usage_window.used_percent),
                reset_at=_reset_at(usage_window.reset_at, usage_window.reset_after_seconds, now_epoch),
                window_minutes=_window_minutes(usage_window.limit_window_seconds),
            )
            windows = merged.setdefault(quota_key, {})
            existing = windows.get(window_name)
            windows[window_name] = (
                candidate
                if existing is None
                else _prefer_merged_additional_window(
                    existing,
                    candidate,
                    quota_key=quota_key,
                    window=window_name,
                )
            )
    return merged


async def _add_additional_usage_entry(
    repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository,
    *,
    account_id: str,
    limit_name: str,
    metered_feature: str,
    quota_key: str,
    window: str,
    used_percent: float,
    reset_at: int | None,
    window_minutes: int | None,
) -> None:
    add_entry = repo.add_entry
    if "quota_key" in inspect.signature(add_entry).parameters:
        await add_entry(
            account_id=account_id,
            limit_name=limit_name,
            metered_feature=metered_feature,
            quota_key=quota_key,
            window=window,
            used_percent=used_percent,
            reset_at=reset_at,
            window_minutes=window_minutes,
        )
        return

    await add_entry(
        account_id=account_id,
        limit_name=limit_name,
        metered_feature=metered_feature,
        window=window,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=window_minutes,
    )


async def _list_additional_usage_quota_keys(
    repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository,
    *,
    account_ids: Collection[str] | None = None,
) -> list[str]:
    list_quota_keys = getattr(repo, "list_quota_keys", None)
    if callable(list_quota_keys):
        return await list_quota_keys(account_ids=account_ids)
    return await repo.list_limit_names(account_ids=account_ids)


async def _delete_additional_usage_quota_key(
    repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository,
    account_id: str,
    quota_key: str,
) -> None:
    delete_by_quota_key = getattr(repo, "delete_for_account_and_quota_key", None)
    if callable(delete_by_quota_key):
        await delete_by_quota_key(account_id, quota_key)
        return
    await repo.delete_for_account_and_limit(account_id, quota_key)


async def _delete_additional_usage_quota_key_window(
    repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository,
    account_id: str,
    quota_key: str,
    window: str,
) -> None:
    delete_by_quota_key_window = getattr(repo, "delete_for_account_quota_key_window", None)
    if callable(delete_by_quota_key_window):
        await delete_by_quota_key_window(account_id, quota_key, window)
        return
    await repo.delete_for_account_limit_window(account_id, quota_key, window)


def _latest_usage_is_fresh(
    latest: UsageHistory | None,
    *,
    now: datetime,
    interval_seconds: int,
) -> bool:
    if latest is None:
        return False
    if (now - latest.recorded_at).total_seconds() >= interval_seconds:
        return False
    # If the usage window has reset since this entry was recorded, the
    # recorded percentage is stale — force a re-fetch so the scheduler
    # picks up the new (lower) usage from the upstream API promptly.
    if latest.reset_at is not None:
        now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
        if now_epoch >= latest.reset_at:
            return False
    return True


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


def _account_needs_post_reset_refresh(account: Account, now: datetime) -> bool:
    """Return True when the account is blocked with an expired reset time.

    This catches the case where only the *secondary* window has reset (the
    primary entry may still look fresh) but the account is still persisted as
    RATE_LIMITED or QUOTA_EXCEEDED from the previous cycle.
    """
    if account.status not in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED):
        return False
    if account.reset_at is None:
        return False
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    return now_epoch >= account.reset_at


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
