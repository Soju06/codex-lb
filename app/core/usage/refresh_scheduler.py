from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol, cast

from app.core.balancer.logic import RATE_LIMITED_MIN_COOLDOWN_SECONDS
from app.core.plan_types import normalize_account_plan_type
from app.core.resilience.toggles import resolve_resilience_toggles
from app.core.scheduling.leader_election_handle import get_leader_election as _get_leader_election
from app.core.usage import capacity_for_plan
from app.core.usage.refresh_policy import USAGE_REFRESH_INTERVAL_SECONDS
from app.core.utils.time import naive_utc_to_epoch
from app.db.models import Account, AccountLimitWarmup, AccountStatus, UsageHistory
from app.db.session import detach_session_objects, get_background_session
from app.modules.accounts.background_repository import BackgroundAccountsRepository
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.limit_warmup.service import (
    LimitWarmupService,
    StreamingLimitWarmupSender,
    usage_reset_confirmed,
)
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.load_balancer import background_recovery_state_from_account, effective_routing_tunables
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage import updater as usage_updater_module
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import build_background_usage_updater

logger = logging.getLogger(__name__)

_RECOVERABLE_ACCOUNT_STATUSES = frozenset({AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED})
_BLOCK_RESET_MATCH_TOLERANCE_SECONDS = 5
# Quota-window slots a persisted rate-limit deadline can be anchored to.
# Upstream reports the paid short window and the paid 7d window through the
# primary/secondary slots and the free 30d window through the monthly slot, so
# the anchor search covers all three instead of assuming one plan's shape.
_RESET_EVIDENCE_WINDOWS: tuple[str, ...] = ("primary", "secondary", "monthly")
# Cap for the anchored-evidence lookback. A blocked account keeps accumulating
# one usage row per refresh interval per window, so scanning everything since
# `blocked_at` would grow with how long the account has been benched -- the
# query that rescues it would get more expensive the longer it stays stuck.
# Both the baseline and the transition it anchors sit at the recent end of that
# history, so the newest rows are the only ones that can produce evidence. A
# transition older than this cap falls back to the ordinary persisted cooldown,
# matching the existing fail-closed behavior when retention drops the pair.
_RESET_EVIDENCE_HISTORY_ROW_CAP = 512


def _normalized_usage_window(entry: UsageHistory) -> str:
    """Return the slot an entry belongs to, matching the repository's filter.

    Primary rows may persist a ``NULL`` window; ``UsageRepository`` normalizes
    those to ``"primary"`` when filtering, so window comparisons here must use
    the same normalization or a legacy primary row would never match its slot.
    """

    return entry.window or "primary"


@dataclass(frozen=True, slots=True)
class _ResetEvidence:
    baseline: UsageHistory
    before: UsageHistory
    after: UsageHistory

    @property
    def window(self) -> str:
        return _normalized_usage_window(self.baseline)


class _RecoverableAccountsRepository(Protocol):
    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = None,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None | object = None,
    ) -> bool: ...


class _LatestUsageRepository(Protocol):
    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]: ...


class _BackgroundLimitWarmupRepository:
    async def latest_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]:
        async with get_background_session() as session:
            attempts = await LimitWarmupRepository(session).latest_by_account(account_ids)
            detach_session_objects(session)
            return attempts

    async def try_create_attempt(
        self,
        *,
        account_id: str,
        window: str,
        reset_at: int,
        model: str,
        attempted_at: datetime,
        status: str = "pending",
        reset_at_tolerance_seconds: int = 0,
        require_no_prior_attempt: bool = False,
    ) -> AccountLimitWarmup | None:
        async with get_background_session() as session:
            attempt = await LimitWarmupRepository(session).try_create_attempt(
                account_id=account_id,
                window=window,
                reset_at=reset_at,
                model=model,
                attempted_at=attempted_at,
                status=status,
                reset_at_tolerance_seconds=reset_at_tolerance_seconds,
                require_no_prior_attempt=require_no_prior_attempt,
            )
            detach_session_objects(session)
            return attempt

    async def complete_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        completed_at: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AccountLimitWarmup | None:
        async with get_background_session() as session:
            attempt = await LimitWarmupRepository(session).complete_attempt(
                attempt_id,
                status=status,
                completed_at=completed_at,
                error_code=error_code,
                error_message=error_message,
            )
            detach_session_objects(session)
            return attempt


