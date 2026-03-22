from __future__ import annotations

import logging
import time
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable

import anyio

from app.core import usage as usage_core
from app.core.balancer import (
    AccountState,
    RoutingStrategy,
    SelectionResult,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    select_account,
)
from app.core.balancer.types import UpstreamError
from app.core.config.settings import get_settings
from app.core.openai.model_registry import get_model_registry
from app.core.usage.quota import apply_usage_quota
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, StickySessionKind, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.additional_model_limits import get_additional_quota_key_for_model_id
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.usage.additional_quota_keys import canonicalize_additional_quota_key

logger = logging.getLogger(__name__)

_STICKY_GRACE_PERIOD_SECONDS = 10.0
_RECOVERABLE_STATUSES = frozenset(
    {
        AccountStatus.ACTIVE,
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
    }
)

NO_PLAN_SUPPORT_FOR_MODEL = "no_plan_support_for_model"
ADDITIONAL_QUOTA_DATA_UNAVAILABLE = "additional_quota_data_unavailable"
NO_ADDITIONAL_QUOTA_ELIGIBLE_ACCOUNTS = "no_additional_quota_eligible_accounts"


@dataclass
class RuntimeState:
    reset_at: float | None = None
    cooldown_until: float | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    version: int = 0


@dataclass
class AccountSelection:
    account: Account | None
    error_message: str | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _SelectionInputs:
    accounts: list[Account]
    latest_primary: dict[str, UsageHistory]
    latest_secondary: dict[str, UsageHistory]
    error_message: str | None = None
    error_code: str | None = None


