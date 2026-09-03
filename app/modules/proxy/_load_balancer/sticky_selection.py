from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Collection, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Generic, Literal, Protocol, TypeVar

from app.core.balancer import (
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    ROUTING_POLICY_BURN_FIRST,
    ROUTING_POLICY_PRESERVE,
    TRAFFIC_CLASS_FOREGROUND,
    AccountState,
    ResetPreferenceWindow,
    RoutingCostsByAccount,
    RoutingStrategy,
    SelectionResult,
    TrafficClass,
    select_account,
)
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, StickySessionKind, UsageHistory
from app.db.snapshot import clone_row
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy._load_balancer.types import (
    MAX_SELECTION_ATTEMPTS,
    AccountConcurrencyCaps,
    AccountLease,
    AccountLeaseKind,
    ProbeReservation,
)
from app.modules.proxy.affinity import _CodexSessionSource
from app.modules.proxy.fair_share import (
    API_KEY_STREAM_FAIR_SHARE_ERROR_CODE,
    FairShareDecision,
    fair_share_denial_message,
)
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.sticky_repository import StickyOwnerLookup, StickySessionsRepository
from app.modules.quota_planner.logic import PlannerSettings, build_routing_costs

# Preserve the established observability surface while implementation moves to
# a private module; operators and tests filter this logger by its public owner.
logger = logging.getLogger("app.modules.proxy.load_balancer")

_STICKY_GRACE_PERIOD_SECONDS = 10.0
_STICKY_EXISTING_UNSET = object()
_RECOVERABLE_STATUSES = frozenset(
    {
        AccountStatus.ACTIVE,
        AccountStatus.REAUTH_REQUIRED,
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
    }
)
_AMBIGUOUS_CONVERSATION_OWNER_CODE = "conversation_owner_unavailable"
_AMBIGUOUS_CONVERSATION_OWNER_MESSAGE = "Conversation owner cannot be determined from the eligible account pool"

StickySelectionDisposition = Literal["shared_result", "direct_error"]
AccountCapRejectionCallback = Callable[[AccountLeaseKind | None], None]


class SelectionInputsProtocol(Protocol):
    accounts: list[Account]
    latest_primary: dict[str, UsageHistory | AdditionalUsageHistory]
    latest_secondary: dict[str, UsageHistory | AdditionalUsageHistory]
    latest_monthly: dict[str, UsageHistory]
    quota_planner_settings: PlannerSettings
    runtime_accounts: list[Account] | None
    error_message: str | None
    error_code: str | None
    ignore_standard_quota_account_ids: frozenset[str]
    routing_policy_override: str | None

    @property
    def effective_continuity_owner_candidates(self) -> list[Account]: ...

    @property
    def effective_sticky_mutation_authority_account_ids(self) -> frozenset[str]: ...


SelectionInputsT = TypeVar("SelectionInputsT", bound=SelectionInputsProtocol)


class StickySelectionOwner(Protocol):
    _runtime_lock: asyncio.Lock
    _repo_factory: ProxyRepoFactory

    def _prepare_sticky_selection_states(
        self,
        selection_inputs: SelectionInputsProtocol,
        *,
        required_account_id: str | None,
        redact_sensitive_details: bool,
    ) -> tuple[list[AccountState], dict[str, Account]]: ...

    def _sync_runtime_state(
        self,
        account: Account,
        state: AccountState,
        *,
        selected: bool = False,
        expected_version: int | None = None,
    ) -> bool: ...

    def _account_lease_allowed_locked(
        self,
        account_id: str,
        *,
        kind: AccountLeaseKind,
        caps: AccountConcurrencyCaps,
        stream_reserve_slots: int = 0,
    ) -> bool: ...

    def _acquire_account_lease_locked(
        self,
        account_id: str,
        *,
        kind: AccountLeaseKind,
        estimated_tokens: float,
        record_selection: bool = True,
        api_key_id: str | None = None,
    ) -> AccountLease: ...

    def _api_key_stream_fair_share_denial_locked(
        self,
        *,
        api_key_id: str | None,
        lease_kind: AccountLeaseKind | None,
        candidate_account_ids: Collection[str],
        caps: AccountConcurrencyCaps,
        stream_reserve_slots: int,
        threshold_pct: int,
        redact_sensitive_details: bool = False,
    ) -> FairShareDecision | None: ...

    def _reserve_due_probe_locked(
        self,
        states: list[AccountState],
        *,
        prefer_earlier_reset: bool,
        prefer_earlier_reset_window: ResetPreferenceWindow,
        routing_strategy: RoutingStrategy,
        relative_availability_power: float,
        relative_availability_top_k: int,
        traffic_class: TrafficClass,
        routing_costs_by_account_id: RoutingCostsByAccount | None,
    ) -> ProbeReservation | None: ...

    def _probe_reservation_current_locked(self, reservation: ProbeReservation | None) -> bool: ...

    def _release_due_probe_reservation_locked(self, reservation: ProbeReservation | None) -> None: ...

    def _commit_due_probe_reservation_locked(self, reservation: ProbeReservation | None) -> bool: ...

    def _sync_committed_probe_state_locked(
        self,
        reservation: ProbeReservation,
        account_map: dict[str, Account],
        states: list[AccountState],
    ) -> None: ...

    async def _persist_selection_state(
        self,
        accounts_repo: AccountsRepository,
        account_map: dict[str, Account],
        states: list[AccountState],
    ) -> set[str]: ...

    async def _select_with_stickiness(
        self,
        *,
        states: list[AccountState],
        account_map: dict[str, Account],
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        reallocate_sticky: bool,
        sticky_max_age_seconds: int | None,
        budget_threshold_pct: float,
        secondary_budget_threshold_pct: float,
        prefer_earlier_reset_accounts: bool,
        prefer_earlier_reset_window: ResetPreferenceWindow,
        routing_strategy: RoutingStrategy,
        relative_availability_power: float,
        relative_availability_top_k: int,
        sticky_repo: StickySessionsRepository | None,
        routing_costs_by_account_id: RoutingCostsByAccount | None,
        sticky_existing_account_id: str | None | object,
        initial_preferred_account_id: str | None,
        preserve_existing_mapping_on_fallback: bool,
        traffic_class: TrafficClass,
        ignore_standard_quota: bool,
        allow_usage_exhaustion_error: bool = True,
        usage_exhaustion_states: Iterable[AccountState] | None = None,
        sticky_refresh_skip_deadline: datetime | None = None,
    ) -> _StickySelectionOutcome: ...

    async def release_account_lease(self, lease: AccountLease | None) -> None: ...


@dataclass(frozen=True, slots=True)
class StickySelectionRequest(Generic[SelectionInputsT]):
    sticky_key: str
    sticky_kind: StickySessionKind | None
    reallocate_sticky: bool
    sticky_source: _CodexSessionSource | None
    legacy_sticky_key: str | None
    legacy_existing_account_id: str | None
    legacy_abandoned_account_id: str | None
    sticky_seed_key: str | None
    sticky_seed_kind: StickySessionKind | None
    sticky_seed_account_id: str | None
    spill_bare_session_on_account_cap: bool
    abandon_unavailable_legacy_owner: bool
    require_unambiguous_account: bool
    sticky_max_age_seconds: int | None
    prefer_earlier_reset_accounts: bool
    prefer_earlier_reset_window: ResetPreferenceWindow
    routing_strategy: RoutingStrategy
    relative_availability_power: float
    relative_availability_top_k: int
    required_account_id: str | None
    budget_threshold_pct: float
    secondary_budget_threshold_pct: float
    routing_costs_by_account_id: RoutingCostsByAccount | None
    lease_kind: AccountLeaseKind | None
    estimated_lease_tokens: float
    stream_reserve_slots: int
    traffic_class: TrafficClass
    concurrency_caps: AccountConcurrencyCaps
    redact_sensitive_details: bool
    selection_inputs: SelectionInputsT
    reload_inputs: Callable[[], Awaitable[SelectionInputsT]]
    record_account_cap_rejection: AccountCapRejectionCallback
    allow_usage_exhaustion_error: bool = True
    api_key_id: str | None = None
    api_key_stream_fair_share_threshold_pct: int = 0
    # First-iteration owner read performed by the caller inside its shared
    # owner-lookup session (see load_balancer.select_account). Consumed exactly
    # once; retries re-read fresh ownership evidence through a repo bundle.
    initial_sticky_owner_lookup: StickyOwnerLookup | None = None