class _BackgroundRequestLogsRepository:
    async def add_log(self, *args: Any, **kwargs: Any) -> object:
        async with get_background_session() as session:
            return await RequestLogsRepository(session).add_log(*args, **kwargs)


@dataclass(slots=True)
class UsageRefreshScheduler:
    interval_seconds: int
    enabled: bool
    _next_account_index: int = 0
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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
        await usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT.cancel_all()

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            started_at = time.monotonic()
            delay = await self._refresh_once()
            remaining_delay = max(0.0, delay - (time.monotonic() - started_at))
            if remaining_delay <= 0:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=remaining_delay)
            except asyncio.TimeoutError:
                continue

    async def _refresh_once(self) -> float:
        delay = await _get_leader_election().run_if_leader(self._refresh_as_leader)
        if delay is None:
            return float(self.interval_seconds)
        return delay

    async def _refresh_as_leader(self) -> float:
        async with self._lock:
            account_count = 0
            try:
                async with get_background_session() as session:
                    usage_repo = UsageRepository(session)
                    accounts_repo = AccountsRepository(session)
                    accounts = _ordered_usage_refresh_accounts(await accounts_repo.list_accounts())
                    selected_account, cycle_complete = self._select_next_account(accounts)
                    if selected_account is not None:
                        selected_account_ids = [selected_account.id]
                        previous_plan_types = {
                            selected_account.id: normalize_account_plan_type(selected_account.plan_type)
                        }
                        before_primary = await usage_repo.latest_by_account(
                            window="primary",
                            account_ids=selected_account_ids,
                        )
                        before_secondary = await usage_repo.latest_by_account(
                            window="secondary",
                            account_ids=selected_account_ids,
                        )
                        before_monthly = await usage_repo.latest_by_account(
                            window="monthly",
                            account_ids=selected_account_ids,
                        )
                    detach_session_objects(session)
                account_count = len(accounts)
                if selected_account is None:
                    await _invalidate_usage_refresh_caches()
                    return float(self.interval_seconds)

                updater = build_background_usage_updater()
                refresh_started_at = usage_updater_module.utcnow()
                usage_written = await updater.refresh_accounts([selected_account], before_primary)
                if usage_written:
                    async with get_background_session() as session:
                        usage_repo = UsageRepository(session)
                        accounts_repo = AccountsRepository(session)
                        settings_repo = SettingsRepository(session)
                        after_primary = await usage_repo.latest_by_account(
                            window="primary",
                            account_ids=selected_account_ids,
                        )
                        after_secondary = await usage_repo.latest_by_account(
                            window="secondary",
                            account_ids=selected_account_ids,
                        )
                        after_monthly = await usage_repo.latest_by_account(
                            window="monthly",
                            account_ids=selected_account_ids,
                        )
                        dashboard_settings = await settings_repo.get_or_create()
                        refreshed_accounts = await accounts_repo.list_accounts(refresh_existing=True)
                        refreshed_selected_accounts = [
                            account for account in refreshed_accounts if account.id == selected_account.id
                        ]
                        resolved_reset_evidence = await _resolve_reset_evidence(
                            accounts=refreshed_selected_accounts,
                            usage_repo=usage_repo,
                            before_monthly=before_monthly,
                            after_monthly=after_monthly,
                        )
                        detach_session_objects(session)
                    warmup_before_monthly = dict(before_monthly)
                    warmup_after_monthly = dict(after_monthly)
                    for account_id, account_evidence in resolved_reset_evidence.items():
                        # Warm-up consumes the monthly slot; feeding it a
                        # primary/secondary transition would mislabel a paid
                        # short or 7d window as the monthly long window.
                        if account_evidence.window != "monthly":
                            continue
                        warmup_before_monthly[account_id] = account_evidence.before
                        warmup_after_monthly[account_id] = account_evidence.after
                    async with get_background_session() as session:
                        await reconcile_recoverable_account_statuses(
                            accounts_repo=AccountsRepository(session),
                            usage_repo=UsageRepository(session),
                            accounts=refreshed_selected_accounts,
                            reset_evidence=resolved_reset_evidence,
                            dashboard_settings=dashboard_settings,
                        )
                    warmup_service = LimitWarmupService(
                        cast(Any, _BackgroundLimitWarmupRepository()),
                        cast(Any, _BackgroundRequestLogsRepository()),
                        sender=StreamingLimitWarmupSender(
                            cast(AccountsRepository, BackgroundAccountsRepository()),
                            accounts_repo_factory=_background_accounts_repo,
                        ),
                    )
                    await warmup_service.run_after_usage_refresh(
                        accounts=refreshed_selected_accounts,
                        stagger_accounts=refreshed_accounts,
                        settings=dashboard_settings,
                        before_primary=before_primary,
                        before_secondary=_select_long_window_entries(
                            accounts=refreshed_selected_accounts,
                            monthly_entries=warmup_before_monthly,
                            secondary_entries=before_secondary,
                        ),
                        after_primary=after_primary,
                        after_secondary=_select_long_window_entries(
                            accounts=refreshed_selected_accounts,
                            monthly_entries=warmup_after_monthly,
                            secondary_entries=after_secondary,
                        ),
                        previous_plan_types=previous_plan_types,
                        refresh_started_at=refresh_started_at,
                        usage_refresh_interval_seconds=self.interval_seconds,
                    )
                if cycle_complete:
                    await _invalidate_usage_refresh_caches()
            except Exception:
                logger.exception("Usage refresh loop failed")
                return float(self.interval_seconds)
        return _usage_refresh_slice_seconds(self.interval_seconds, account_count)

    def _select_next_account(self, accounts: list[Account]) -> tuple[Account | None, bool]:
        if not accounts:
            self._next_account_index = 0
            return None, True
        index = self._next_account_index % len(accounts)
        next_index = (index + 1) % len(accounts)
        self._next_account_index = next_index
        return accounts[index], next_index == 0