class LoadBalancer:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._runtime: dict[str, RuntimeState] = {}
        self._runtime_lock = anyio.Lock()

    async def select_account(
        self,
        sticky_key: str | None = None,
        *,
        sticky_kind: StickySessionKind | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
        prefer_earlier_reset_accounts: bool = False,
        routing_strategy: RoutingStrategy = "usage_weighted",
        model: str | None = None,
        additional_limit_name: str | None = None,
        exclude_account_ids: Collection[str] | None = None,
    ) -> AccountSelection:
        excluded_ids = set(exclude_account_ids or ())

        async def load_selection_inputs() -> _SelectionInputs:
            selection_inputs = await self._load_selection_inputs(
                model=model,
                additional_limit_name=additional_limit_name,
            )
            if excluded_ids and selection_inputs.accounts:
                selection_inputs = _SelectionInputs(
                    accounts=[account for account in selection_inputs.accounts if account.id not in excluded_ids],
                    latest_primary=selection_inputs.latest_primary,
                    latest_secondary=selection_inputs.latest_secondary,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                )
            return selection_inputs

        selection_inputs = await load_selection_inputs()
        if selection_inputs.error_code is not None and not selection_inputs.accounts:
            return AccountSelection(
                account=None,
                error_message=selection_inputs.error_message,
                error_code=selection_inputs.error_code,
            )

        selected_snapshot: Account | None = None
        selected_runtime_version: int | None = None
        error_message: str | None = None
        selected_states: list[AccountState] = []
        selected_account_map: dict[str, Account] = {}
        if sticky_key is None:
            while True:
                async with self._runtime_lock:
                    self._prune_runtime(selection_inputs.accounts)
                    states, account_map = _build_states(
                        accounts=selection_inputs.accounts,
                        latest_primary=selection_inputs.latest_primary,
                        latest_secondary=selection_inputs.latest_secondary,
                        runtime=self._runtime,
                    )
                    runtime_versions = {
                        account.id: self._runtime.get(account.id, RuntimeState()).version
                        for account in selection_inputs.accounts
                    }

                result = select_account(
                    states,
                    prefer_earlier_reset=prefer_earlier_reset_accounts,
                    routing_strategy=routing_strategy,
                )

                reload_selection_inputs = False
                async with self._runtime_lock:
                    if any(
                        self._runtime.get(account.id, RuntimeState()).version != runtime_versions[account.id]
                        for account in selection_inputs.accounts
                    ):
                        reload_selection_inputs = True
                    else:
                        selected_account_map = account_map
                        selected_states = []
                        for state in states:
                            account = account_map.get(state.account_id)
                            if account is None:
                                continue
                            self._sync_runtime_state(
                                account,
                                state,
                                selected=result.account is not None and state.account_id == result.account.account_id,
                            )
                            selected_states.append(state)
                        if result.account is not None:
                            selected = account_map.get(result.account.account_id)
                            if selected is None:
                                error_message = result.error_message
                            else:
                                selected_reset_at = selected.reset_at
                                for state in selected_states:
                                    if state.account_id == result.account.account_id:
                                        state.status = result.account.status
                                        state.deactivation_reason = result.account.deactivation_reason
                                        selected_reset_at = int(state.reset_at) if state.reset_at else None
                                        break
                                selected_snapshot = _clone_account(selected)
                                selected_snapshot.status = result.account.status
                                selected_snapshot.deactivation_reason = result.account.deactivation_reason
                                selected_snapshot.reset_at = selected_reset_at
                                selected_runtime_version = self._runtime.get(selected.id, RuntimeState()).version
                        else:
                            error_message = result.error_message

                if reload_selection_inputs:
                    selection_inputs = await load_selection_inputs()
                    if selection_inputs.error_code is not None and not selection_inputs.accounts:
                        return AccountSelection(
                            account=None,
                            error_message=selection_inputs.error_message,
                            error_code=selection_inputs.error_code,
                        )
                    continue

                async with self._repo_factory() as repos:
                    stale_account_ids = await self._persist_selection_state(
                        repos.accounts,
                        selected_account_map,
                        selected_states,
                    )
                stale_account_ids = stale_account_ids or set()
                if selected_snapshot is None:
                    async with self._runtime_lock:
                        runtime_changed = any(
                            self._runtime.get(account.id, RuntimeState()).version != runtime_versions[account.id]
                            for account in selection_inputs.accounts
                        )
                    if runtime_changed:
                        selection_inputs = await load_selection_inputs()
                        if selection_inputs.error_code is not None and not selection_inputs.accounts:
                            return AccountSelection(
                                account=None,
                                error_message=selection_inputs.error_message,
                                error_code=selection_inputs.error_code,
                            )
                        error_message = None
                        selected_states = []
                        selected_account_map = {}
                        continue
                if selected_snapshot is not None and selected_runtime_version is not None:
                    async with self._runtime_lock:
                        current_runtime = self._runtime.get(selected_snapshot.id)
                        runtime_changed = current_runtime is None or current_runtime.version != selected_runtime_version
                    if selected_snapshot.id in stale_account_ids or runtime_changed:
                        selection_inputs = await load_selection_inputs()
                        if selection_inputs.error_code is not None and not selection_inputs.accounts:
                            return AccountSelection(
                                account=None,
                                error_message=selection_inputs.error_message,
                                error_code=selection_inputs.error_code,
                            )
                        selected_snapshot = None
                        selected_runtime_version = None
                        error_message = None
                        selected_states = []
                        selected_account_map = {}
                        continue
                break
        else:
            while True:
                async with self._runtime_lock:
                    self._prune_runtime(selection_inputs.accounts)
                    states, account_map = _build_states(
                        accounts=selection_inputs.accounts,
                        latest_primary=selection_inputs.latest_primary,
                        latest_secondary=selection_inputs.latest_secondary,
                        runtime=self._runtime,
                    )
                    async with self._repo_factory() as repos:
                        result = await self._select_with_stickiness(
                            states=states,
                            account_map=account_map,
                            sticky_key=sticky_key,
                            sticky_kind=sticky_kind,
                            reallocate_sticky=reallocate_sticky,
                            sticky_max_age_seconds=sticky_max_age_seconds,
                            prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                            routing_strategy=routing_strategy,
                            sticky_repo=repos.sticky_sessions,
                        )
                        selected_account_map = account_map
                        selected_states = []
                        for state in states:
                            account = account_map.get(state.account_id)
                            if account is None:
                                continue
                            self._sync_runtime_state(
                                account,
                                state,
                                selected=result.account is not None and state.account_id == result.account.account_id,
                            )
                            selected_states.append(state)
                        if result.account is not None:
                            selected = account_map.get(result.account.account_id)
                            if selected is None:
                                error_message = result.error_message
                            else:
                                selected_reset_at = selected.reset_at
                                for state in selected_states:
                                    if state.account_id == result.account.account_id:
                                        state.status = result.account.status
                                        state.deactivation_reason = result.account.deactivation_reason
                                        selected_reset_at = int(state.reset_at) if state.reset_at else None
                                        break
                                selected_snapshot = _clone_account(selected)
                                selected_snapshot.status = result.account.status
                                selected_snapshot.deactivation_reason = result.account.deactivation_reason
                                selected_snapshot.reset_at = selected_reset_at
                        else:
                            error_message = result.error_message

                        stale_account_ids = await self._persist_selection_state(
                            repos.accounts,
                            selected_account_map,
                            selected_states,
                        )
                stale_account_ids = stale_account_ids or set()
                if selected_snapshot is not None and selected_snapshot.id in stale_account_ids:
                    selection_inputs = await load_selection_inputs()
                    if selection_inputs.error_code is not None and not selection_inputs.accounts:
                        return AccountSelection(
                            account=None,
                            error_message=selection_inputs.error_message,
                            error_code=selection_inputs.error_code,
                        )
                    selected_snapshot = None
                    error_message = None
                    selected_states = []
                    selected_account_map = {}
                    continue
                break

        if selected_snapshot is None:
            logger.warning(
                "No account selected strategy=%s sticky=%s model=%s error=%s",
                routing_strategy,
                bool(sticky_key),
                model,
                error_message,
            )

        if selected_snapshot is None:
            return AccountSelection(account=None, error_message=error_message, error_code=None)
        logger.info(
            "Selected account_id=%s strategy=%s sticky=%s model=%s",
            selected_snapshot.id,
            routing_strategy,
            bool(sticky_key),
            model,
        )
        return AccountSelection(account=selected_snapshot, error_message=None, error_code=None)

    async def _load_selection_inputs(
        self,
        *,
        model: str | None,
        additional_limit_name: str | None = None,
    ) -> _SelectionInputs:
        async with self._repo_factory() as repos:
            all_accounts = await repos.accounts.list_accounts()
            effective_limit_name = additional_limit_name or _gated_limit_name_for_model(model)
            accounts = all_accounts
            if model and (effective_limit_name is None or _mapped_model_has_registry_entry(model)):
                accounts = _filter_accounts_for_model(accounts, model)
            if model and not accounts:
                if not all_accounts:
                    return _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                    )
                return _SelectionInputs(
                    accounts=[],
                    latest_primary={},
                    latest_secondary={},
                    error_message=f"No accounts with a plan supporting model '{model}'",
                    error_code=NO_PLAN_SUPPORT_FOR_MODEL,
                )

            if effective_limit_name:
                accounts, error_code, error_message = await self._filter_accounts_for_additional_limit(
                    accounts,
                    model=model,
                    limit_name=effective_limit_name,
                    repos=repos,
                )
                if not accounts:
                    return _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        error_message=error_message,
                        error_code=error_code,
                    )
            if not accounts:
                return _SelectionInputs(
                    accounts=[],
                    latest_primary={},
                    latest_secondary={},
                )

            latest_primary = await repos.usage.latest_by_account()
            latest_secondary = await repos.usage.latest_by_account(window="secondary")
            return _SelectionInputs(
                accounts=[_clone_account(account) for account in accounts],
                latest_primary={
                    account_id: _clone_usage_history(entry) for account_id, entry in latest_primary.items()
                },
                latest_secondary={
                    account_id: _clone_usage_history(entry) for account_id, entry in latest_secondary.items()
                },
            )

    async def _filter_accounts_for_additional_limit(
        self,
        accounts: list[Account],
        *,
        model: str | None,
        limit_name: str,
        repos: ProxyRepositories,
    ) -> tuple[list[Account], str | None, str | None]:
        if not accounts:
            return [], None, None

        fresh_since = _additional_usage_fresh_since()
        account_ids = [account.id for account in accounts]
        latest_primary = await _latest_additional_by_key(
            repos.additional_usage,
            limit_name,
            "primary",
            account_ids=account_ids,
        )
        latest_secondary = await _latest_additional_by_key(
            repos.additional_usage,
            limit_name,
            "secondary",
            account_ids=account_ids,
        )
        fresh_primary = await _latest_additional_by_key(
            repos.additional_usage,
            limit_name,
            "primary",
            account_ids=account_ids,
            since=fresh_since,
        )
        fresh_secondary = await _latest_additional_by_key(
            repos.additional_usage,
            limit_name,
            "secondary",
            account_ids=account_ids,
            since=fresh_since,
        )

        fresh_account_ids = set(fresh_primary) | set(fresh_secondary)

        eligible_accounts: list[Account] = []
        blocked_by_data = False
        for account in accounts:
            eligibility = _additional_quota_eligibility(
                account_id=account.id,
                latest_primary=latest_primary,
                latest_secondary=latest_secondary,
                fresh_primary=fresh_primary,
                fresh_secondary=fresh_secondary,
            )
            if eligibility == "eligible":
                eligible_accounts.append(account)
                continue
            if eligibility == "data_unavailable":
                blocked_by_data = True

        if not eligible_accounts:
            error_code = ADDITIONAL_QUOTA_DATA_UNAVAILABLE if blocked_by_data else NO_ADDITIONAL_QUOTA_ELIGIBLE_ACCOUNTS
            error_message = (
                f"No fresh additional quota data available for model '{model}'"
                if blocked_by_data
                else f"No accounts with available additional quota for model '{model}'"
            )
            logger.warning(
                (
                    "Blocked gated model routing model=%s limit_name=%s reason=%s "
                    "freshness_since=%s candidate_accounts=%s fresh_accounts=%s"
                ),
                model,
                limit_name,
                error_code,
                fresh_since.isoformat(),
                len(accounts),
                len(fresh_account_ids),
            )
            return ([], error_code, error_message)

        logger.info(
            (
                "Applied gated model routing model=%s limit_name=%s "
                "candidate_accounts=%s fresh_accounts=%s eligible_accounts=%s"
            ),
            model,
            limit_name,
            len(accounts),
            len(fresh_account_ids),
            len(eligible_accounts),
        )
        return eligible_accounts, None, None

    def _prune_runtime(self, accounts: Iterable[Account]) -> None:
        account_ids = {account.id for account in accounts}
        stale_ids = [account_id for account_id in self._runtime if account_id not in account_ids]
        for account_id in stale_ids:
            self._runtime.pop(account_id, None)

    async def _select_with_stickiness(
        self,
        *,
        states: list[AccountState],
        account_map: dict[str, Account],
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        reallocate_sticky: bool,
        sticky_max_age_seconds: int | None,
        prefer_earlier_reset_accounts: bool,
        routing_strategy: RoutingStrategy,
        sticky_repo: StickySessionsRepository | None,
    ) -> SelectionResult:
        if not sticky_key or not sticky_repo:
            return select_account(
                states,
                prefer_earlier_reset=prefer_earlier_reset_accounts,
                routing_strategy=routing_strategy,
            )
        if sticky_kind is None:
            raise ValueError("sticky_kind is required when sticky_key is provided")

        existing = await sticky_repo.get_account_id(
            sticky_key,
            kind=sticky_kind,
            max_age_seconds=sticky_max_age_seconds,
        )

        # When the pinned account is temporarily unavailable (rate-limited,
        # error backoff) but still in the pool, pick a fallback WITHOUT
        # overwriting the sticky mapping so the next request returns to the
        # original account — and its warm OpenAI prompt cache — once it
        # recovers.  Only reallocate_sticky=True opts in to permanent
        # reassignment.
        persist_fallback = True

        if existing:
            pinned = next((state for state in states if state.account_id == existing), None)
            if pinned is not None:
                pinned_result = select_account(
                    [pinned],
                    prefer_earlier_reset=prefer_earlier_reset_accounts,
                    routing_strategy=routing_strategy,
                    allow_backoff_fallback=False,
                )
                if pinned_result.account is not None:
                    if not reallocate_sticky and sticky_max_age_seconds is not None:
                        await sticky_repo.upsert(sticky_key, pinned.account_id, kind=sticky_kind)
                    return pinned_result
                # Grace period: if the pinned account is rate-limited with a
                # known reset time within a short window, retry selection
                # with a small time advance to preserve prompt cache.
                # A shallow copy is used so the time-advanced selection does
                # not mutate the original state (which is later synced to DB
                # by _sync_state for all accounts).
                if not reallocate_sticky and pinned.status == AccountStatus.RATE_LIMITED:
                    grace_copy = replace(pinned)
                    grace_result = select_account(
                        [grace_copy],
                        now=time.time() + _STICKY_GRACE_PERIOD_SECONDS,
                        prefer_earlier_reset=prefer_earlier_reset_accounts,
                        routing_strategy=routing_strategy,
                        allow_backoff_fallback=False,
                    )
                    if grace_result.account is not None:
                        if sticky_max_age_seconds is not None:
                            await sticky_repo.upsert(sticky_key, pinned.account_id, kind=sticky_kind)
                        return grace_result
                if reallocate_sticky:
                    await sticky_repo.delete(sticky_key, kind=sticky_kind)
                elif pinned.status not in _RECOVERABLE_STATUSES:
                    # Permanently down (PAUSED/DEACTIVATED) — let the
                    # fallback be persisted to rebind the mapping.
                    pass
                elif sticky_max_age_seconds is not None:
                    # TTL-based kind (PROMPT_CACHE): preserve the original
                    # mapping so the next request returns to the warm-cache
                    # account once it recovers.  The TTL will naturally
                    # expire the mapping if recovery takes too long.
                    persist_fallback = False
                # else: durable kind without TTL (CODEX_SESSION) — persist
                # fallback so the session sticks to one account during
                # the outage instead of bouncing across random fallbacks.
            else:
                await sticky_repo.delete(sticky_key, kind=sticky_kind)

        chosen = select_account(
            states,
            prefer_earlier_reset=prefer_earlier_reset_accounts,
            routing_strategy=routing_strategy,
        )
        if persist_fallback and chosen.account is not None and chosen.account.account_id in account_map:
            await sticky_repo.upsert(sticky_key, chosen.account.account_id, kind=sticky_kind)
        return chosen

    async def mark_rate_limit(self, account: Account, error: UpstreamError) -> None:
        async with self._runtime_lock:
            state = self._state_for(account)
            handle_rate_limit(state, error)
            self._sync_runtime_state(account, state)
            async with self._repo_factory() as repos:
                await self._persist_state(repos.accounts, account, state)

    async def mark_quota_exceeded(self, account: Account, error: UpstreamError) -> None:
        async with self._runtime_lock:
            state = self._state_for(account)
            handle_quota_exceeded(state, error)
            self._sync_runtime_state(account, state)
            async with self._repo_factory() as repos:
                await self._persist_state(repos.accounts, account, state)

    async def mark_permanent_failure(self, account: Account, error_code: str) -> None:
        async with self._runtime_lock:
            state = self._state_for(account)
            handle_permanent_failure(state, error_code)
            self._sync_runtime_state(account, state)
            async with self._repo_factory() as repos:
                await self._persist_state(repos.accounts, account, state)

    async def record_error(self, account: Account) -> None:
        await self.record_errors(account, 1)

    async def record_errors(self, account: Account, count: int) -> None:
        """Record *count* transient errors in a single lock acquisition."""
        if count < 1:
            return
        async with self._runtime_lock:
            account_snapshot = _clone_account(account)
            state = self._state_for(account)
            state.error_count += count
            state.last_error_at = time.time()
            self._sync_runtime_state(account, state)
        async with self._repo_factory() as repos:
            await self._persist_state_if_current(repos.accounts, account_snapshot, state)

    async def record_success(self, account: Account) -> None:
        """Clear transient error state after a successful upstream request."""
        async with self._runtime_lock:
            runtime = self._runtime.get(account.id)
            if runtime and runtime.error_count > 0:
                runtime.error_count = 0
                runtime.last_error_at = None
                runtime.version += 1

    def _state_for(self, account: Account) -> AccountState:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        return AccountState(
            account_id=account.id,
            status=account.status,
            used_percent=None,
            reset_at=runtime.reset_at,
            cooldown_until=runtime.cooldown_until,
            secondary_used_percent=None,
            secondary_reset_at=None,
            last_error_at=runtime.last_error_at,
            last_selected_at=runtime.last_selected_at,
            error_count=runtime.error_count,
            deactivation_reason=account.deactivation_reason,
        )

    def _sync_runtime_state(
        self,
        account: Account,
        state: AccountState,
        *,
        selected: bool = False,
        expected_version: int | None = None,
    ) -> bool:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        if expected_version is not None and runtime.version != expected_version:
            if selected:
                runtime.last_selected_at = time.time()
                runtime.version += 1
            return False

        dirty = False
        if runtime.reset_at != state.reset_at:
            runtime.reset_at = state.reset_at
            dirty = True
        if runtime.cooldown_until != state.cooldown_until:
            runtime.cooldown_until = state.cooldown_until
            dirty = True
        if runtime.last_error_at != state.last_error_at:
            runtime.last_error_at = state.last_error_at
            dirty = True
        if runtime.error_count != state.error_count:
            runtime.error_count = state.error_count
            dirty = True
        if account.status != state.status:
            dirty = True
        if account.deactivation_reason != state.deactivation_reason:
            dirty = True
        if selected:
            runtime.last_selected_at = time.time()
            dirty = True
        if dirty:
            runtime.version += 1
        return True

    async def _persist_selection_state(
        self,
        accounts_repo: AccountsRepository,
        account_map: dict[str, Account],
        states: list[AccountState],
    ) -> set[str]:
        stale_account_ids: set[str] = set()
        for state in states:
            account = account_map.get(state.account_id)
            if account is not None:
                persisted = await self._persist_state_if_current(accounts_repo, account, state)
                if not persisted:
                    stale_account_ids.add(account.id)
        return stale_account_ids

    async def _persist_state(
        self,
        accounts_repo: AccountsRepository,
        account: Account,
        state: AccountState,
    ) -> None:
        reset_at_int = int(state.reset_at) if state.reset_at else None
        status_changed = account.status != state.status
        reason_changed = account.deactivation_reason != state.deactivation_reason
        reset_changed = account.reset_at != reset_at_int

        if status_changed or reason_changed or reset_changed:
            await accounts_repo.update_status(
                account.id,
                state.status,
                state.deactivation_reason,
                reset_at_int,
            )
            account.status = state.status
            account.deactivation_reason = state.deactivation_reason
            account.reset_at = reset_at_int

    async def _persist_state_if_current(
        self,
        accounts_repo: AccountsRepository,
        account: Account,
        state: AccountState,
    ) -> bool:
        reset_at_int = int(state.reset_at) if state.reset_at else None
        status_changed = account.status != state.status
        reason_changed = account.deactivation_reason != state.deactivation_reason
        reset_changed = account.reset_at != reset_at_int

        if status_changed or reason_changed or reset_changed:
            updated = await accounts_repo.update_status_if_current(
                account.id,
                state.status,
                state.deactivation_reason,
                reset_at_int,
                expected_status=account.status,
                expected_deactivation_reason=account.deactivation_reason,
                expected_reset_at=account.reset_at,
            )
            if updated:
                account.status = state.status
                account.deactivation_reason = state.deactivation_reason
                account.reset_at = reset_at_int
            return updated
        return True

    async def _sync_state(
        self,
        accounts_repo: AccountsRepository,
        account: Account,
        state: AccountState,
    ) -> None:
        self._sync_runtime_state(account, state)
        await self._persist_state(accounts_repo, account, state)