@dataclass(frozen=True, slots=True)
class _StickyMutation:
    # ``None`` is an intentional delete; absence of a mutation means preserve
    # the current mapping until final admission succeeds.
    account_id: str | None
    # Set only when this mutation is a pure same-owner freshness rewrite of a
    # row this request's lookup observed inside the repository's refresh-skip
    # window. The persist site revalidates the deadline against the clock at
    # write time and may then omit the statement entirely; a mutation that
    # rebinds, deletes, or must initialize a seed mapping never carries it.
    refresh_skip_deadline: datetime | None = None


@dataclass(frozen=True, slots=True)
class _StickySelectionOutcome:
    selection: SelectionResult
    mutation: _StickyMutation | None = None


@dataclass(frozen=True, slots=True)
class StickySelectionOutcome(Generic[SelectionInputsT]):
    selection_inputs: SelectionInputsT
    selected_snapshot: Account | None
    selected_lease: AccountLease | None
    error_message: str | None
    error_code: str | None
    resets_at: int | None = None
    disposition: StickySelectionDisposition = "shared_result"


async def run_sticky_selection_path(
    owner: StickySelectionOwner,
    *,
    request: StickySelectionRequest[SelectionInputsT],
) -> StickySelectionOutcome[SelectionInputsT]:
    selection_inputs = request.selection_inputs
    sticky_key = request.sticky_key
    sticky_kind = request.sticky_kind
    reallocate_sticky = request.reallocate_sticky
    sticky_source = request.sticky_source
    legacy_sticky_key = request.legacy_sticky_key
    legacy_existing_account_id = request.legacy_existing_account_id
    legacy_abandoned_account_id = request.legacy_abandoned_account_id
    sticky_seed_key = request.sticky_seed_key
    sticky_seed_kind = request.sticky_seed_kind
    sticky_seed_account_id = request.sticky_seed_account_id
    spill_bare_session_on_account_cap = request.spill_bare_session_on_account_cap
    abandon_unavailable_legacy_owner = request.abandon_unavailable_legacy_owner
    require_unambiguous_account = request.require_unambiguous_account
    sticky_max_age_seconds = request.sticky_max_age_seconds
    prefer_earlier_reset_accounts = request.prefer_earlier_reset_accounts
    prefer_earlier_reset_window = request.prefer_earlier_reset_window
    routing_strategy = request.routing_strategy
    relative_availability_power = request.relative_availability_power
    relative_availability_top_k = request.relative_availability_top_k
    required_account_id = request.required_account_id
    budget_threshold_pct = request.budget_threshold_pct
    secondary_budget_threshold_pct = request.secondary_budget_threshold_pct
    routing_costs_by_account_id = request.routing_costs_by_account_id
    lease_kind = request.lease_kind
    estimated_lease_tokens = request.estimated_lease_tokens
    stream_reserve_slots = request.stream_reserve_slots
    traffic_class = request.traffic_class
    caps = request.concurrency_caps
    redact_sensitive_details = request.redact_sensitive_details
    load_selection_inputs = request.reload_inputs
    _record_account_cap_rejection = request.record_account_cap_rejection
    allow_usage_exhaustion_error = request.allow_usage_exhaustion_error
    api_key_id = request.api_key_id
    fair_share_threshold_pct = request.api_key_stream_fair_share_threshold_pct

    selected_snapshot: Account | None = None
    selected_lease: AccountLease | None = None
    error_message: str | None = None
    selection_error_code: str | None = None
    selection_resets_at: int | None = None

    def _direct_error(
        *,
        account: None,
        error_message: str | None,
        error_code: str | None = None,
    ) -> StickySelectionOutcome[SelectionInputsT]:
        assert account is None
        return StickySelectionOutcome(
            selection_inputs=selection_inputs,
            selected_snapshot=None,
            selected_lease=None,
            error_message=error_message,
            error_code=error_code,
            disposition="direct_error",
        )

    sticky_existing_account_id: str | None | object = _STICKY_EXISTING_UNSET
    sticky_continuity_abandoned = False
    sticky_refresh_skip_deadline: datetime | None = None
    # A thread row whose process seed exists but is still unowned must keep
    # its retention write: that write doubles as the seed-initialization
    # carrier (see the ``initialize_seed_key`` argument at the persist site
    # below), and suppressing it would let sibling threads select divergent
    # owners until the skip window closes. Thread-only affinity without a
    # seed key has nothing to initialize and stays skippable.
    seed_initialization_pending = (
        sticky_source == "thread_header" and sticky_seed_key is not None and sticky_seed_account_id is None
    )
    # A source-qualified marker can be observed before this call or after a
    # retirement CAS miss. In both cases its retained owner is authoritative
    # exclusion evidence even though it is no longer affinity ownership for
    # the matching session-header source.
    retired_legacy_owner_account_ids = (
        {legacy_abandoned_account_id} if legacy_abandoned_account_id is not None else set()
    )
    attempt = 0
    suppress_recovery_probe_candidates = False
    pending_initial_owner_lookup = request.initial_sticky_owner_lookup
    while True:
        attempt += 1
        sticky_existing_is_legacy = isinstance(legacy_existing_account_id, str)
        if sticky_kind is not None:
            async with owner._runtime_lock:
                pass
            if pending_initial_owner_lookup is not None:
                # The caller already read this iteration's owner inside its
                # shared owner-lookup session. Consume it exactly once so
                # every retry (including post-reset attempts that wrap
                # ``attempt`` back to 1) still re-reads fresh evidence.
                sticky_owner_lookup = pending_initial_owner_lookup
                pending_initial_owner_lookup = None
            else:
                async with owner._repo_factory() as repos:
                    sticky_owner_lookup = await repos.sticky_sessions.get_account_id_and_abandonment(
                        sticky_key,
                        kind=sticky_kind,
                        max_age_seconds=sticky_max_age_seconds,
                        continuity_source=sticky_source,
                    )
            sticky_existing_account_id = sticky_owner_lookup.account_id
            # `is True` (not a truthy check): an un-configured test double
            # for sticky_sessions may return an object whose attribute
            # access auto-vivifies to a mock rather than a real bool, and
            # that must fail safe as "not abandoned", the same as it
            # always has, rather than silently bypassing the ambiguous
            # owner check below.
            sticky_continuity_abandoned = sticky_owner_lookup.continuity_abandoned is True
            # ``isinstance`` for the same test-double reason as above. The
            # deadline is only ever an optimization hint: None always
            # falls back to today's write-on-every-request refresh
            # behavior, and seed-needing requests never skip.
            observed_refresh_skip_deadline = sticky_owner_lookup.refresh_skip_deadline
            sticky_refresh_skip_deadline = (
                observed_refresh_skip_deadline
                if isinstance(observed_refresh_skip_deadline, datetime) and not seed_initialization_pending
                else None
            )
            sticky_abandoned_account_id = sticky_owner_lookup.abandoned_account_id
            if sticky_owner_lookup.continuity_abandoned is True and isinstance(
                sticky_abandoned_account_id,
                str,
            ):
                retired_legacy_owner_account_ids.add(sticky_abandoned_account_id)
            if sticky_existing_is_legacy:
                # Mixed-version replicas can create both rows on
                # different accounts. The raw row was loaded before
                # branch selection and always wins as possible hard
                # turn-state ownership.
                sticky_existing_account_id = legacy_existing_account_id
                sticky_continuity_abandoned = False
                # The freshness observation belongs to the namespaced row,
                # not the raw legacy owner that now shadows it.
                sticky_refresh_skip_deadline = None
        async with owner._runtime_lock:
            states, account_map = owner._prepare_sticky_selection_states(
                selection_inputs,
                required_account_id=required_account_id,
                redact_sensitive_details=redact_sensitive_details,
            )
            if retired_legacy_owner_account_ids:
                # Retirement is authoritative even when this selector loaded a
                # pre-retirement account snapshot (or another replica still has
                # one cached). Never let that stale snapshot immediately repin
                # the account this request just proved durably unavailable.
                states = [state for state in states if state.account_id not in retired_legacy_owner_account_ids]
                account_map = {
                    account_id: account
                    for account_id, account in account_map.items()
                    if account_id not in retired_legacy_owner_account_ids
                }
            effective_routing_costs = (
                routing_costs_by_account_id
                if routing_costs_by_account_id is not None
                else build_routing_costs(
                    settings=selection_inputs.quota_planner_settings,
                    states=states,
                    now=datetime.now(timezone.utc),
                )
            )
            # Key shape is deliberately irrelevant here. Only typed
            # source provenance created by the affinity parser can
            # grant mobility; otherwise a crafted hard turn-state key
            # could become spillable.
            bare_session_key = (
                sticky_kind == StickySessionKind.CODEX_SESSION
                and sticky_source == "session_header"
                and legacy_sticky_key is not None
                and not sticky_existing_is_legacy
            )
            cap_spillover_allowed = spill_bare_session_on_account_cap and lease_kind is not None and bare_session_key
            hard_sticky = isinstance(sticky_existing_account_id, str) and (
                sticky_existing_is_legacy or (sticky_kind == StickySessionKind.CODEX_SESSION and not bare_session_key)
            )
            if hard_sticky and required_account_id is not None and sticky_existing_account_id != required_account_id:
                return _direct_error(
                    account=None,
                    error_message="Account-owned continuity sources conflict; retry the logical turn",
                    error_code="continuity_owner_conflict",
                )
            # A resolved hard row proves ownership. Without one, use the
            # same pre-health/pre-cap pool as the no-sticky path above.
            #
            # A tombstoned row (sticky_continuity_abandoned — see
            # purge_stale_hard_codex_session_mappings) is exempted from this
            # fail-closed check even though it's just as pool-ambiguous as a
            # never-seen key: unlike a never-seen key, we know this session's
            # owner was durably unavailable and continuity was deliberately
            # abandoned, so picking a fresh owner here is what lets the
            # request recover instead of failing closed forever with no path
            # back to a resolved hard row.
            if (
                require_unambiguous_account
                and not hard_sticky
                and not sticky_continuity_abandoned
                and len(selection_inputs.effective_continuity_owner_candidates) != 1
            ):
                return _direct_error(
                    account=None,
                    error_message=_AMBIGUOUS_CONVERSATION_OWNER_MESSAGE,
                    error_code=_AMBIGUOUS_CONVERSATION_OWNER_CODE,
                )
            # Fair share is measured against the full candidate pool, before
            # hard-sticky narrows selection to the owner account.
            fair_share_candidate_ids = [state.account_id for state in states]
            fair_share_denial = owner._api_key_stream_fair_share_denial_locked(
                api_key_id=api_key_id,
                lease_kind=lease_kind,
                candidate_account_ids=fair_share_candidate_ids,
                caps=caps,
                stream_reserve_slots=stream_reserve_slots,
                threshold_pct=fair_share_threshold_pct,
                redact_sensitive_details=redact_sensitive_details,
            )
            if hard_sticky:
                # A resolved hard Codex mapping is an ownership
                # constraint, not a preference. Scope, exclusions,
                # health, and caps may make it unavailable, but must
                # never delete or rebind it.
                selection_states = [state for state in states if state.account_id == sticky_existing_account_id]
            elif bare_session_key and isinstance(sticky_existing_account_id, str) and not cap_spillover_allowed:
                # Mobility was revoked by owner-bearing payload or
                # recovery stage. Keep the old cap exception for this
                # soft hint; the authoritative preferred-owner path
                # normally bypasses it.
                selection_states = states
            else:
                selection_states = _filter_states_for_account_caps(
                    states,
                    lease_kind=lease_kind,
                    caps=caps,
                    stream_reserve_slots=stream_reserve_slots,
                )
            if cap_spillover_allowed and lease_kind == "stream":
                # Stream selection immediately precedes response-create
                # admission. Prefer an account that can satisfy both,
                # while preserving the later create-cap error when all
                # are full.
                response_create_states = _filter_states_for_account_caps(
                    selection_states,
                    lease_kind="response_create",
                    caps=caps,
                    stream_reserve_slots=0,
                )
                selection_states = response_create_states or selection_states
            preserve_existing_mapping = (
                bare_session_key
                and isinstance(sticky_existing_account_id, str)
                and (
                    (
                        cap_spillover_allowed
                        and any(state.account_id == sticky_existing_account_id for state in states)
                        and not any(state.account_id == sticky_existing_account_id for state in selection_states)
                    )
                    or require_unambiguous_account
                )
            )
            if suppress_recovery_probe_candidates:
                selection_states = _filter_recovery_probe_candidates(
                    selection_states,
                    traffic_class=traffic_class,
                )
            probe_reservation: ProbeReservation | None = None
        # Raw sticky rows are global, while account-assigned API keys and
        # other authenticated policies narrow a request's mutation authority.
        # Keep this check on the pre-health continuity pool: quota exhaustion
        # may authorize retirement, but being outside policy scope never does.
        legacy_owner_in_effective_policy_scope = (
            isinstance(sticky_existing_account_id, str)
            and sticky_existing_account_id in selection_inputs.effective_sticky_mutation_authority_account_ids
        )
        if (
            abandon_unavailable_legacy_owner
            and hard_sticky
            and sticky_existing_is_legacy
            and sticky_source in {"session_header", "thread_header"}
            and legacy_sticky_key is not None
            and isinstance(sticky_existing_account_id, str)
            and legacy_owner_in_effective_policy_scope
        ):
            async with owner._repo_factory() as repos:
                owner_retired = await repos.sticky_sessions.abandon_legacy_session_header_owner_if_unavailable(
                    legacy_sticky_key,
                    kind=StickySessionKind.CODEX_SESSION,
                    expected_account_id=sticky_existing_account_id,
                )
                authoritative_legacy_owner = None
                if not owner_retired:
                    authoritative_legacy_owner = await repos.sticky_sessions.get_account_id_and_abandonment(
                        legacy_sticky_key,
                        kind=StickySessionKind.CODEX_SESSION,
                        continuity_source="session_header",
                    )
            # One guarded write is authoritative for this selection. Repeating
            # it in capacity-wait retries would add write pressure and could
            # reinterpret a later status transition as restart authorization.
            abandon_unavailable_legacy_owner = False
            if owner_retired:
                # The raw compatibility row is now a tombstone. Drop only the
                # selection loop's cached legacy owner and run the normal path
                # again so namespaced affinity, leases, and admission checks
                # are established through the existing selection path.
                logger.info(
                    "Legacy Codex session-header owner abandoned for self-contained goal restart account_id=%s",
                    "<redacted>" if redact_sensitive_details else sticky_existing_account_id,
                )
                retired_legacy_owner_account_ids.add(sticky_existing_account_id)
                legacy_existing_account_id = None
                continue
            # A failed compare-and-set means the cached owner is no longer
            # authoritative: it may have recovered, another request may have
            # rebound the raw row, or another worker may already have
            # tombstoned it. Re-read under a fresh transaction and restart the
            # loop so each outcome is handled by normal selection. Retaining
            # the stale owner here would defeat the CAS and can fail a restart
            # even though a concurrent operation already established a valid
            # replacement.
            assert authoritative_legacy_owner is not None
            legacy_existing_account_id = authoritative_legacy_owner.account_id
            if authoritative_legacy_owner.continuity_abandoned is True:
                abandoned_account_id = authoritative_legacy_owner.abandoned_account_id
                if isinstance(abandoned_account_id, str):
                    retired_legacy_owner_account_ids.add(abandoned_account_id)
            continue
        sticky_outcome = _StickySelectionOutcome(selection=SelectionResult(None, None))
        if fair_share_denial is not None:
            # Denial parks in the transport capacity-wait loop like a cap
            # denial. Sticky DB work, mapping mutation, and probe
            # reservation are all skipped so mappings are preserved.
            selection_error_code = API_KEY_STREAM_FAIR_SHARE_ERROR_CODE
            result = SelectionResult(None, fair_share_denial_message(fair_share_denial))
        elif hard_sticky and not selection_states:
            selection_error_code = "hard_affinity_saturated"
            selection_resets_at = None
            result = SelectionResult(None, "Hard affinity owner account is unavailable")
        elif not selection_states and states:
            selection_error_code = _account_cap_error_code(lease_kind)
            selection_resets_at = None
            result = SelectionResult(None, _account_cap_error_message(lease_kind, caps))
            logger.warning(
                "Account cap exhausted during sticky selection lease_kind=%s reason=%s candidates=%s",
                lease_kind,
                selection_error_code,
                len(states),
            )
            _record_account_cap_rejection(lease_kind)
        elif hard_sticky:
            # Hard rows are ownership evidence. Select only from the
            # resolved owner state and never enter sticky fallback code,
            # which may delete or rebind soft mappings under pressure.
            result = _select_account_preferring_budget_safe(
                selection_states,
                prefer_earlier_reset=prefer_earlier_reset_accounts,
                prefer_earlier_reset_window=prefer_earlier_reset_window,
                routing_strategy=routing_strategy,
                relative_availability_power=relative_availability_power,
                relative_availability_top_k=relative_availability_top_k,
                budget_threshold_pct=budget_threshold_pct,
                secondary_budget_threshold_pct=secondary_budget_threshold_pct,
                traffic_class=traffic_class,
                ignore_standard_quota=False,
                routing_costs_by_account_id=effective_routing_costs,
                allow_usage_exhaustion_error=allow_usage_exhaustion_error,
                usage_exhaustion_states=states,
            )
            if result.account is None:
                selection_error_code = "hard_affinity_saturated"
                selection_resets_at = None
                result = SelectionResult(
                    None,
                    result.error_message or "Hard affinity owner account is unavailable",
                )
            else:
                selection_error_code = None
                selection_resets_at = None
        else:
            selection_error_code = None
            selection_resets_at = None
            try:
                async with owner._repo_factory() as repos:
                    sticky_outcome = await owner._select_with_stickiness(
                        states=selection_states,
                        account_map=account_map,
                        sticky_key=sticky_key,
                        sticky_kind=sticky_kind,
                        reallocate_sticky=reallocate_sticky,
                        sticky_max_age_seconds=sticky_max_age_seconds,
                        budget_threshold_pct=budget_threshold_pct,
                        secondary_budget_threshold_pct=secondary_budget_threshold_pct,
                        prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                        prefer_earlier_reset_window=prefer_earlier_reset_window,
                        routing_strategy=routing_strategy,
                        relative_availability_power=relative_availability_power,
                        relative_availability_top_k=relative_availability_top_k,
                        sticky_repo=repos.sticky_sessions,
                        sticky_existing_account_id=sticky_existing_account_id,
                        initial_preferred_account_id=(
                            sticky_seed_account_id
                            if not isinstance(sticky_existing_account_id, str) and not sticky_continuity_abandoned
                            else None
                        ),
                        preserve_existing_mapping_on_fallback=preserve_existing_mapping,
                        traffic_class=traffic_class,
                        ignore_standard_quota=False,
                        routing_costs_by_account_id=effective_routing_costs,
                        allow_usage_exhaustion_error=allow_usage_exhaustion_error,
                        usage_exhaustion_states=states,
                        sticky_refresh_skip_deadline=sticky_refresh_skip_deadline,
                    )
                    result = sticky_outcome.selection
                    if (
                        result.account is None
                        and result.error_code is None
                        and lease_kind is not None
                        and len(selection_states) < len(states)
                        and any(
                            state.status in (AccountStatus.ACTIVE, AccountStatus.REAUTH_REQUIRED)
                            for state in states
                            if state not in selection_states
                        )
                    ):
                        selection_error_code = _account_cap_error_code(lease_kind)
                        result = SelectionResult(
                            None,
                            _account_cap_error_message(lease_kind, caps),
                            error_code=selection_error_code,
                        )
            except BaseException:
                async with owner._runtime_lock:
                    owner._release_due_probe_reservation_locked(probe_reservation)
                raise
        selected_account_map = account_map
        selected_states = []
        probe_reservation_invalidated = False
        reserved_probe_admitted = False
        async with owner._runtime_lock:
            selected: Account | None = None
            selection_admitted = False
            selected_reserved_probe = False
            should_reserve_probe = bool(
                result.account is not None
                and (
                    not isinstance(sticky_existing_account_id, str)
                    or result.account.account_id != sticky_existing_account_id
                    or reallocate_sticky
                )
            )
            probing_result_requires_reservation = _probing_result_requires_recovery_reservation(
                selection_states,
                result.account,
                routing_strategy=routing_strategy,
                traffic_class=traffic_class,
            )
            if should_reserve_probe and probing_result_requires_reservation:
                # Sticky persistence happens outside the runtime lock.
                # Delay the reversible reservation until after sticky
                # selection proves we are not simply retaining a
                # selectable owner; otherwise an owner-retaining request
                # can temporarily consume the only due probing slot and
                # make concurrent unbound traffic miss recovery.
                probe_reservation = owner._reserve_due_probe_locked(
                    selection_states,
                    prefer_earlier_reset=prefer_earlier_reset_accounts,
                    prefer_earlier_reset_window=prefer_earlier_reset_window,
                    routing_strategy=routing_strategy,
                    relative_availability_power=relative_availability_power,
                    relative_availability_top_k=relative_availability_top_k,
                    traffic_class=traffic_class,
                    routing_costs_by_account_id=effective_routing_costs,
                )
                if (
                    probe_reservation is not None
                    and result.account is not None
                    and probe_reservation.account_id != result.account.account_id
                ):
                    owner._release_due_probe_reservation_locked(probe_reservation)
                    probe_reservation = None
                if probe_reservation is None and result.account is not None:
                    # The result came from a pre-DB snapshot, but the
                    # current runtime no longer admits that probing
                    # candidate. Rebuild from fresh state instead of
                    # returning or persisting stale recovery affinity.
                    probe_reservation_invalidated = True
            if result.account is None:
                error_message = result.error_message
                selection_error_code = result.error_code or selection_error_code
                selection_resets_at = result.resets_at or selection_resets_at
            elif probe_reservation_invalidated:
                selected = None
            else:
                selected = account_map.get(result.account.account_id)
                selected_reserved_probe = bool(
                    selected is not None
                    and probe_reservation is not None
                    and selected.id == probe_reservation.account_id
                )
                if selected_reserved_probe and not owner._probe_reservation_current_locked(probe_reservation):
                    # A health mutation won the CAS while sticky DB work
                    # was in flight. Do not return or persist affinity to
                    # the stale probing snapshot; rebuild and select from
                    # the newer runtime state instead.
                    owner._release_due_probe_reservation_locked(probe_reservation)
                    selected = None
                    probe_reservation_invalidated = True
                elif selected is None:
                    error_message = result.error_message
                elif lease_kind is not None and not owner._account_lease_allowed_locked(
                    selected.id,
                    kind=lease_kind,
                    caps=caps,
                    stream_reserve_slots=stream_reserve_slots,
                ):
                    selection_error_code = _account_cap_error_code(lease_kind)
                    error_message = _account_cap_error_message(lease_kind, caps)
                elif (
                    lease_kind is not None
                    and (
                        fair_share_recheck := owner._api_key_stream_fair_share_denial_locked(
                            api_key_id=api_key_id,
                            lease_kind=lease_kind,
                            candidate_account_ids=fair_share_candidate_ids,
                            caps=caps,
                            stream_reserve_slots=stream_reserve_slots,
                            threshold_pct=fair_share_threshold_pct,
                            redact_sensitive_details=redact_sensitive_details,
                        )
                    )
                    is not None
                ):
                    # Sticky DB work runs between the filter-phase gate and
                    # this commit section; concurrent selections for one key
                    # could otherwise overshoot the share.
                    selection_error_code = API_KEY_STREAM_FAIR_SHARE_ERROR_CODE
                    error_message = fair_share_denial_message(fair_share_recheck)
                else:
                    selection_admitted = True
                    if lease_kind is not None:
                        selected_lease = owner._acquire_account_lease_locked(
                            selected.id,
                            kind=lease_kind,
                            estimated_tokens=estimated_lease_tokens,
                            # Keep the reservation token intact until
                            # persistence commits the recovery admission.
                            record_selection=not selected_reserved_probe,
                            api_key_id=api_key_id,
                        )

            if not probe_reservation_invalidated:
                reserved_probe_admitted = selection_admitted and selected_reserved_probe
                if not reserved_probe_admitted:
                    owner._release_due_probe_reservation_locked(probe_reservation)
                for state in states:
                    account = account_map.get(state.account_id)
                    if account is None:
                        continue
                    state_is_reserved_probe = False
                    if reserved_probe_admitted:
                        assert probe_reservation is not None
                        state_is_reserved_probe = state.account_id == probe_reservation.account_id
                    if not state_is_reserved_probe:
                        owner._sync_runtime_state(
                            account,
                            state,
                            # A selected probe remains provisional through DB
                            # persistence. Its reservation is committed below;
                            # advancing last_selected_at here would make later
                            # admission failures impossible to roll back.
                            selected=(
                                selection_admitted
                                and result.account is not None
                                and state.account_id == result.account.account_id
                            ),
                        )
                    selected_states.append(state)
                if selection_admitted and selected is not None and result.account is not None:
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

        if probe_reservation_invalidated:
            selected_snapshot = None
            error_message = None
            selected_states = []
            selected_account_map = {}
            if attempt >= MAX_SELECTION_ATTEMPTS:
                suppress_recovery_probe_candidates = True
                attempt = 0
                selection_inputs = await load_selection_inputs()
                if selection_inputs.error_code is not None and not selection_inputs.accounts:
                    return _direct_error(
                        account=None,
                        error_message=selection_inputs.error_message,
                        error_code=selection_inputs.error_code,
                    )
                await asyncio.sleep(0)
                continue
            selection_inputs = await load_selection_inputs()
            if selection_inputs.error_code is not None and not selection_inputs.accounts:
                return _direct_error(
                    account=None,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                )
            await asyncio.sleep(0)
            continue

        try:
            async with owner._repo_factory() as repos:
                stale_account_ids = await owner._persist_selection_state(
                    repos.accounts,
                    selected_account_map,
                    selected_states,
                )
        except BaseException:
            await owner.release_account_lease(selected_lease)
            selected_lease = None
            async with owner._runtime_lock:
                owner._release_due_probe_reservation_locked(probe_reservation)
            raise
        stale_account_ids = stale_account_ids or set()
        if selected_snapshot is not None and selected_snapshot.id in stale_account_ids:
            await owner.release_account_lease(selected_lease)
            selected_lease = None
            async with owner._runtime_lock:
                owner._release_due_probe_reservation_locked(probe_reservation)
            selected_snapshot = None
            error_message = None
            selected_states = []
            selected_account_map = {}
            if attempt >= MAX_SELECTION_ATTEMPTS:
                break
            selection_inputs = await load_selection_inputs()
            if selection_inputs.error_code is not None and not selection_inputs.accounts:
                return _direct_error(
                    account=None,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                )
            await asyncio.sleep(0)
            continue
        if (
            selected_snapshot is None
            and selection_error_code is not None
            and not hard_sticky
            and attempt < MAX_SELECTION_ATTEMPTS
        ):
            selection_inputs = await load_selection_inputs()
            if selection_inputs.error_code is not None and not selection_inputs.accounts:
                return _direct_error(
                    account=None,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                )
            error_message = None
            selected_states = []
            selected_account_map = {}
            await asyncio.sleep(0)
            continue
        should_persist_sticky_mutation = (
            sticky_outcome.mutation is not None
            and selection_error_code is None
            and (selected_snapshot is not None or result.account is None)
        )
        if selected_snapshot is not None and reserved_probe_admitted and should_persist_sticky_mutation:
            reservation_committed = False
            assert sticky_kind is not None
            sticky_mutation = sticky_outcome.mutation
            assert sticky_mutation is not None
            # A pure same-owner freshness rewrite may be omitted here exactly
            # as on the non-probe path below (the row already holds this
            # owner, so the rollback restores become no-ops and are skipped
            # symmetrically). The probe path deliberately never initializes a
            # seed, so only the deadline gates the skip.
            probe_refresh_write_skipped = _sticky_refresh_write_skippable(
                sticky_mutation,
                initialize_seed_key=None,
            )
            if not probe_refresh_write_skipped:
                try:
                    async with owner._repo_factory() as repos:
                        # A recovery-probe reservation is still reversible until
                        # the runtime CAS below succeeds. Persist its thread row so
                        # existing rollback machinery can restore it, but do not
                        # publish an immutable process seed that cannot be safely
                        # deleted after a concurrent sibling observes it.
                        await _persist_sticky_mutation(
                            sticky_repo=repos.sticky_sessions,
                            sticky_key=sticky_key,
                            sticky_kind=sticky_kind,
                            mutation=sticky_mutation,
                        )
                except BaseException:
                    await owner.release_account_lease(selected_lease)
                    selected_lease = None
                    async with owner._runtime_lock:
                        owner._release_due_probe_reservation_locked(probe_reservation)
                    raise
            try:
                async with owner._runtime_lock:
                    assert probe_reservation is not None
                    reservation_committed = owner._commit_due_probe_reservation_locked(probe_reservation)
                    if reservation_committed:
                        owner._sync_committed_probe_state_locked(
                            probe_reservation,
                            selected_account_map,
                            selected_states,
                        )
                    else:
                        owner._release_due_probe_reservation_locked(probe_reservation)
            except BaseException:
                await owner.release_account_lease(selected_lease)
                selected_lease = None
                async with owner._runtime_lock:
                    owner._release_due_probe_reservation_locked(probe_reservation)
                if not probe_refresh_write_skipped:
                    async with owner._repo_factory() as repos:
                        await _restore_sticky_mutation(
                            sticky_repo=repos.sticky_sessions,
                            sticky_key=sticky_key,
                            sticky_kind=sticky_kind,
                            expected_account_id=sticky_mutation.account_id,
                            sticky_existing_account_id=sticky_existing_account_id,
                        )
                raise
            if not reservation_committed:
                # Runtime health changed while account-state persistence
                # was in flight. The lease, probe quiet interval, and
                # provisional affinity must not escape; restore the
                # previous sticky owner before retrying against a fresh
                # runtime snapshot.
                await owner.release_account_lease(selected_lease)
                selected_lease = None
                if not probe_refresh_write_skipped:
                    async with owner._repo_factory() as repos:
                        await _restore_sticky_mutation(
                            sticky_repo=repos.sticky_sessions,
                            sticky_key=sticky_key,
                            sticky_kind=sticky_kind,
                            expected_account_id=sticky_mutation.account_id,
                            sticky_existing_account_id=sticky_existing_account_id,
                        )
                selected_snapshot = None
                error_message = None
                selected_states = []
                selected_account_map = {}
                if attempt >= MAX_SELECTION_ATTEMPTS:
                    suppress_recovery_probe_candidates = True
                    attempt = 0
                    selection_inputs = await load_selection_inputs()
                    if selection_inputs.error_code is not None and not selection_inputs.accounts:
                        return _direct_error(
                            account=None,
                            error_message=selection_inputs.error_message,
                            error_code=selection_inputs.error_code,
                        )
                    await asyncio.sleep(0)
                    continue
                selection_inputs = await load_selection_inputs()
                if selection_inputs.error_code is not None and not selection_inputs.accounts:
                    return _direct_error(
                        account=None,
                        error_message=selection_inputs.error_message,
                        error_code=selection_inputs.error_code,
                    )
                await asyncio.sleep(0)
                continue
        elif selected_snapshot is not None and reserved_probe_admitted:
            reservation_committed = False
            try:
                assert probe_reservation is not None
                async with owner._runtime_lock:
                    reservation_committed = owner._commit_due_probe_reservation_locked(probe_reservation)
                    if reservation_committed:
                        owner._sync_committed_probe_state_locked(
                            probe_reservation,
                            selected_account_map,
                            selected_states,
                        )
                    else:
                        owner._release_due_probe_reservation_locked(probe_reservation)
            except BaseException:
                await owner.release_account_lease(selected_lease)
                selected_lease = None
                async with owner._runtime_lock:
                    owner._release_due_probe_reservation_locked(probe_reservation)
                raise
            if not reservation_committed:
                # Runtime health changed while account-state persistence
                # was in flight. The lease and provisional affinity must
                # not escape; retry against a fresh runtime snapshot.
                await owner.release_account_lease(selected_lease)
                selected_lease = None
                selected_snapshot = None
                error_message = None
                selected_states = []
                selected_account_map = {}
                if attempt >= MAX_SELECTION_ATTEMPTS:
                    suppress_recovery_probe_candidates = True
                    attempt = 0
                    selection_inputs = await load_selection_inputs()
                    if selection_inputs.error_code is not None and not selection_inputs.accounts:
                        return _direct_error(
                            account=None,
                            error_message=selection_inputs.error_message,
                            error_code=selection_inputs.error_code,
                        )
                    await asyncio.sleep(0)
                    continue
                selection_inputs = await load_selection_inputs()
                if selection_inputs.error_code is not None and not selection_inputs.accounts:
                    return _direct_error(
                        account=None,
                        error_message=selection_inputs.error_message,
                        error_code=selection_inputs.error_code,
                    )
                await asyncio.sleep(0)
                continue
        if should_persist_sticky_mutation and not reserved_probe_admitted:
            # Sticky decisions stay provisional until cap classification,
            # final lease admission, account-state persistence, and the
            # probe CAS (when present) all succeed. Applying one final
            # desired-state mutation avoids unsafe compensating writes.
            assert sticky_kind is not None
            sticky_mutation = sticky_outcome.mutation
            assert sticky_mutation is not None
            initialize_seed_key = (
                sticky_seed_key if sticky_source == "thread_header" and sticky_seed_account_id is None else None
            )
            if not _sticky_refresh_write_skippable(sticky_mutation, initialize_seed_key=initialize_seed_key):
                try:
                    async with owner._repo_factory() as repos:
                        await _persist_sticky_mutation(
                            sticky_repo=repos.sticky_sessions,
                            sticky_key=sticky_key,
                            sticky_kind=sticky_kind,
                            mutation=sticky_mutation,
                            initialize_seed_key=initialize_seed_key,
                            initialize_seed_kind=sticky_seed_kind,
                        )
                except BaseException:
                    # Runtime admission may already be committed. Preserve
                    # its selection timestamp, but never leak the local
                    # concurrency lease when sticky persistence fails.
                    await owner.release_account_lease(selected_lease)
                    selected_lease = None
                    raise
        break

    return StickySelectionOutcome(
        selection_inputs=selection_inputs,
        selected_snapshot=selected_snapshot,
        selected_lease=selected_lease,
        error_message=error_message,
        error_code=selection_error_code,
        resets_at=selection_resets_at,
    )