def build_usage_refresh_scheduler() -> UsageRefreshScheduler:
    return UsageRefreshScheduler(interval_seconds=USAGE_REFRESH_INTERVAL_SECONDS, enabled=True)


def _ordered_usage_refresh_accounts(accounts: list[Account]) -> list[Account]:
    return sorted(
        (
            account
            for account in accounts
            if account.status not in (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED)
        ),
        key=lambda account: account.id,
    )


def _usage_refresh_slice_seconds(interval_seconds: int, account_count: int) -> float:
    if account_count <= 0:
        return float(interval_seconds)
    return float(interval_seconds) / account_count


async def _invalidate_usage_refresh_caches() -> None:
    await get_rate_limit_headers_cache().invalidate()
    get_account_selection_cache().invalidate()


@contextlib.asynccontextmanager
async def _background_accounts_repo() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        try:
            yield AccountsRepository(session)
        finally:
            detach_session_objects(session)


async def reconcile_recoverable_account_statuses(
    *,
    accounts_repo: _RecoverableAccountsRepository,
    usage_repo: _LatestUsageRepository,
    accounts: list[Account],
    reset_evidence: dict[str, _ResetEvidence] | None = None,
    dashboard_settings: object | None = None,
) -> int:
    """Repair recoverable account statuses from the latest usage evidence.

    ``dashboard_settings`` is the dashboard-settings row the refresh cycle
    already read; the state builds below resolve soft drain and the routing
    tunables from it once, so the health tier they compute follows the
    dashboard toggle exactly like a request-path state build (``None`` = the
    environment layer, for callers without a row).
    """
    candidates = [account for account in accounts if account.status in _RECOVERABLE_ACCOUNT_STATUSES]
    if not candidates:
        return 0
    routing_tunables = effective_routing_tunables(dashboard_settings)
    soft_drain_enabled = resolve_resilience_toggles(dashboard_settings).soft_drain_enabled

    candidate_ids = [account.id for account in candidates]
    latest_primary = await usage_repo.latest_by_account(window="primary", account_ids=candidate_ids)
    latest_secondary = await usage_repo.latest_by_account(window="secondary", account_ids=candidate_ids)
    latest_monthly = await usage_repo.latest_by_account(window="monthly", account_ids=candidate_ids)

    recovered = 0
    for account in candidates:
        monthly_entry = latest_monthly.get(account.id)
        latest_by_window: dict[str, UsageHistory | None] = {
            "primary": latest_primary.get(account.id),
            "secondary": latest_secondary.get(account.id),
            "monthly": monthly_entry,
        }
        if _confirmed_window_reset_recovery(
            account=account,
            evidence=(reset_evidence or {}).get(account.id),
            latest_by_window=latest_by_window,
        ):
            status = AccountStatus.ACTIVE
            reset_at = None
            blocked_at = None
        else:
            state = background_recovery_state_from_account(
                account=account,
                primary_entry=latest_primary.get(account.id),
                secondary_entry=_select_long_window_entry(
                    account=account,
                    monthly_entry=monthly_entry,
                    secondary_entry=latest_secondary.get(account.id),
                ),
                routing_tunables=routing_tunables,
                soft_drain_enabled=soft_drain_enabled,
            )
            if state.status != AccountStatus.ACTIVE:
                continue
            status = state.status
            reset_at = int(state.reset_at) if state.reset_at else None
            blocked_at = int(state.blocked_at) if state.blocked_at else None
        deactivation_reason = None
        if (
            status == account.status
            and deactivation_reason == account.deactivation_reason
            and reset_at == account.reset_at
            and blocked_at == account.blocked_at
        ):
            continue
        updated = await accounts_repo.update_status_if_current(
            account.id,
            status,
            deactivation_reason,
            reset_at,
            blocked_at=blocked_at,
            expected_status=account.status,
            expected_deactivation_reason=account.deactivation_reason,
            expected_reset_at=account.reset_at,
            expected_blocked_at=account.blocked_at,
        )
        if not updated:
            continue
        account.status = status
        account.deactivation_reason = deactivation_reason
        account.reset_at = reset_at
        account.blocked_at = blocked_at
        recovered += 1
    return recovered