def _build_states(
    *,
    accounts: Iterable[Account],
    latest_primary: dict[str, UsageHistory],
    latest_secondary: dict[str, UsageHistory],
    runtime: dict[str, RuntimeState],
) -> tuple[list[AccountState], dict[str, Account]]:
    states: list[AccountState] = []
    account_map: dict[str, Account] = {}

    for account in accounts:
        state = _state_from_account(
            account=account,
            primary_entry=latest_primary.get(account.id),
            secondary_entry=latest_secondary.get(account.id),
            runtime=runtime.setdefault(account.id, RuntimeState()),
        )
        states.append(state)
        account_map[account.id] = account
    return states, account_map


def _state_from_account(
    *,
    account: Account,
    primary_entry: UsageHistory | None,
    secondary_entry: UsageHistory | None,
    runtime: RuntimeState,
) -> AccountState:
    primary_used = primary_entry.used_percent if primary_entry else None
    primary_reset = primary_entry.reset_at if primary_entry else None
    primary_window_minutes = primary_entry.window_minutes if primary_entry else None
    effective_secondary_entry = secondary_entry
    primary_row = _usage_entry_to_window_row(primary_entry) if primary_entry is not None else None
    secondary_row = _usage_entry_to_window_row(secondary_entry) if secondary_entry is not None else None
    # Weekly-only accounts may not emit a dedicated secondary row; treat the
    # weekly primary row as quota-window input for balancer decisions. When
    # both rows exist, prefer the newer weekly snapshot.
    if primary_row is not None and usage_core.should_use_weekly_primary(primary_row, secondary_row):
        effective_secondary_entry = primary_entry

    secondary_used = effective_secondary_entry.used_percent if effective_secondary_entry else None
    secondary_reset = effective_secondary_entry.reset_at if effective_secondary_entry else None

    # Use account.reset_at from DB as the authoritative source for runtime reset
    # and to survive process restarts.
    db_reset_at = float(account.reset_at) if account.reset_at else None
    effective_runtime_reset = db_reset_at or runtime.reset_at

    status, used_percent, reset_at = apply_usage_quota(
        status=account.status,
        primary_used=primary_used,
        primary_reset=primary_reset,
        primary_window_minutes=primary_window_minutes,
        runtime_reset=effective_runtime_reset,
        secondary_used=secondary_used,
        secondary_reset=secondary_reset,
    )

    return AccountState(
        account_id=account.id,
        status=status,
        used_percent=used_percent,
        reset_at=reset_at,
        cooldown_until=runtime.cooldown_until,
        secondary_used_percent=secondary_used,
        secondary_reset_at=secondary_reset,
        last_error_at=runtime.last_error_at,
        last_selected_at=runtime.last_selected_at,
        error_count=runtime.error_count,
        deactivation_reason=account.deactivation_reason,
    )