async def _select_with_stickiness(
    *,
    states: list[AccountState],
    account_map: dict[str, Account],
    sticky_key: str | None,
    sticky_kind: StickySessionKind | None,
    reallocate_sticky: bool,
    sticky_max_age_seconds: int | None,
    budget_threshold_pct: float = 95.0,
    secondary_budget_threshold_pct: float = 100.0,
    prefer_earlier_reset_accounts: bool,
    prefer_earlier_reset_window: ResetPreferenceWindow,
    routing_strategy: RoutingStrategy,
    relative_availability_power: float = 2.0,
    relative_availability_top_k: int = 5,
    sticky_repo: StickySessionsRepository | None,
    routing_costs_by_account_id: RoutingCostsByAccount | None = None,
    sticky_existing_account_id: str | None | object = _STICKY_EXISTING_UNSET,
    initial_preferred_account_id: str | None = None,
    preserve_existing_mapping_on_fallback: bool = False,
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ignore_standard_quota: bool = False,
    allow_usage_exhaustion_error: bool = True,
    usage_exhaustion_states: Iterable[AccountState] | None = None,
    sticky_refresh_skip_deadline: datetime | None = None,
) -> _StickySelectionOutcome:
    if not sticky_key or not sticky_repo:
        return _StickySelectionOutcome(
            selection=_select_account_preferring_budget_safe(
                states,
                prefer_earlier_reset=prefer_earlier_reset_accounts,
                prefer_earlier_reset_window=prefer_earlier_reset_window,
                routing_strategy=routing_strategy,
                relative_availability_power=relative_availability_power,
                relative_availability_top_k=relative_availability_top_k,
                budget_threshold_pct=budget_threshold_pct,
                traffic_class=traffic_class,
                ignore_standard_quota=ignore_standard_quota,
                routing_costs_by_account_id=routing_costs_by_account_id,
                allow_usage_exhaustion_error=allow_usage_exhaustion_error,
                usage_exhaustion_states=usage_exhaustion_states,
            )
        )
    if sticky_kind is None:
        raise ValueError("sticky_kind is required when sticky_key is provided")

    pending_mutation: _StickyMutation | None = None

    def finish_selection(
        selection: SelectionResult,
        *,
        persist_account_id: str | None = None,
        refresh_skip_deadline: datetime | None = None,
    ) -> _StickySelectionOutcome:
        mutation = pending_mutation
        if persist_account_id is not None:
            mutation = _StickyMutation(
                account_id=persist_account_id,
                refresh_skip_deadline=refresh_skip_deadline,
            )
        return _StickySelectionOutcome(selection=selection, mutation=mutation)

    if sticky_existing_account_id is _STICKY_EXISTING_UNSET:
        existing = await sticky_repo.get_account_id(
            sticky_key,
            kind=sticky_kind,
            max_age_seconds=sticky_max_age_seconds,
        )
        # The skip deadline is only valid for the lookup that produced the
        # caller's ``sticky_existing_account_id``; this fresh lookup did not
        # observe row freshness, so fall back to write-through refresh.
        sticky_refresh_skip_deadline = None
    else:
        existing = sticky_existing_account_id if isinstance(sticky_existing_account_id, str) else None
    # When the pinned account is temporarily unavailable (rate-limited,
    # error backoff) but still in the pool, pick a fallback WITHOUT
    # overwriting the sticky mapping so the next request returns to the
    # original account — and its warm OpenAI prompt cache — once it
    # recovers.  Only reallocate_sticky=True opts in to permanent
    # reassignment.
    persist_fallback = not preserve_existing_mapping_on_fallback
    apply_sticky_secondary_budget_threshold = False

    if not existing and initial_preferred_account_id is not None:
        initial_preferred = next(
            (state for state in states if state.account_id == initial_preferred_account_id),
            None,
        )
        if initial_preferred is not None:
            initial_result = select_account(
                [initial_preferred],
                prefer_earlier_reset=prefer_earlier_reset_accounts,
                prefer_earlier_reset_window=prefer_earlier_reset_window,
                routing_strategy=routing_strategy,
                allow_backoff_fallback=False,
                relative_availability_power=relative_availability_power,
                relative_availability_top_k=relative_availability_top_k,
                traffic_class=traffic_class,
                ignore_standard_quota=ignore_standard_quota,
                routing_costs=routing_costs_by_account_id,
            )
            if initial_result.account is not None:
                # Persist only the new thread row. The process mapping supplied
                # the preference but is deliberately outside this mutation.
                return finish_selection(
                    initial_result,
                    persist_account_id=initial_preferred.account_id,
                )

    if existing:
        pinned = next((state for state in states if state.account_id == existing), None)
        if pinned is not None:
            # Retaining the pinned owner persists only to advance
            # ``updated_at`` on TTL-based kinds. When this request's lookup
            # already observed the row inside the repository's refresh-skip
            # window, the persist site may skip that write after revalidating
            # the observed deadline against the clock: concurrent requests on
            # a hot session otherwise serialize on the same row's upsert
            # lock. Rebinds and deletes never carry the deadline and always
            # write immediately.
            pinned_refresh_account_id = pinned.account_id if sticky_max_age_seconds is not None else None
            pinned_refresh_skip_deadline = (
                sticky_refresh_skip_deadline if pinned_refresh_account_id is not None else None
            )
            # Proactively rebind session affinity for any sticky kind
            # once the pinned account is already above the configured
            # budget threshold. That preserves continuity below the
            # threshold while avoiding obvious short-window failures once
            # the session is skating on the edge of exhaustion.
            now = time.time()
            budget_pressured = (
                sticky_kind
                in (
                    StickySessionKind.PROMPT_CACHE,
                    StickySessionKind.STICKY_THREAD,
                    StickySessionKind.CODEX_SESSION,
                )
                and routing_strategy not in ("sequential_drain", "reset_drain", "single_account")
                and pinned.status != AccountStatus.RATE_LIMITED
                and _state_above_sticky_budget_threshold(
                    pinned,
                    budget_threshold_pct,
                    secondary_budget_threshold_pct,
                )
            )
            rate_limit_far_away = (
                sticky_kind == StickySessionKind.PROMPT_CACHE
                and pinned.status == AccountStatus.RATE_LIMITED
                and pinned.reset_at is not None
                and pinned.reset_at - now >= 600  # 10 minutes
            )

            burn_first_reallocate = pinned.routing_policy != ROUTING_POLICY_BURN_FIRST
            if burn_first_reallocate:
                burn_first_candidates = [state for state in states if state.routing_policy == ROUTING_POLICY_BURN_FIRST]
                if burn_first_candidates:
                    burn_first = select_account(
                        burn_first_candidates,
                        prefer_earlier_reset=prefer_earlier_reset_accounts,
                        routing_strategy=routing_strategy,
                        allow_backoff_fallback=False,
                        deterministic_probe=True,
                        relative_availability_power=relative_availability_power,
                        relative_availability_top_k=relative_availability_top_k,
                        traffic_class=traffic_class,
                        ignore_standard_quota=ignore_standard_quota,
                    )
                    burn_first_reallocate = burn_first.account is not None

            if not ((budget_pressured or rate_limit_far_away) and burn_first_reallocate):
                pinned_result = select_account(
                    [pinned],
                    prefer_earlier_reset=prefer_earlier_reset_accounts,
                    prefer_earlier_reset_window=prefer_earlier_reset_window,
                    routing_strategy=routing_strategy,
                    allow_backoff_fallback=False,
                    relative_availability_power=relative_availability_power,
                    relative_availability_top_k=relative_availability_top_k,
                    traffic_class=traffic_class,
                    ignore_standard_quota=ignore_standard_quota,
                    routing_costs=routing_costs_by_account_id,
                )
                if pinned_result.account is not None:
                    return finish_selection(
                        pinned_result,
                        persist_account_id=pinned_refresh_account_id,
                        refresh_skip_deadline=pinned_refresh_skip_deadline,
                    )
            else:
                # Reallocate only when a burn-first target exists and can
                # currently be selected, avoiding sticky churn to
                # ineligible targets.
                # Before reallocating, check whether the pool has a
                # meaningfully better candidate.  When every account
                # is above the budget threshold, reallocating just
                # wastes DB writes and destroys prompt-cache locality
                # (thrashing).
                if budget_pressured:
                    apply_sticky_secondary_budget_threshold = True
                    pool_best = _select_account_preferring_budget_safe(
                        states,
                        prefer_earlier_reset=prefer_earlier_reset_accounts,
                        prefer_earlier_reset_window=prefer_earlier_reset_window,
                        routing_strategy=routing_strategy,
                        relative_availability_power=relative_availability_power,
                        relative_availability_top_k=relative_availability_top_k,
                        deterministic_probe=True,
                        budget_threshold_pct=budget_threshold_pct,
                        secondary_budget_threshold_pct=secondary_budget_threshold_pct,
                        apply_secondary_budget_threshold=True,
                        traffic_class=traffic_class,
                        ignore_standard_quota=ignore_standard_quota,
                        routing_costs_by_account_id=routing_costs_by_account_id,
                        allow_usage_exhaustion_error=allow_usage_exhaustion_error,
                        usage_exhaustion_states=usage_exhaustion_states,
                    )
                    pool_also_exhausted = pool_best.account is not None and (
                        pool_best.account.account_id == pinned.account_id
                        or _state_above_sticky_budget_threshold(
                            pool_best.account,
                            budget_threshold_pct,
                            secondary_budget_threshold_pct,
                        )
                    )
                    if pool_also_exhausted:
                        pinned_result = select_account(
                            [pinned],
                            prefer_earlier_reset=prefer_earlier_reset_accounts,
                            prefer_earlier_reset_window=prefer_earlier_reset_window,
                            routing_strategy=routing_strategy,
                            allow_backoff_fallback=False,
                            relative_availability_power=relative_availability_power,
                            relative_availability_top_k=relative_availability_top_k,
                            traffic_class=traffic_class,
                            ignore_standard_quota=ignore_standard_quota,
                            routing_costs=routing_costs_by_account_id,
                        )
                        if pinned_result.account is not None:
                            return finish_selection(
                                pinned_result,
                                persist_account_id=pinned_refresh_account_id,
                                refresh_skip_deadline=pinned_refresh_skip_deadline,
                            )
                reallocate_sticky = True
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
                    prefer_earlier_reset_window=prefer_earlier_reset_window,
                    routing_strategy=routing_strategy,
                    allow_backoff_fallback=False,
                    relative_availability_power=relative_availability_power,
                    relative_availability_top_k=relative_availability_top_k,
                    traffic_class=traffic_class,
                    ignore_standard_quota=ignore_standard_quota,
                    routing_costs=routing_costs_by_account_id,
                )
                if grace_result.account is not None:
                    return finish_selection(
                        grace_result,
                        persist_account_id=pinned_refresh_account_id,
                        refresh_skip_deadline=pinned_refresh_skip_deadline,
                    )
            if reallocate_sticky:
                pending_mutation = _StickyMutation(account_id=None)
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
            if not preserve_existing_mapping_on_fallback:
                pending_mutation = _StickyMutation(account_id=None)

    chosen = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=prefer_earlier_reset_accounts,
        prefer_earlier_reset_window=prefer_earlier_reset_window,
        routing_strategy=routing_strategy,
        relative_availability_power=relative_availability_power,
        relative_availability_top_k=relative_availability_top_k,
        budget_threshold_pct=budget_threshold_pct,
        secondary_budget_threshold_pct=secondary_budget_threshold_pct,
        apply_secondary_budget_threshold=apply_sticky_secondary_budget_threshold,
        traffic_class=traffic_class,
        ignore_standard_quota=ignore_standard_quota,
        routing_costs_by_account_id=routing_costs_by_account_id,
        allow_usage_exhaustion_error=allow_usage_exhaustion_error,
        usage_exhaustion_states=usage_exhaustion_states,
    )
    if persist_fallback and chosen.account is not None and chosen.account.account_id in account_map:
        return finish_selection(chosen, persist_account_id=chosen.account.account_id)
    if preserve_existing_mapping_on_fallback and chosen.account is not None and existing is not None:
        # Spillover is deliberately request-local. The alternate may create
        # its own hard response/file/bridge owner, but local cap pressure
        # alone never turns this soft mapping into a distributed commit.
        logger.info(
            "internal_soft_affinity_spillover old_account_id=%s new_account_id=%s sticky_kind=%s",
            existing,
            chosen.account.account_id,
            sticky_kind.value,
        )
    return finish_selection(chosen)