def _sibling_window_blocks_recovery(
    entry: UsageHistory | None,
    *,
    account: Account,
    window: str,
    now: float,
) -> bool:
    """Return whether a non-recovered window would immediately re-block the account.

    Releasing an account whose *other* quota window is still exhausted only buys
    one upstream 429 and a fresh block, so a current sibling at 100% keeps the
    account blocked. Two exclusions keep that from over-blocking:

    * Only windows that carry quota for the account's plan count. A free
      account's primary slot has zero capacity and is a normalization artifact
      of the monthly payload, not a live 5h window.
    * An elapsed window is stale exhaustion evidence rather than a live block
      (see "Usage refresh does not trust elapsed reset windows"). A 100% row
      with no reset metadata is treated as current because nothing proves it
      rolled.
    """

    if entry is None or entry.used_percent < 100.0:
        return False
    if not capacity_for_plan(account.plan_type, window):
        return False
    return entry.reset_at is None or entry.reset_at > now


def _confirmed_window_reset_recovery(
    *,
    account: Account,
    evidence: _ResetEvidence | None,
    latest_by_window: dict[str, UsageHistory | None],
) -> bool:
    """Return whether usage history proves the blocked quota window already reset.

    The persisted ``reset_at`` is the cross-replica authority for a 429, so it
    is only overridden when history identifies *the very window that deadline
    came from* and shows it rolling. The baseline match on ``reset_at`` is what
    binds the evidence to this block: a deadline derived from a generic
    Retry-After hint or a model-scoped throttle matches no quota window's reset
    metadata, and a reset in an unrelated window does not match this block's
    deadline. That anchoring -- not the account's plan -- is what keeps a paid
    account with an exhausted short window from being released by unrelated
    long-window availability.
    """

    if account.status != AccountStatus.RATE_LIMITED:
        return False
    if account.reset_at is None or account.blocked_at is None:
        return False
    now = time.time()
    if now >= account.reset_at:
        return False
    if now < account.blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS:
        return False
    if evidence is None:
        return False
    baseline = evidence.baseline
    before = evidence.before
    after = evidence.after
    window = evidence.window
    if _normalized_usage_window(before) != window or _normalized_usage_window(after) != window:
        return False
    latest = latest_by_window.get(window)
    if latest is None or _normalized_usage_window(latest) != window:
        return False
    if baseline.reset_at is None:
        return False
    if abs(baseline.reset_at - account.reset_at) > _BLOCK_RESET_MATCH_TOLERANCE_SECONDS:
        return False
    if naive_utc_to_epoch(baseline.recorded_at) <= account.blocked_at:
        return False
    if not usage_reset_confirmed(before=before, after=after):
        return False
    if after.used_percent >= 100.0 or latest.used_percent >= 100.0:
        return False
    if any(
        _sibling_window_blocks_recovery(entry, account=account, window=sibling, now=now)
        for sibling, entry in latest_by_window.items()
        if sibling != window
    ):
        return False
    return (
        naive_utc_to_epoch(after.recorded_at) > account.blocked_at
        and naive_utc_to_epoch(latest.recorded_at) > account.blocked_at
    )


