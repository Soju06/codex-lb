from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
from app.modules.rate_limit_reset_credits import outcomes
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
_AUTO_REDEEM_WINDOW_SECONDS = 60 * 60
_DISCOVERY_WORKERS = 3
_DEADLINE_WORKERS = 4
SnapshotCallback = Callable[[Account, RateLimitResetCreditsSnapshot], None]


@dataclass(slots=True)
class _Deadline:
    account: Account
    snapshot: RateLimitResetCreditsSnapshot
    due_at: datetime
    verify_request_id: str | None = None


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
    enabled: bool = True
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _deadlines: dict[str, _Deadline] = field(default_factory=dict)
    _verifications: dict[str, _Deadline] = field(default_factory=dict)
    _deadline_changed: asyncio.Event = field(default_factory=asyncio.Event)
    _inflight_deadlines: set[str] = field(default_factory=set)

    async def start(self) -> None:
        if not self.enabled:
            await self._warn_if_auto_redeem_conflicts()
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def _warn_if_auto_redeem_conflicts(self) -> None:
        # The refresh loop is the only driver of automatic redemption, so a
        # disabled scheduler silently starves a persisted auto-redeem opt-in.
        try:
            async with get_background_session() as session:
                dashboard_settings = await SettingsRepository(session).get_or_create()
                auto_redeem_enabled = dashboard_settings.auto_redeem_reset_credits_before_expiry
        except Exception:
            logger.exception("Reset credits auto-redeem conflict check failed")
            return
        if auto_redeem_enabled:
            logger.warning(
                "rate_limit_reset_credits_refresh_enabled=false disables automatic reset-credit "
                "redemption, but dashboard setting auto_redeem_reset_credits_before_expiry is "
                "enabled; credits will expire without redemption until polling is re-enabled"
            )

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
        await self._restore_deadlines()
        async with asyncio.TaskGroup() as workers:
            deadline_worker = workers.create_task(self._run_deadlines())
            verification_worker = workers.create_task(self._deadline_worker(verification=True))
            try:
                # Jitter only discovery; persisted urgent attempts must not
                # lose another refresh interval when a replica restarts.
                if await self._wait_or_stop(self._startup_delay_seconds()):
                    return
                while not self._stop.is_set():
                    await self._refresh_once()
                    if await self._wait_or_stop(self._tick_delay_seconds()):
                        return
            finally:
                deadline_worker.cancel()
                verification_worker.cancel()

    def _schedule_snapshot(self, account: Account, snapshot: RateLimitResetCreditsSnapshot) -> None:
        credit = _select_auto_redeem_target_credit(snapshot)
        if credit is None or credit.expires_at is None:
            self._deadlines.pop(account.id, None)
            return
        expiry = outcomes.utc(credit.expires_at)
        if expiry <= datetime.now(UTC):
            self._deadlines.pop(account.id, None)
            return
        previous = self._deadlines.get(account.id)
        if previous is not None and _select_auto_redeem_target_credit(previous.snapshot) == credit:
            return
        self._deadlines[account.id] = _Deadline(
            account,
            snapshot,
            expiry - timedelta(seconds=_AUTO_REDEEM_WINDOW_SECONDS),
        )
        self._deadline_changed.set()

    async def _restore_deadlines(self) -> None:
        try:
            requests = await outcomes.unresolved_requests()
            async with get_background_session() as session:
                for row in requests:
                    account = await session.get(Account, row.account_id)
                    if account is None:
                        continue
                    # Publish only detached payloads: a later row can fail to
                    # load, causing rollback before the scan reaches its end.
                    detach_session_objects(session)
                    if row.outcome == "confirmed_reset":
                        if row.account_id not in self._verifications:
                            self._verifications[row.account_id] = _Deadline(
                                account,
                                RateLimitResetCreditsSnapshot(),
                                datetime.now(UTC),
                                row.redeem_request_id,
                            )
                            self._deadline_changed.set()
                        continue
                    credit = ResetCreditItem(id=row.credit_id, status="available", expires_at=row.credit_expires_at)
                    self._schedule_snapshot(
                        account, build_snapshot(ResetCreditsResponse(available_count=1, credits=[credit]))
                    )
                    deadline = self._deadlines.get(row.account_id)
                    if deadline is not None and row.next_retry_at is not None:
                        deadline.due_at = max(deadline.due_at, outcomes.utc(row.next_retry_at))
                detach_session_objects(session)
        except Exception:
            logger.exception("Reset-credit deadline recovery failed; discovery will retry")

    async def _run_deadlines(self) -> None:
        async with asyncio.TaskGroup() as workers:
            for _ in range(_DEADLINE_WORKERS):
                workers.create_task(self._deadline_worker())

    async def _deadline_worker(self, *, verification: bool = False) -> None:
        queue = self._verifications if verification else self._deadlines
        while not self._stop.is_set():
            self._deadline_changed.clear()
            candidates = [
                item
                for account_id, item in queue.items()
                if account_id not in self._inflight_deadlines and (item.verify_request_id is not None) == verification
            ]
            now = datetime.now(UTC)
            ready = [item for item in candidates if item.due_at <= now]
            # Backoff determines eligibility, but a due retry still keeps its
            # original expiry priority ahead of less urgent first attempts.
            deadline = (
                min(ready, key=lambda item: outcomes.utc(item.snapshot.nearest_expires_at or item.due_at))
                if ready
                else min(candidates, key=lambda item: item.due_at, default=None)
            )
            delay = max(0.0, (deadline.due_at - datetime.now(UTC)).total_seconds()) if deadline else 60.0
            if delay > 0:
                try:
                    await asyncio.wait_for(self._deadline_changed.wait(), timeout=min(delay, 60.0))
                except TimeoutError:
                    pass
                continue
            assert deadline is not None
            account_id = deadline.account.id
            if deadline.verify_request_id is not None:
                try:
                    await _refresh_usage_after_auto_redeem(deadline.account)
                    await outcomes.mark_usage_verified(account_id, deadline.verify_request_id)
                    await _refresh_account_reset_credits(
                        deadline.account,
                        encryptor=TokenEncryptor(),
                        store=get_rate_limit_reset_credits_store(),
                        fetch_fn=fetch_reset_credits,
                        resolve_route=_resolve_reset_credits_refresh_route,
                    )
                    self._verifications.pop(account_id, None)
                except Exception:
                    logger.warning("Reset-credit quota verification pending account_id=%s", account_id, exc_info=True)
                    deadline.due_at = datetime.now(UTC) + timedelta(seconds=60)
                continue
            if not _should_auto_redeem_snapshot(deadline.snapshot, window_seconds=_AUTO_REDEEM_WINDOW_SECONDS):
                self._deadlines.pop(account_id, None)
                continue
            completed = False
            self._inflight_deadlines.add(account_id)
            attempt_started = time.monotonic()
            try:
                completed = await _auto_redeem_reset_credit(
                    deadline.account,
                    snapshot=deadline.snapshot,
                    encryptor=TokenEncryptor(),
                    store=get_rate_limit_reset_credits_store(),
                    fetch_fn=fetch_reset_credits,
                    redeem_fn=None,
                    resolve_route=_resolve_reset_credits_consume_route,
                    on_confirmed=self._schedule_verification,
                )
            except Exception:
                logger.warning(
                    "Automatic reset-credit deadline attempt failed account_id=%s", account_id, exc_info=True
                )
            finally:
                self._inflight_deadlines.discard(account_id)
                self._deadline_changed.set()
                logger.info(
                    "Reset-credit deadline account_id=%s queue_delay_seconds=%.2f attempt_seconds=%.2f completed=%s",
                    account_id,
                    max(0.0, (now - deadline.due_at).total_seconds()),
                    time.monotonic() - attempt_started,
                    completed,
                )
            if self._deadlines.get(account_id) is deadline:
                if completed:
                    self._deadlines.pop(account_id, None)
                else:
                    deadline.due_at = outcomes.retry_at(datetime.now(UTC), deadline.snapshot.nearest_expires_at)
                    credit = _select_auto_redeem_target_credit(deadline.snapshot)
                    try:
                        receipt = await outcomes.find_credit_request(account_id, credit.id) if credit else None
                        if (
                            receipt is not None
                            and receipt.next_retry_at is not None
                            and outcomes.utc(receipt.next_retry_at) > datetime.now(UTC)
                        ):
                            deadline.due_at = outcomes.utc(receipt.next_retry_at)
                    except Exception:
                        logger.warning("Cannot read reset-credit retry time account_id=%s", account_id, exc_info=True)

    def _schedule_verification(self, account: Account, request_id: str) -> None:
        self._verifications[account.id] = _Deadline(
            account,
            RateLimitResetCreditsSnapshot(),
            datetime.now(UTC),
            request_id,
        )
        self._deadline_changed.set()

    async def _wait_or_stop(self, delay_seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay_seconds)
        except asyncio.TimeoutError:
            return False
        return True

    async def _refresh_once(self) -> None:
        async with self._lock:
            scan_started = time.monotonic()
            try:
                async with get_background_session() as session:
                    accounts_repo = AccountsRepository(session)
                    settings_repo = SettingsRepository(session)
                    accounts = await accounts_repo.list_accounts()
                    dashboard_settings = await settings_repo.get_or_create()
                    auto_redeem_before_expiry = dashboard_settings.auto_redeem_reset_credits_before_expiry
                    detach_session_objects(session)
                store = get_rate_limit_reset_credits_store()
                if auto_redeem_before_expiry:
                    for account in accounts:
                        snapshot = store.get(account.id)
                        if snapshot is not None:
                            self._schedule_snapshot(account, snapshot)
                visible = {account.id for account in accounts}
                for queue in (self._deadlines, self._verifications):
                    for account_id in queue.keys() - visible:
                        queue.pop(account_id, None)
                await self._restore_deadlines()
                await refresh_reset_credits_for_accounts(
                    accounts=accounts,
                    encryptor=TokenEncryptor(),
                    store=get_rate_limit_reset_credits_store(),
                    fetch_fn=fetch_reset_credits,
                    resolve_route=_resolve_reset_credits_refresh_route,
                    auto_redeem_resolve_route=_resolve_reset_credits_consume_route,
                    auto_redeem_before_expiry=auto_redeem_before_expiry,
                    auto_redeem_window_seconds=float(_AUTO_REDEEM_WINDOW_SECONDS),
                    on_snapshot=self._schedule_snapshot if auto_redeem_before_expiry else None,
                )
                logger.info(
                    "Reset-credit discovery finished accounts=%d elapsed_seconds=%.2f queued_deadlines=%d",
                    len(accounts),
                    time.monotonic() - scan_started,
                    len(self._deadlines),
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
    on_snapshot: SnapshotCallback | None = None,
) -> None:
    """Refresh the cached reset-credits snapshot for each eligible account.

    CRITICAL invariant: this function MUST NOT mutate any account's persisted
    status. On upstream error it logs and retains the prior cached snapshot
    (i.e. it simply skips overwriting the cache) so account-status derivation
    stays owned by usage refresh. One account failing must not abort the loop.
    """
    eligible = iter(
        {
            account.id: account
            for account in accounts
            if (
                account.delete_requested_at is None
                and account.status not in _RESET_CREDITS_SKIP_STATUSES
                and account.chatgpt_account_id
            )
        }.values()
    )

    async def worker() -> None:
        for account in eligible:
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
                on_snapshot=on_snapshot,
            )

    async with asyncio.TaskGroup() as workers:
        for _ in range(_DISCOVERY_WORKERS):
            workers.create_task(worker())


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
    on_snapshot: SnapshotCallback | None = None,
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
    if on_snapshot is not None:
        on_snapshot(account, snapshot)
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
    return 0 < seconds_until_expiry <= float(window_seconds or 0)