def _sticky_refresh_write_skippable(
    mutation: _StickyMutation,
    *,
    initialize_seed_key: str | None,
) -> bool:
    """Whether this mutation's write may be omitted at persist time.

    True only for a pure same-owner freshness rewrite whose observed skip
    deadline still holds now, at the moment the statement would otherwise be
    issued — admission and account-state persistence sit between selection
    and this point, so the deadline computed at lookup time must be
    revalidated to keep the mapping's effective expiry within the documented
    skip-window bound. Deletes and seed-initializing writes are never
    skippable.
    """
    if mutation.account_id is None or initialize_seed_key is not None:
        return False
    deadline = mutation.refresh_skip_deadline
    return isinstance(deadline, datetime) and utcnow() <= deadline


async def _persist_sticky_mutation(
    *,
    sticky_repo: StickySessionsRepository,
    sticky_key: str,
    sticky_kind: StickySessionKind,
    mutation: _StickyMutation,
    initialize_seed_key: str | None = None,
    initialize_seed_kind: StickySessionKind | None = None,
) -> None:
    if mutation.account_id is None:
        await sticky_repo.delete(sticky_key, kind=sticky_kind)
        return
    if initialize_seed_key is not None:
        if initialize_seed_kind is None:
            raise ValueError("initialize_seed_kind is required when initialize_seed_key is provided")
        # Current Codex sends thread-id on the first root request, so a fresh
        # process has no older bare-session request available to create its
        # default. Initialize it exactly once from the first admitted thread.
        # insert-if-absent is essential: failover or a later child may move its
        # own bounded row but can never rewrite the process/sibling default.
        # The repository operation is intentionally atomic; splitting it into
        # the public insert/upsert methods would commit a process default even
        # when persistence of the initiating thread fails.
        await sticky_repo.upsert_with_seed_if_absent(
            sticky_key,
            mutation.account_id,
            kind=sticky_kind,
            seed_key=initialize_seed_key,
            seed_kind=initialize_seed_kind,
        )
        return
    await sticky_repo.upsert(sticky_key, mutation.account_id, kind=sticky_kind)