async def _resolve_reset_evidence(
    *,
    accounts: list[Account],
    usage_repo: UsageRepository,
    before_monthly: dict[str, UsageHistory],
    after_monthly: dict[str, UsageHistory],
) -> dict[str, _ResetEvidence]:
    """Resolve the reset transition each account's recovery and warm-up can use.

    The in-cycle monthly pair feeds reset-confirmed warm-up for every account
    and is unchanged. For a blocked account the persisted lookup additionally
    searches each quota-window slot for a post-block transition anchored to the
    account's own ``reset_at``, so recovery works after a restart and for
    whichever window upstream actually blocked -- the paid 5h/7d primary and
    secondary slots as well as the free monthly slot.
    """

    evidence: dict[str, _ResetEvidence] = {}
    for account in accounts:
        before = before_monthly.get(account.id)
        after = after_monthly.get(account.id)
        if usage_reset_confirmed(before=before, after=after):
            assert before is not None and after is not None
            evidence[account.id] = _ResetEvidence(
                baseline=before,
                before=before,
                after=after,
            )
        if account.status != AccountStatus.RATE_LIMITED or account.reset_at is None or account.blocked_at is None:
            continue
        since = datetime.fromtimestamp(account.blocked_at, timezone.utc).replace(tzinfo=None)
        for window in _RESET_EVIDENCE_WINDOWS:
            history = await usage_repo.history_since(
                account.id,
                window,
                since,
                limit=_RESET_EVIDENCE_HISTORY_ROW_CAP,
            )
            persisted = _latest_confirmed_reset_transition_after_baseline(
                [entry for entry in history if entry.recorded_at > since],
                expected_reset_at=account.reset_at,
                reset_at_tolerance_seconds=_BLOCK_RESET_MATCH_TOLERANCE_SECONDS,
            )
            if persisted is not None:
                # The baseline deadline match makes at most one window the
                # anchor for this block, so the first hit is the answer.
                evidence[account.id] = persisted
                break
    return evidence


def _latest_confirmed_reset_transition_after_baseline(
    history: list[UsageHistory],
    *,
    expected_reset_at: int,
    reset_at_tolerance_seconds: int,
) -> _ResetEvidence | None:
    baseline = next(
        (
            (index, entry)
            for index, entry in enumerate(history)
            if entry.reset_at is not None and abs(entry.reset_at - expected_reset_at) <= reset_at_tolerance_seconds
        ),
        None,
    )
    if baseline is None:
        return None
    baseline_index, baseline_entry = baseline

    latest_transition: _ResetEvidence | None = None
    for index in range(baseline_index, len(history) - 1):
        before = history[index]
        after = history[index + 1]
        if usage_reset_confirmed(before=before, after=after):
            latest_transition = _ResetEvidence(
                baseline=baseline_entry,
                before=before,
                after=after,
            )
    return latest_transition


def _select_long_window_entry(
    *,
    account: Account,
    monthly_entry: UsageHistory | None,
    secondary_entry: UsageHistory | None,
) -> UsageHistory | None:
    if monthly_entry is not None and capacity_for_plan(account.plan_type, "monthly") is not None:
        return monthly_entry
    return secondary_entry


def _select_long_window_entries(
    *,
    accounts: list[Account],
    monthly_entries: dict[str, UsageHistory],
    secondary_entries: dict[str, UsageHistory],
) -> dict[str, UsageHistory]:
    selected: dict[str, UsageHistory] = {}
    for account in accounts:
        entry = _select_long_window_entry(
            account=account,
            monthly_entry=monthly_entries.get(account.id),
            secondary_entry=secondary_entries.get(account.id),
        )
        if entry is not None:
            selected[account.id] = entry
    return selected