async def _auto_redeem_reset_credit(
    account: Account,
    *,
    snapshot: RateLimitResetCreditsSnapshot,
    encryptor: TokenEncryptor,
    store: RateLimitResetCreditsStore,
    fetch_fn: ResetCreditsFetchFn,
    redeem_fn: ResetCreditsRedeemFn | None,
    resolve_route: ResolveRouteFn | None,
    on_confirmed: Callable[[Account, str], None] | None = None,
) -> bool:
    from app.modules.rate_limit_reset_credits.api import _redeem_soonest_reset_credit

    redeem_request_id = _auto_redeem_request_id(account, snapshot)
    target_credit = _select_auto_redeem_target_credit(snapshot)
    if redeem_request_id is None or target_credit is None:
        return True
    effective_redeem_fn = redeem_fn or _redeem_soonest_reset_credit
    async with get_background_session() as lock_session:
        latest_account = await lock_session.get(Account, account.id)
        settings = await SettingsRepository(lock_session).get_or_create()
        if not settings.auto_redeem_reset_credits_before_expiry:
            return True
        if latest_account is None or latest_account.delete_requested_at is not None:
            return True
        if latest_account.status in _RESET_CREDITS_SKIP_STATUSES or not latest_account.chatgpt_account_id:
            return True
        result = await effective_redeem_fn(
            account=latest_account,
            store=store,
            encryptor=encryptor,
            lock_session=lock_session,
            fetch_fn=fetch_fn,
            resolve_route=resolve_route,
            refresh_usage=None,
            redeem_request_id=redeem_request_id,
            automatic=True,
            expected_credit_id=target_credit.id,
            expected_credit_expires_at=target_credit.expires_at,
        )
        if result is not None and result.response.outcome == "confirmed_reset" and on_confirmed is not None:
            # Background-session cleanup rolls back its read transaction and
            # expires attached rows. Queued work must outlive that cleanup.
            detach_session_objects(lock_session)
            on_confirmed(latest_account, result.redeem_request_id or redeem_request_id)
        return result is not None and result.response.outcome in {"confirmed_reset", "no_reset", "expired"}


def _auto_redeem_request_id(account: Account, snapshot: RateLimitResetCreditsSnapshot) -> str | None:
    expires_at = snapshot.nearest_expires_at
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    else:
        expires_at = expires_at.astimezone(UTC)
    credit = _select_auto_redeem_target_credit(snapshot)
    if credit is None:
        return None
    digest = hashlib.sha256(f"{account.id}:{credit.id}:{expires_at.isoformat()}".encode("utf-8")).hexdigest()[:32]
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
        enabled=settings.rate_limit_reset_credits_refresh_enabled,
    )