async def _restore_sticky_mutation(
    *,
    sticky_repo: StickySessionsRepository,
    sticky_key: str,
    sticky_kind: StickySessionKind,
    expected_account_id: str | None,
    sticky_existing_account_id: str | None | object,
) -> None:
    if sticky_existing_account_id is _STICKY_EXISTING_UNSET:
        return
    await sticky_repo.restore_if_current(
        sticky_key,
        kind=sticky_kind,
        expected_account_id=expected_account_id,
        restore_account_id=sticky_existing_account_id if isinstance(sticky_existing_account_id, str) else None,
    )


def _filter_states_for_account_caps(
    states: Iterable[AccountState],
    *,
    lease_kind: AccountLeaseKind | None,
    caps: AccountConcurrencyCaps,
    stream_reserve_slots: int = 0,
) -> list[AccountState]:
    if lease_kind is None:
        return list(states)
    filtered: list[AccountState] = []
    for state in states:
        if lease_kind == "response_create":
            cap = caps.response_create_limit
            if cap > 0 and state.inflight_response_creates >= cap:
                continue
        else:
            cap = caps.stream_limit
            effective_cap = max(1, cap - max(0, stream_reserve_slots))
            if cap > 0 and state.inflight_streams >= effective_cap:
                continue
        filtered.append(state)
    return filtered


