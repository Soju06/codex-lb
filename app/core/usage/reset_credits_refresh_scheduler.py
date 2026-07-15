from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.clients.rate_limit_reset_credits import (
    RateLimitResetCreditsSnapshot,
    ResetCreditFetchError,
    ResetCreditItem,
    ResetCreditsResponse,
    build_snapshot,
    fetch_reset_credits,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.db.models import Account, AccountStatus
from app.db.session import detach_session_objects, get_background_session
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.rate_limit_reset_credits.store import (
    RateLimitResetCreditsStore,
    get_rate_limit_reset_credits_store,
)
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater, _resolve_upstream_route_for_account

logger = logging.getLogger(__name__)

_RESET_CREDITS_SKIP_STATUSES = frozenset(
    {AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED}
)

ResetCreditsFetchFn = Callable[..., Awaitable[ResetCreditsResponse]]
ResetCreditsRedeemFn = Callable[..., Awaitable[Any]]
ResolveRouteFn = Callable[[Account], Awaitable[ResolvedUpstreamRoute | None]]


_TICK_JITTER_LOW = 0.9
_TICK_JITTER_HIGH = 1.1
_AUTO_REDEEM_WINDOW_SECONDS = 5 * 60


@dataclass(slots=True)
class RateLimitResetCreditsRefreshScheduler:
    """Per-replica reset-credits refresh loop with desynchronized ticks.

    Every replica refreshes its own process-local snapshot store (the store is
    not shared, so the loop MUST NOT be leader-gated). The randomized startup
    delay and per-tick jitter only spread replica ticks over the interval so
    N replicas do not hit upstream in lockstep; aggregate upstream fetch rate
    still scales with replica count and is controlled by
    ``rate_limit_reset_credits_refresh_interval_seconds``.
    """

    interval_seconds: int
    rng: random.Random = field(default_factory=random.Random)
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def _startup_delay_seconds(self) -> float:
        return self.rng.uniform(0.0, float(self.interval_seconds))

    def _tick_delay_seconds(self) -> float:
        return float(self.interval_seconds) * self.rng.uniform(_TICK_JITTER_LOW, _TICK_JITTER_HIGH)

    async def _run_loop(self) -> None:
        if await self._wait_or_stop(self._startup_delay_seconds()):
            return
        while not self._stop.is_set():
            await self._refresh_once()
            if await self._wait_or_stop(self._tick_delay_seconds()):
                return

    async def _wait_or_stop(self, delay_seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay_seconds)
        except asyncio.TimeoutError:
            return False
        return True

    async def _refresh_once(self) -> None:
        async with self._lock:
            try:
                async with get_background_session() as session:
                    accounts_repo = AccountsRepository(session)
                    settings_repo = SettingsRepository(session)
                    accounts = await accounts_repo.list_accounts()
                    dashboard_settings = await settings_repo.get_or_create()
                    auto_redeem_before_expiry = dashboard_settings.auto_redeem_reset_credits_before_expiry
                    detach_session_objects(session)
                await refresh_reset_credits_for_accounts(
                    accounts=accounts,
                    encryptor=TokenEncryptor(),
                    store=get_rate_limit_reset_credits_store(),
                    fetch_fn=fetch_reset_credits,
                    resolve_route=_resolve_reset_credits_refresh_route,
                    auto_redeem_resolve_route=_resolve_reset_credits_consume_route,
                    auto_redeem_before_expiry=auto_redeem_before_expiry,
                    auto_redeem_window_seconds=float(_AUTO_REDEEM_WINDOW_SECONDS),
                )
            except Exception:
                logger.exception("Reset credits refresh loop failed")


async def refresh_reset_credits_for_accounts(
    *,
    accounts: list[Account],
    encryptor: TokenEncryptor,
    store: RateLimitResetCreditsStore,
    fetch_fn: ResetCreditsFetchFn = fetch_reset_credits,
    redeem_fn: ResetCreditsRedeemFn | None = None,
    resolve_route: ResolveRouteFn | None = None,
    auto_redeem_resolve_route: ResolveRouteFn | None = None,
    auto_redeem_before_expiry: bool = False,
    auto_redeem_window_seconds: float | None = None,
) -> None:
    """Refresh the cached reset-credits snapshot for each eligible account.

    CRITICAL invariant: this function MUST NOT mutate any account's persisted
    status. On upstream error it logs and retains the prior cached snapshot
    (i.e. it simply skips overwriting the cache) so account-status derivation
    stays owned by usage refresh. One account failing must not abort the loop.
    """
    for account in accounts:
        if account.status in _RESET_CREDITS_SKIP_STATUSES:
            continue
        if not account.chatgpt_account_id:
            continue
        await _refresh_account_reset_credits(
            account,
            encryptor=encryptor,
            store=store,
            fetch_fn=fetch_fn,
            redeem_fn=redeem_fn,
            resolve_route=resolve_route,
            auto_redeem_resolve_route=auto_redeem_resolve_route,
            auto_redeem_before_expiry=auto_redeem_before_expiry,
            auto_redeem_window_seconds=auto_redeem_window_seconds,
        )


async def _resolve_reset_credits_refresh_route(account: Account) -> ResolvedUpstreamRoute | None:
    return await _resolve_upstream_route_for_account(account, operation="usage_refresh")


async def _resolve_reset_credits_consume_route(account: Account) -> ResolvedUpstreamRoute | None:
    return await _resolve_upstream_route_for_account(account, operation="rate_limit_reset_consume")


async def _refresh_account_reset_credits(
    account: Account,
    *,
    encryptor: TokenEncryptor,
    store: RateLimitResetCreditsStore,
    fetch_fn: ResetCreditsFetchFn,
    redeem_fn: ResetCreditsRedeemFn | None = None,
    resolve_route: ResolveRouteFn | None = None,
    auto_redeem_resolve_route: ResolveRouteFn | None = None,
    auto_redeem_before_expiry: bool = False,
    auto_redeem_window_seconds: float | None = None,
) -> None:
    snapshot_generation = store.generation(account.id)
    route: ResolvedUpstreamRoute | None = None
    if resolve_route is not None:
        try:
            route = await resolve_route(account)
        except UpstreamProxyRouteError as exc:
            logger.warning(
                "Reset credits refresh upstream proxy route unavailable account_id=%s reason=%s",
                account.id,
                exc.reason,
            )
            return
    try:
        access_token = encryptor.decrypt(account.access_token_encrypted)
        response = await fetch_fn(
            access_token,
            account.chatgpt_account_id,
            route=route,
            allow_direct_egress=route is None,
        )
    except ResetCreditFetchError as exc:
        logger.warning(
            "Reset credits refresh failed account_id=%s error=%s",
            account.id,
            exc,
        )
        return
    except Exception as exc:
        logger.warning(
            "Reset credits refresh failed account_id=%s error=%s",
            account.id,
            exc,
        )
        return

    snapshot = build_snapshot(response)
    stored = await store.set_if_generation(account.id, snapshot, snapshot_generation)
    if not stored:
        logger.info(
            "Skipped stale reset credits snapshot account_id=%s",
            account.id,
        )
        return
    if auto_redeem_before_expiry and _should_auto_redeem_snapshot(
        snapshot,
        window_seconds=auto_redeem_window_seconds,
    ):
        try:
            await _auto_redeem_reset_credit(
                account,
                snapshot=snapshot,
                encryptor=encryptor,
                store=store,
                fetch_fn=fetch_fn,
                redeem_fn=redeem_fn,
                resolve_route=auto_redeem_resolve_route or resolve_route,
            )
        except Exception:
            logger.warning(
                "Automatic reset credit redeem failed account_id=%s",
                account.id,
                exc_info=True,
            )


def _should_auto_redeem_snapshot(
    snapshot: RateLimitResetCreditsSnapshot,
    *,
    window_seconds: float | None,
) -> bool:
    if snapshot.available_count <= 0 or snapshot.nearest_expires_at is None:
        return False
    expires_at = snapshot.nearest_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    seconds_until_expiry = (expires_at - datetime.now(UTC)).total_seconds()
    return 0 <= seconds_until_expiry <= float(window_seconds or 0)


async def _auto_redeem_reset_credit(
    account: Account,
    *,
    snapshot: RateLimitResetCreditsSnapshot,
    encryptor: TokenEncryptor,
    store: RateLimitResetCreditsStore,
    fetch_fn: ResetCreditsFetchFn,
    redeem_fn: ResetCreditsRedeemFn | None,
    resolve_route: ResolveRouteFn | None,
) -> None:
    from app.modules.rate_limit_reset_credits.api import (
        ResetCreditRedeemRequestAlreadyPinned,
        _redeem_soonest_reset_credit,
    )
    from app.modules.rate_limit_reset_credits.redeem_coordination import get_pinned_redeem_credit_id

    redeem_request_id = _auto_redeem_request_id(account, snapshot)
    if redeem_request_id is None:
        return
    pinned_credit_id = await get_pinned_redeem_credit_id(account.id, redeem_request_id)
    if pinned_credit_id is not None:
        logger.info(
            "Skipping automatic reset credit redeem because an automatic redeem is already pinned "
            "account_id=%s credit_id=%s",
            account.id,
            pinned_credit_id,
        )
        return
    target_credit = _select_auto_redeem_target_credit(snapshot)
    if target_credit is None:
        return

    effective_redeem_fn = redeem_fn or _redeem_soonest_reset_credit
    async with get_background_session() as lock_session:
        latest_account = await lock_session.get(Account, account.id)
        if latest_account is None:
            logger.info(
                "Skipping automatic reset credit redeem because account no longer exists account_id=%s",
                account.id,
            )
            return
        if latest_account.status in _RESET_CREDITS_SKIP_STATUSES or not latest_account.chatgpt_account_id:
            logger.info(
                "Skipping automatic reset credit redeem because account is no longer eligible "
                "account_id=%s status=%s has_chatgpt_account_id=%s",
                latest_account.id,
                latest_account.status.value,
                bool(latest_account.chatgpt_account_id),
            )
            return
        try:
            await effective_redeem_fn(
                account=latest_account,
                store=store,
                encryptor=encryptor,
                lock_session=lock_session,
                fetch_fn=fetch_fn,
                resolve_route=resolve_route,
                refresh_usage=_refresh_usage_after_auto_redeem,
                redeem_request_id=redeem_request_id,
                skip_if_redeem_request_pinned=True,
                expected_credit_id=target_credit.id,
                expected_credit_expires_at=target_credit.expires_at,
            )
        except ResetCreditRedeemRequestAlreadyPinned as exc:
            logger.info(
                "Skipping automatic reset credit redeem because an automatic redeem was pinned "
                "inside the redeem lock account_id=%s credit_id=%s",
                exc.account_id,
                exc.credit_id,
            )


def _auto_redeem_request_id(account: Account, snapshot: RateLimitResetCreditsSnapshot) -> str | None:
    expires_at = snapshot.nearest_expires_at
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    else:
        expires_at = expires_at.astimezone(UTC)
    # Intentionally one automatic request per account and UTC expiry date. If
    # multiple credits expire on the same day, the automatic path favors not
    # burning a second coupon over trying to exhaust every expiring credit.
    digest = hashlib.sha256(f"{account.id}:{expires_at.date().isoformat()}".encode("utf-8")).hexdigest()[:32]
    return f"auto-reset-credit:{digest}"


def _select_auto_redeem_target_credit(snapshot: RateLimitResetCreditsSnapshot) -> ResetCreditItem | None:
    if snapshot.available_count <= 0:
        return None
    available = [
        credit for credit in snapshot.credits if credit.status == "available" and credit.expires_at is not None
    ]
    if not available:
        return None
    return min(available, key=lambda credit: credit.expires_at)


async def _refresh_usage_after_auto_redeem(account: Account) -> None:
    async with get_background_session() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        additional_usage_repo = AdditionalUsageRepository(session)
        current = await accounts_repo.get_by_id(account.id)
        if current is None:
            raise RuntimeError(f"Account {account.id} disappeared before automatic reset-credit usage refresh")
        refreshed = await UsageUpdater(
            usage_repo,
            accounts_repo,
            additional_usage_repo,
            auth_manager=AuthManager(accounts_repo),
        ).force_refresh(current, ignore_refresh_disabled=True)
        if not refreshed:
            raise RuntimeError(f"Forced usage refresh returned no update for account {account.id}")
        get_account_selection_cache().invalidate()


def build_rate_limit_reset_credits_scheduler() -> RateLimitResetCreditsRefreshScheduler:
    settings = get_settings()
    return RateLimitResetCreditsRefreshScheduler(
        interval_seconds=settings.rate_limit_reset_credits_refresh_interval_seconds,
    )
