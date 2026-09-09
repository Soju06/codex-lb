"""Auto-redeem banked rate-limit reset credits on full pool exhaustion.

Standing operator rule (2026-07-17): a banked OpenAI reset credit may only be
spent when **all** subscription-usable OpenAI accounts are used up. While any
account can still serve, credits stay banked. The scheduler encodes that rule
deterministically; redemption itself reuses
``AccountsService.redeem_rate_limit_reset_credit`` so the post-redeem usage
refresh and selection-cache invalidation apply.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import AuditService
from app.core.clients import rate_limit_resets
from app.core.clients.rate_limit_resets import ResetCreditsError
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import OPENAI_PROVIDER_NAME, normalize_provider_name
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts import reset_credit_cache
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.reset_credit_attempts import ResetCreditAttemptsRepository
from app.modules.accounts.reset_credit_recovery import refresh_standard_capacity
from app.modules.accounts.subscription_status import is_subscription_usable
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)

_SERVING_STATUSES = frozenset(
    {AccountStatus.ACTIVE, AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED}
)
# ISO timestamps sort lexicographically; missing expiry sorts last.
_NO_EXPIRY_SORT_KEY = "9999"


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


def serving_openai_pool(accounts: list[Account]) -> list[Account]:
    return [
        account
        for account in accounts
        if normalize_provider_name(account.provider) == OPENAI_PROVIDER_NAME
        and account.status in _SERVING_STATUSES
        and is_subscription_usable(account)
    ]


def exhausted_pool_or_none(accounts: list[Account]) -> list[Account] | None:
    """Return redemption candidates iff the whole serving pool is exhausted.

    ``None`` means "do not redeem": the pool is empty or at least one account
    is still active. Candidates come back ordered ``quota_exceeded`` first —
    weekly-dead accounts recover slowest, so a full reset buys the most there.
    """
    pool = serving_openai_pool(accounts)
    if not pool:
        return None
    if any(account.status == AccountStatus.ACTIVE for account in pool):
        return None
    return sorted(pool, key=lambda account: account.status != AccountStatus.QUOTA_EXCEEDED)


@dataclass(slots=True)
class ResetCreditAutoRedeemScheduler:
    interval_seconds: int
    cooldown_seconds: int
    enabled: bool
    expiry_enabled: bool = False
    expiry_window_hours: int = 24
    expiry_sweep_interval_seconds: int = 3600
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _last_redeemed_at: datetime | None = None
    _last_expiry_sweep_at: datetime | None = None

    async def start(self) -> None:
        if not (self.enabled or self.expiry_enabled):
            return
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
            try:
                await self._tick()
            except Exception:
                logger.exception("Reset-credit auto-redeem loop failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    def _cooldown_active(self) -> bool:
        if self._last_redeemed_at is None:
            return False
        return (utcnow() - self._last_redeemed_at).total_seconds() < self.cooldown_seconds

    def _expiry_sweep_due(self) -> bool:
        if not self.expiry_enabled:
            return False
        if self._last_expiry_sweep_at is None:
            return True
        return (utcnow() - self._last_expiry_sweep_at).total_seconds() >= self.expiry_sweep_interval_seconds

    async def _tick(self) -> None:
        exhaustion_eligible = self.enabled and not self._cooldown_active()
        expiry_due = self._expiry_sweep_due()
        if not self.enabled and not expiry_due:
            return
        if not await _get_leader_election().try_acquire():
            return
        async with get_background_session() as session:
            repo = AccountsRepository(session)
            attempts = ResetCreditAttemptsRepository(session)
            active = await attempts.active()
            if active is not None:
                service = _build_accounts_service(repo, session)
                try:
                    await service.redeem_rate_limit_reset_credit(
                        active.account_id, credit_id=active.credit_id, trigger=active.trigger,
                    )
                except Exception:
                    logger.warning("Reset recovery remains pending attempt=%s", active.id, exc_info=True)
                return
            applied_at = await attempts.latest_applied_at()
            if applied_at is not None:
                self._last_redeemed_at = to_utc_naive(applied_at)
                exhaustion_eligible = self.enabled and not self._cooldown_active()
            accounts = list(await repo.list_accounts())
            if exhaustion_eligible:
                candidates = exhausted_pool_or_none(accounts)
                if candidates is not None:
                    usage_repo = UsageRepository(session)
                    updater = UsageUpdater(usage_repo, repo)
                    for account in candidates:
                        available = await refresh_standard_capacity(account, updater, usage_repo)
                        if available is not False:
                            # Unknown refresh or any recovered capacity keeps
                            # credits banked and lets ordinary failover recover.
                            break
                    else:
                        candidates = exhausted_pool_or_none(await repo.list_accounts(refresh_existing=True))
                        if candidates is not None:
                            await self._redeem_first_available(session, repo, candidates)
                            return
            if expiry_due:
                self._last_expiry_sweep_at = utcnow()
                await self._expiry_sweep(session, repo, accounts)

    async def _expiry_sweep(
        self,
        session: AsyncSession,
        repo: AccountsRepository,
        accounts: list[Account],
    ) -> None:
        """Redeem credits about to lapse, regardless of pool capacity.

        Safe to attempt early: upstream ``nothing_to_reset`` leaves the credit
        banked (only ``reset`` consumes one), so unredeemable attempts retry on
        the next sweep until the credit expires or the account has usage to
        wipe.
        """
        pool = serving_openai_pool(accounts)
        if not pool:
            return
        service = _build_accounts_service(repo, session)
        encryptor = TokenEncryptor()
        window = timedelta(hours=self.expiry_window_hours)
        now = utcnow()
        for account in pool:
            try:
                payload = await rate_limit_resets.fetch_reset_credits(
                    access_token=encryptor.decrypt(account.access_token_encrypted),
                    chatgpt_account_id=account.chatgpt_account_id,
                )
            except ResetCreditsError as exc:
                logger.warning(
                    "Reset-credit listing failed during expiry sweep account=%s error=%s",
                    account.id,
                    exc.message,
                )
                continue
            available = [credit for credit in payload.credits if credit.status == "available"]
            reset_credit_cache.record_count(account.id, len(available))
            expiring = [
                credit for credit in available if _expires_within(credit.expires_at, now, window)
            ]
            for credit in expiring:
                await self._redeem_expiring(service, account, credit.id, credit.expires_at)
                # A second expiry reset needs a new inventory/usage sweep.
                # This also prevents unknown results from fanning out.
                return

    async def _redeem_expiring(self, service, account: Account, credit_id: str, expires_at: str | None) -> None:  # noqa: ANN001
        try:
            result = await service.redeem_rate_limit_reset_credit(account.id, credit_id=credit_id, trigger="expiring")
        except Exception:
            logger.exception(
                "Expiring reset-credit redemption failed account=%s credit=%s",
                account.id,
                credit_id,
            )
            return
        if result is None:
            return
        if result.code == "nothing_to_reset":
            logger.info(
                "Expiring reset credit not redeemable yet (nothing to reset) account=%s credit=%s expires_at=%s",
                account.id,
                credit_id,
                expires_at,
            )
            return
        if result.status != "redeemed":
            logger.warning(
                "Expiring reset-credit redemption not applied account=%s credit=%s code=%s",
                account.id,
                credit_id,
                result.code,
            )
            return
        logger.info(
            "Auto-redeemed expiring reset credit account=%s email=%s credit=%s expires_at=%s windows_reset=%s",
            account.id,
            account.email,
            credit_id,
            expires_at,
            result.windows_reset,
        )
        await _wake_upstream_limiter(service, account)
        AuditService.log_async(
            "account_reset_credit_redeemed",
            actor_ip=None,
            details={
                "account_id": account.id,
                "credit_id": credit_id,
                "code": result.code,
                "windows_reset": result.windows_reset,
                "trigger": "expiring",
                "expires_at": expires_at,
            },
        )

    async def _redeem_first_available(
        self,
        session: AsyncSession,
        repo: AccountsRepository,
        candidates: list[Account],
    ) -> None:
        service = _build_accounts_service(repo, session)
        encryptor = TokenEncryptor()
        ranked = await _rank_candidates_by_credit_expiry(candidates, encryptor)
        if not ranked:
            logger.info(
                "OpenAI pool fully exhausted but no banked reset credits available accounts=%s",
                len(candidates),
            )
            return
        for account, credit_id in ranked:
            try:
                result = await service.redeem_rate_limit_reset_credit(account.id, credit_id=credit_id, trigger="auto")
            except Exception:
                logger.exception(
                    "Auto reset-credit redemption requires reconciliation account=%s credit=%s",
                    account.id,
                    credit_id,
                )
                return
            if result is None or result.status != "redeemed":
                logger.warning(
                    "Auto reset-credit redemption not applied account=%s credit=%s code=%s",
                    account.id,
                    credit_id,
                    result.code if result else "missing_account",
                )
                continue
            self._last_redeemed_at = utcnow()
            logger.info(
                "Auto-redeemed reset credit account=%s email=%s credit=%s windows_reset=%s",
                account.id,
                account.email,
                credit_id,
                result.windows_reset,
            )
            await _wake_upstream_limiter(service, account)
            AuditService.log_async(
                "account_reset_credit_redeemed",
                actor_ip=None,
                details={
                    "account_id": account.id,
                    "credit_id": credit_id,
                    "code": result.code,
                    "windows_reset": result.windows_reset,
                    "trigger": "auto",
                },
            )
            return


async def _wake_upstream_limiter(service, account: Account) -> None:  # noqa: ANN001
    """Probe the account after a redemption so ``/wham/usage`` re-evaluates.

    Upstream serves stale pre-reset usage until a real request wakes the
    limiter (the account-probe endpoint exists for exactly this). Without the
    wake, a redeemed account can keep reporting ``quota_exceeded`` locally —
    which the exhaustion rule would misread as "still exhausted" and, after
    cooldown, spend another credit on. Best-effort: probe failure only delays
    recovery until organic traffic or the next probe.
    """
    try:
        await service.probe_account(account.id)
    except Exception:
        logger.warning(
            "Post-redemption wake probe failed account=%s; usage may stay stale until next refresh",
            account.id,
            exc_info=True,
        )


def _expires_within(expires_at: str | None, now: datetime, window: timedelta) -> bool:
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparseable reset-credit expiry expires_at=%s", expires_at)
        return False
    return to_utc_naive(parsed) <= now + window


async def _rank_candidates_by_credit_expiry(
    candidates: list[Account],
    encryptor: TokenEncryptor,
) -> list[tuple[Account, str]]:
    """Pair each candidate with its earliest-expiring available credit.

    Preserves the quota_exceeded-first account ordering as the primary key and
    orders by credit expiry within each status group.
    """
    ranked: list[tuple[int, str, Account, str]] = []
    for account in candidates:
        try:
            payload = await rate_limit_resets.fetch_reset_credits(
                access_token=encryptor.decrypt(account.access_token_encrypted),
                chatgpt_account_id=account.chatgpt_account_id,
            )
        except ResetCreditsError as exc:
            logger.warning(
                "Reset-credit listing failed during auto-redeem sweep account=%s error=%s",
                account.id,
                exc.message,
            )
            continue
        available = [credit for credit in payload.credits if credit.status == "available"]
        reset_credit_cache.record_count(account.id, len(available))
        if not available:
            continue
        earliest = min(available, key=lambda credit: credit.expires_at or _NO_EXPIRY_SORT_KEY)
        status_rank = 0 if account.status == AccountStatus.QUOTA_EXCEEDED else 1
        ranked.append((status_rank, earliest.expires_at or _NO_EXPIRY_SORT_KEY, account, earliest.id))
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    return [(account, credit_id) for _, _, account, credit_id in ranked]


def _build_accounts_service(repo: AccountsRepository, session: AsyncSession):  # noqa: ANN202
    # Deferred imports: app.dependencies imports module routers at load time,
    # and importing it at module scope from here would cycle through main.py.
    from app.dependencies import _accounts_repo_context
    from app.modules.accounts.auth_manager import AuthManager
    from app.modules.accounts.service import AccountsService

    return AccountsService(
        repo,
        UsageRepository(session),
        auth_manager=AuthManager(repo, refresh_repo_factory=_accounts_repo_context),
    )


def build_reset_credit_auto_redeem_scheduler() -> ResetCreditAutoRedeemScheduler:
    settings = get_settings()
    return ResetCreditAutoRedeemScheduler(
        interval_seconds=settings.reset_credit_auto_redeem_interval_seconds,
        cooldown_seconds=settings.reset_credit_auto_redeem_cooldown_seconds,
        enabled=settings.reset_credit_auto_redeem_enabled,
        expiry_enabled=settings.reset_credit_expiry_redeem_enabled,
        expiry_window_hours=settings.reset_credit_expiry_redeem_window_hours,
        expiry_sweep_interval_seconds=settings.reset_credit_expiry_sweep_interval_seconds,
    )