def _probing_result_requires_recovery_reservation(
    states: Collection[AccountState],
    result_account: AccountState | None,
    *,
    routing_strategy: str,
    traffic_class: TrafficClass,
) -> bool:
    if routing_strategy in ("sequential_drain", "reset_drain", "single_account"):
        return False
    if result_account is None or result_account.health_tier != HEALTH_TIER_PROBING:
        return False
    return _pool_has_available_healthy_account_without_backoff(states, traffic_class=traffic_class)


def _filter_recovery_probe_candidates(
    states: list[AccountState],
    *,
    traffic_class: TrafficClass,
) -> list[AccountState]:
    if not _pool_has_available_healthy_account_without_backoff(states, traffic_class=traffic_class):
        return states
    return [state for state in states if state.health_tier != HEALTH_TIER_PROBING]


def _pool_has_available_healthy_account_without_backoff(
    states: Iterable[AccountState],
    *,
    traffic_class: TrafficClass,
) -> bool:
    return _pool_has_available_account_without_backoff(
        (state for state in states if state.health_tier == HEALTH_TIER_HEALTHY),
        traffic_class=traffic_class,
    )


def _pool_has_available_account_without_backoff(
    states: Iterable[AccountState],
    *,
    traffic_class: TrafficClass,
) -> bool:
    """Return whether the complete pool passes non-cap routing eligibility."""
    # ``select_account`` normalizes expired quota/cooldown fields in place;
    # classify on copies so cap-error reporting cannot mutate the real
    # selection snapshot before sticky persistence. Keep the pool intact:
    # opportunistic admission compares candidates with one another.
    result = select_account(
        [replace(state) for state in states],
        now=time.time(),
        routing_strategy="single_account",
        allow_backoff_fallback=False,
        traffic_class=traffic_class,
    )
    return result.account is not None