def _filter_accounts_for_model(accounts: list[Account], model: str) -> list[Account]:
    allowed_plans = get_model_registry().plan_types_for_model(model)
    if allowed_plans is None:
        return accounts
    return [a for a in accounts if a.plan_type in allowed_plans]


def _gated_limit_name_for_model(model: str | None) -> str | None:
    return get_additional_quota_key_for_model_id(model)


def _mapped_model_has_registry_entry(model: str | None) -> bool:
    if model is None:
        return False
    registry = get_model_registry()
    get_snapshot = getattr(registry, "get_snapshot", None)
    if not callable(get_snapshot):
        return False
    snapshot = get_snapshot()
    if snapshot is None:
        return False
    return model.strip().lower() in snapshot.model_plans


def _usage_entry_to_window_row(entry: UsageHistory) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )


def _clone_account(account: Account) -> Account:
    data = {column.name: getattr(account, column.name) for column in Account.__table__.columns}
    return Account(**data)


def _clone_usage_history(entry: UsageHistory) -> UsageHistory:
    data = {column.name: getattr(entry, column.name) for column in UsageHistory.__table__.columns}
    return UsageHistory(**data)


async def _latest_additional_by_key(
    additional_usage_repo,
    quota_key: str,
    window: str,
    *,
    account_ids: list[str] | None = None,
    since: datetime | None = None,
) -> dict[str, AdditionalUsageHistory]:
    resolved_quota_key = canonicalize_additional_quota_key(
        quota_key=quota_key,
        limit_name=quota_key,
    )
    if resolved_quota_key is None:
        return {}
    if hasattr(additional_usage_repo, "latest_by_quota_key"):
        return await additional_usage_repo.latest_by_quota_key(
            resolved_quota_key,
            window,
            account_ids=account_ids,
            since=since,
        )
    return await additional_usage_repo.latest_by_account(
        resolved_quota_key,
        window,
        account_ids=account_ids,
        since=since,
    )


