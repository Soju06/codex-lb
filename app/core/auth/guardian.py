from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, cast

from app.core.auth.refresh import RefreshError
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.account_cache import get_account_selection_cache

logger = logging.getLogger(__name__)


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


class _AccountsRepositoryLike(Protocol):
    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]: ...

    async def get_by_id(self, account_id: str) -> Account | None: ...


class _AuthManagerLike(Protocol):
    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account: ...


_RepoFactory = Callable[[], AbstractAsyncContextManager[_AccountsRepositoryLike]]
_AuthManagerFactory = Callable[[_AccountsRepositoryLike], _AuthManagerLike]
_LeaderElectionFactory = Callable[[], _LeaderElectionLike]
_Sleep = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class _FailureBackoff:
    attempts: int
    retry_after_monotonic: float


@dataclass(slots=True)
class AuthGuardianScheduler:
    interval_seconds: int
    enabled: bool
    max_age_seconds: int
    batch_size: int
    concurrency: int
    jitter_seconds: float
    failure_backoff_base_seconds: float = 300.0
    failure_backoff_max_seconds: float = 3600.0
    leader_election_factory: _LeaderElectionFactory = field(default_factory=lambda: _get_leader_election)
    repo_factory: _RepoFactory = field(default_factory=lambda: _default_accounts_repo_factory)
    auth_manager_factory: _AuthManagerFactory = field(default_factory=lambda: _default_auth_manager_factory)
    sleep: _Sleep = field(default_factory=lambda: asyncio.sleep)
    now: Callable[[], datetime] = field(default_factory=lambda: utcnow)
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _failures: dict[str, _FailureBackoff] = field(default_factory=dict)

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            jitter = _jitter_delay(self.jitter_seconds)
            if jitter > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=jitter)
                    break
                except asyncio.TimeoutError:
                    pass
            try:
                await self._refresh_once()
            except Exception:
                logger.exception("Auth Guardian refresh pass failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _refresh_once(self) -> None:
        if not await self.leader_election_factory().try_acquire():
            return
        async with self._lock:
            async with self.repo_factory() as repo:
                accounts = await repo.list_accounts(refresh_existing=True)
                candidates = select_auth_guardian_candidates(
                    accounts,
                    now=self.now(),
                    max_age_seconds=self.max_age_seconds,
                    limit=len(accounts),
                )
                candidates = [account for account in candidates if not self._in_backoff(account.id)]
                candidates = candidates[: max(0, self.batch_size)]
            if not candidates:
                return
            semaphore = asyncio.Semaphore(max(1, self.concurrency))
            await asyncio.gather(*(self._refresh_candidate(account.id, semaphore) for account in candidates))

    async def _refresh_candidate(self, account_id: str, semaphore: asyncio.Semaphore) -> None:
        if self._in_backoff(account_id):
            return
        async with semaphore:
            async with self.repo_factory() as repo:
                account = await repo.get_by_id(account_id)
                if account is None:
                    self._failures.pop(account_id, None)
                    return
                if not _auth_guardian_account_is_stale_active(
                    account,
                    now=self.now(),
                    max_age_seconds=self.max_age_seconds,
                ):
                    return
                manager = self.auth_manager_factory(repo)
                try:
                    refresh_task = asyncio.create_task(manager.ensure_fresh(account, force=True))
                    try:
                        await asyncio.shield(refresh_task)
                    except asyncio.CancelledError:
                        with contextlib.suppress(Exception):
                            await refresh_task
                        raise
                except RefreshError as exc:
                    self._record_failure(account_id)
                    if exc.is_permanent:
                        get_account_selection_cache().invalidate()
                    logger.warning(
                        "Auth Guardian refresh failed account_id=%s account_alias=%s code=%s permanent=%s transport=%s",
                        account.id,
                        _safe_account_alias(account),
                        exc.code,
                        exc.is_permanent,
                        exc.transport_error,
                    )
                    return
                except Exception as exc:
                    self._record_failure(account_id)
                    logger.warning(
                        "Auth Guardian refresh failed account_id=%s account_alias=%s error_type=%s",
                        account.id,
                        _safe_account_alias(account),
                        exc.__class__.__name__,
                        exc_info=True,
                    )
                    return
                self._failures.pop(account_id, None)
                get_account_selection_cache().invalidate()
                logger.info(
                    "Auth Guardian refreshed account_id=%s account_alias=%s",
                    account.id,
                    _safe_account_alias(account),
                )

    def _in_backoff(self, account_id: str) -> bool:
        failure = self._failures.get(account_id)
        if failure is None:
            return False
        if failure.retry_after_monotonic > time.monotonic():
            return True
        return False

    def _record_failure(self, account_id: str) -> None:
        previous = self._failures.get(account_id)
        attempts = 1 if previous is None else previous.attempts + 1
        base = max(0.0, float(self.failure_backoff_base_seconds))
        cap = max(base, float(self.failure_backoff_max_seconds))
        delay = min(cap, base * (2 ** min(attempts - 1, 6)))
        delay += _jitter_delay(self.jitter_seconds)
        self._failures[account_id] = _FailureBackoff(
            attempts=attempts,
            retry_after_monotonic=time.monotonic() + delay,
        )


def select_auth_guardian_candidates(
    accounts: list[Account],
    *,
    now: datetime,
    max_age_seconds: int,
    limit: int,
) -> list[Account]:
    candidates = [
        account
        for account in accounts
        if _auth_guardian_account_is_stale_active(
            account,
            now=now,
            max_age_seconds=max_age_seconds,
        )
    ]
    candidates.sort(key=lambda account: to_utc_naive(account.last_refresh))
    return candidates[: max(0, limit)]


def build_auth_guardian_scheduler() -> AuthGuardianScheduler:
    from app.core.config.settings import get_settings

    settings = get_settings()
    multi_replica = len(settings.http_responses_session_bridge_instance_ring) > 1
    return AuthGuardianScheduler(
        interval_seconds=settings.auth_guardian_interval_seconds,
        enabled=settings.auth_guardian_enabled and (settings.leader_election_enabled or not multi_replica),
        max_age_seconds=settings.auth_guardian_max_refresh_age_seconds,
        batch_size=settings.auth_guardian_batch_size,
        concurrency=settings.auth_guardian_concurrency,
        jitter_seconds=settings.auth_guardian_jitter_seconds,
        failure_backoff_base_seconds=settings.auth_guardian_failure_backoff_base_seconds,
        failure_backoff_max_seconds=settings.auth_guardian_failure_backoff_max_seconds,
    )


def _auth_guardian_account_is_stale_active(
    account: Account,
    *,
    now: datetime,
    max_age_seconds: int,
) -> bool:
    if account.status != AccountStatus.ACTIVE:
        return False
    age = to_utc_naive(now) - to_utc_naive(account.last_refresh)
    return age > timedelta(seconds=max_age_seconds)


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@asynccontextmanager
async def _default_accounts_repo_factory() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        yield AccountsRepository(session)


def _default_auth_manager_factory(repo: _AccountsRepositoryLike) -> _AuthManagerLike:
    return AuthManager(cast(AccountsRepository, repo), refresh_repo_factory=_default_accounts_repo_factory)


def _jitter_delay(max_seconds: float) -> float:
    if max_seconds <= 0:
        return 0.0
    return random.uniform(0.0, max_seconds)


def _safe_account_alias(account: Account) -> str:
    alias = (account.alias or "").strip()
    if alias:
        return alias[:64]
    return _mask_email(account.email)


def _mask_email(email: str) -> str:
    if "@" not in email:
        return email[:2] + "***" if email else ""
    local, domain = email.split("@", 1)
    if not local:
        return f"***@{domain}"
    if len(local) == 1:
        masked_local = f"{local}***"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"