def _account_cap_error_code(lease_kind: AccountLeaseKind | None) -> str | None:
    if lease_kind == "response_create":
        return "account_response_create_cap"
    if lease_kind == "stream":
        return "account_stream_cap"
    return None


def _account_cap_error_message(lease_kind: AccountLeaseKind | None, caps: AccountConcurrencyCaps) -> str:
    if lease_kind == "response_create":
        cap = caps.response_create_limit
        if caps.replica_count > 1 and caps.configured_response_create_limit is not None:
            return (
                f"Account response-create capacity is exhausted; this replica's share is {cap} "
                f"of the per-account limit {caps.configured_response_create_limit} "
                f"across {caps.replica_count} replicas"
            )
        return f"Account response-create capacity is exhausted; per-account limit is {cap}"
    if lease_kind == "stream":
        cap = caps.stream_limit
        if caps.replica_count > 1 and caps.configured_stream_limit is not None:
            return (
                f"Account stream capacity is exhausted; this replica's share is {cap} "
                f"of the per-account limit {caps.configured_stream_limit} "
                f"across {caps.replica_count} replicas. "
                "Increase the dashboard stream limit or wait for active streams to finish."
            )
        return (
            f"Account stream capacity is exhausted; per-account limit is {cap}. "
            "Increase the dashboard stream limit or wait for active streams to finish."
        )
    return "Account capacity is exhausted"