def _additional_usage_fresh_since(now: datetime | None = None) -> datetime:
    current_time = now or utcnow()
    interval_seconds = max(get_settings().usage_refresh_interval_seconds * 2, 180)
    return current_time - timedelta(seconds=interval_seconds)


def _additional_quota_eligibility(
    *,
    account_id: str,
    latest_primary: dict[str, AdditionalUsageHistory],
    latest_secondary: dict[str, AdditionalUsageHistory],
    fresh_primary: dict[str, AdditionalUsageHistory],
    fresh_secondary: dict[str, AdditionalUsageHistory],
) -> str:
    latest_primary_entry = latest_primary.get(account_id)
    latest_secondary_entry = latest_secondary.get(account_id)
    primary_entry = fresh_primary.get(account_id)
    secondary_entry = fresh_secondary.get(account_id)

    if latest_primary_entry is None and latest_secondary_entry is None:
        return "data_unavailable"
    if latest_primary_entry is not None and primary_entry is None:
        return "data_unavailable"
    if latest_secondary_entry is not None and secondary_entry is None:
        return "data_unavailable"

    if primary_entry is not None and _additional_usage_is_exhausted(primary_entry):
        return "quota_exhausted"
    if secondary_entry is not None and _additional_usage_is_exhausted(secondary_entry):
        return "quota_exhausted"
    return "eligible"


def _additional_usage_is_exhausted(entry: AdditionalUsageHistory) -> bool:
    if entry.used_percent is None:
        return False
    if entry.reset_at is not None and int(entry.reset_at) <= int(time.time()):
        return False
    return float(entry.used_percent) >= 100.0
