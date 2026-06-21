from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.core.auth.refresh import RefreshError
from app.core.clients.rate_limit_reset_credits import (
    ResetCreditFetchError,
    ResetCreditsResponse,
    build_snapshot,
    fetch_reset_credits,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.rate_limit_reset_credits.store import (
    RateLimitResetCreditsStore,
    get_rate_limit_reset_credits_store,
)
from app.modules.usage.updater import _resolve_upstream_route_for_account

logger = logging.getLogger(__name__)

_RESET_CREDITS_SKIP_STATUSES = frozenset(
    {AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED}
)

ResetCreditsFetchFn = Callable[..., Awaitable[ResetCreditsResponse]]
ResolveRouteFn = Callable[[Account], Awaitable[ResolvedUpstreamRoute | None]]


@dataclass(slots=True)
class RateLimitResetCreditsRefreshScheduler:
    interval_seconds: int
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

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            await self._refresh_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _refresh_once(self) -> None:
        async with self._lock:
            try:
                async with get_background_session() as session:
                    accounts_repo = AccountsRepository(session)
                    accounts = await accounts_repo.list_accounts()
                    auth_manager = AuthManager(
                        accounts_repo,
                        refresh_repo_factory=_reset_credits_accounts_repo_factory,
                    )
                    await refresh_reset_credits_for_accounts(
                        accounts=accounts,
                        encryptor=TokenEncryptor(),
                        store=get_rate_limit_reset_credits_store(),
                        fetch_fn=fetch_reset_credits,
                        resolve_route=_resolve_reset_credits_refresh_route,
                        auth_manager=auth_manager,
                    )
            except Exception:
                logger.exception("Reset credits refresh loop failed")


async def refresh_reset_credits_for_accounts(
    *,
    accounts: list[Account],
    encryptor: TokenEncryptor,
    store: RateLimitResetCreditsStore,
    fetch_fn: ResetCreditsFetchFn = fetch_reset_credits,
    resolve_route: ResolveRouteFn | None = None,
    auth_manager: AuthManager | None = None,
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
            resolve_route=resolve_route,
            auth_manager=auth_manager,
        )


async def _resolve_reset_credits_refresh_route(account: Account) -> ResolvedUpstreamRoute | None:
    return await _resolve_upstream_route_for_account(account, operation="usage_refresh")


async def _refresh_account_reset_credits(
    account: Account,
    *,
    encryptor: TokenEncryptor,
    store: RateLimitResetCreditsStore,
    fetch_fn: ResetCreditsFetchFn,
    resolve_route: ResolveRouteFn | None = None,
    auth_manager: AuthManager | None = None,
) -> None:
    snapshot_generation = store.generation(account.id)
    refresh_account = account
    response: ResetCreditsResponse | None = None

    for attempt in range(2):
        route: ResolvedUpstreamRoute | None = None
        if resolve_route is not None:
            try:
                route = await resolve_route(refresh_account)
            except UpstreamProxyRouteError as exc:
                logger.warning(
                    "Reset credits refresh upstream proxy route unavailable account_id=%s reason=%s",
                    refresh_account.id,
                    exc.reason,
                )
                return
        try:
            access_token = encryptor.decrypt(refresh_account.access_token_encrypted)
            response = await fetch_fn(
                access_token,
                refresh_account.chatgpt_account_id,
                route=route,
                allow_direct_egress=route is None,
            )
            break
        except ResetCreditFetchError as exc:
            if exc.status_code != 401 or auth_manager is None or attempt > 0:
                logger.warning(
                    "Reset credits refresh failed account_id=%s error=%s",
                    refresh_account.id,
                    exc,
                )
                return
            try:
                refresh_account = await auth_manager.ensure_fresh(refresh_account, force=True)
            except RefreshError as refresh_exc:
                logger.warning(
                    "Reset credits refresh token refresh failed account_id=%s error=%s",
                    refresh_account.id,
                    refresh_exc,
                )
                return
        except Exception as exc:  # scheduler must never crash the loop or mutate account status
            logger.warning(
                "Reset credits refresh failed account_id=%s error=%s",
                refresh_account.id,
                exc,
            )
            return

    if response is None:
        return

    snapshot = build_snapshot(response)
    stored = await store.set_if_generation(account.id, snapshot, snapshot_generation)
    if not stored:
        logger.info(
            "Skipped stale reset credits snapshot account_id=%s",
            account.id,
        )


@asynccontextmanager
async def _reset_credits_accounts_repo_factory() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        yield AccountsRepository(session)


def build_rate_limit_reset_credits_scheduler() -> RateLimitResetCreditsRefreshScheduler:
    settings = get_settings()
    return RateLimitResetCreditsRefreshScheduler(
        interval_seconds=settings.rate_limit_reset_credits_refresh_interval_seconds,
    )