def _state_above_budget_threshold(state: AccountState, budget_threshold_pct: float) -> bool:
    used_percent = state.priority_used_percent if state.priority_used_percent is not None else state.used_percent
    return used_percent is not None and used_percent > budget_threshold_pct


def _state_above_sticky_budget_threshold(
    state: AccountState,
    budget_threshold_pct: float,
    secondary_budget_threshold_pct: float | None = None,
) -> bool:
    secondary_threshold = (
        budget_threshold_pct if secondary_budget_threshold_pct is None else secondary_budget_threshold_pct
    )
    used_percent = state.priority_used_percent if state.priority_used_percent is not None else state.used_percent
    if state.limit_scoped_usage and state.priority_secondary_used_percent is None:
        secondary_used_percent = used_percent
    else:
        secondary_used_percent = (
            state.priority_secondary_used_percent
            if state.priority_secondary_used_percent is not None
            else state.secondary_used_percent
        )
    return (used_percent is not None and used_percent > budget_threshold_pct) or (
        secondary_used_percent is not None and secondary_used_percent > secondary_threshold
    )


def _select_account_preferring_budget_safe(
    states: Iterable[AccountState],
    *,
    prefer_earlier_reset: bool,
    prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
    routing_strategy: RoutingStrategy,
    relative_availability_power: float = 2.0,
    relative_availability_top_k: int = 5,
    budget_threshold_pct: float,
    secondary_budget_threshold_pct: float = 100.0,
    apply_secondary_budget_threshold: bool = False,
    allow_backoff_fallback: bool = True,
    deterministic_probe: bool = False,
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ignore_standard_quota: bool = False,
    routing_costs_by_account_id: RoutingCostsByAccount | None = None,
    allow_usage_exhaustion_error: bool = True,
    usage_exhaustion_states: Iterable[AccountState] | None = None,
) -> SelectionResult:
    state_list = list(states)
    if routing_strategy not in ("sequential_drain", "reset_drain", "single_account"):
        # This pass must precede budget-safe and routing-policy shortcuts below;
        # otherwise a healthy preferred account can starve PROBING indefinitely.
        recovery_probe = select_account(
            state_list,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            recovery_probe_only=True,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
            allow_usage_exhaustion_error=False,
        )
        if recovery_probe.account is not None:
            return recovery_probe
    state_budget_threshold = (
        (
            lambda state: _state_above_sticky_budget_threshold(
                state,
                budget_threshold_pct,
                secondary_budget_threshold_pct,
            )
        )
        if apply_secondary_budget_threshold
        else (lambda state: _state_above_budget_threshold(state, budget_threshold_pct))
    )
    if routing_strategy in ("sequential_drain", "reset_drain", "single_account"):
        budget_safe_states = [
            state
            for state in state_list
            if state.routing_policy != ROUTING_POLICY_PRESERVE and not state_budget_threshold(state)
        ]
        return select_account(
            budget_safe_states or state_list,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
            allow_usage_exhaustion_error=allow_usage_exhaustion_error,
            usage_exhaustion_states=usage_exhaustion_states,
        )

    best_health_states = _best_health_tier_states(state_list)
    burn_first_states = [state for state in best_health_states if state.routing_policy == ROUTING_POLICY_BURN_FIRST]
    if burn_first_states:
        burn_first = select_account(
            burn_first_states,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=False,
            deterministic_probe=deterministic_probe,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
            allow_usage_exhaustion_error=allow_usage_exhaustion_error,
            usage_exhaustion_states=usage_exhaustion_states,
        )
        if burn_first.account is not None:
            return burn_first

    preferred_states = [
        state
        for state in state_list
        if state.routing_policy != ROUTING_POLICY_PRESERVE and not state_budget_threshold(state)
    ]
    if preferred_states:
        selection_pool = preferred_states if len(preferred_states) != len(state_list) else state_list
        preferred = select_account(
            selection_pool,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
            allow_usage_exhaustion_error=allow_usage_exhaustion_error,
            usage_exhaustion_states=usage_exhaustion_states,
        )
        if preferred.account is not None:
            return preferred
        if len(preferred_states) == len(state_list):
            return preferred
    if routing_strategy == "usage_weighted" and state_list:
        return select_account(
            state_list,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            usage_weighted_order="primary_first",
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
            allow_usage_exhaustion_error=allow_usage_exhaustion_error,
            usage_exhaustion_states=usage_exhaustion_states,
        )
    return select_account(
        state_list,
        prefer_earlier_reset=prefer_earlier_reset,
        prefer_earlier_reset_window=prefer_earlier_reset_window,
        routing_strategy=routing_strategy,
        allow_backoff_fallback=allow_backoff_fallback,
        deterministic_probe=deterministic_probe,
        relative_availability_power=relative_availability_power,
        relative_availability_top_k=relative_availability_top_k,
        traffic_class=traffic_class,
        ignore_standard_quota=ignore_standard_quota,
        routing_costs=routing_costs_by_account_id,
        allow_usage_exhaustion_error=allow_usage_exhaustion_error,
        usage_exhaustion_states=usage_exhaustion_states,
    )


def _best_health_tier_states(states: list[AccountState]) -> list[AccountState]:
    healthy = [state for state in states if state.health_tier == HEALTH_TIER_HEALTHY]
    if healthy:
        return healthy
    probing = [state for state in states if state.health_tier == HEALTH_TIER_PROBING]
    if probing:
        return probing
    draining = [state for state in states if state.health_tier == HEALTH_TIER_DRAINING]
    return draining or states


def _clone_account(account: Account) -> Account:
    return clone_row(account)
