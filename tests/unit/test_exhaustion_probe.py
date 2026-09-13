"""Read-only pool-exhaustion probe (#2123 WP-C1 P4).

The probe is the spec's structured 429 predicate asked of the real selector.
These tests pin three things: the probe contract against a fake admission
service (what it forwards, what it reports, what it never asks for), the
account-scope rule hoisted out of ``ProxyService``, and the parity property
the probe rests on — over arbitrary account pools the deterministic
opportunistic selector it rides on answers ``usage_limit_reached`` exactly when
foreground selection does, with the same ``resets_at``.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from types import SimpleNamespace
from typing import cast
from unittest import mock
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.balancer import (
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    ROUTING_POLICY_BURN_FIRST,
    ROUTING_POLICY_PRESERVE,
    TRAFFIC_CLASS_FOREGROUND,
    TRAFFIC_CLASS_OPPORTUNISTIC,
    USAGE_LIMIT_REACHED,
    AccountState,
    ResetPreferenceWindow,
    RoutingStrategy,
    TrafficClass,
)
from app.core.balancer.logic import ROUTING_POLICY_NORMAL
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._load_balancer import exhaustion_probe as exhaustion_probe_module
from app.modules.proxy._load_balancer.exhaustion_probe import (
    DRAIN_ROUTING_STRATEGIES,
    PROBE_DECLINED_DRAIN_STRATEGY,
    AdmissionProbeService,
    PoolExhaustion,
    probe_pool_usage_exhaustion,
)
from app.modules.proxy._load_balancer.opportunistic_admission import detached_runtime_snapshot
from app.modules.proxy._load_balancer.types import AccountLease, AccountLeaseKind, RuntimeState
from app.modules.proxy._service.support import opportunistic_admission_account_scope
from app.modules.proxy.load_balancer import AccountSelection, _select_account_preferring_budget_safe

pytestmark = pytest.mark.unit


def _api_key(**overrides: object) -> ApiKeyData:
    fields: dict[str, object] = {
        "id": "key_probe",
        "name": "probe",
        "key_prefix": "sk-probe",
        "allowed_models": None,
        "enforced_model": None,
        "enforced_reasoning_effort": None,
        "enforced_service_tier": None,
        "expires_at": None,
        "is_active": True,
        "created_at": utcnow(),
        "last_used_at": None,
    }
    fields.update(overrides)
    return ApiKeyData(**cast(dict, fields))


def _service(selection: AccountSelection) -> tuple[AdmissionProbeService, AsyncMock]:
    admission = AsyncMock(return_value=selection)
    return cast(AdmissionProbeService, SimpleNamespace(check_opportunistic_admission=admission)), admission


# The settings snapshot the routing stage hands the probe: any strategy outside the drain family.
_NON_DRAIN_SETTINGS = SimpleNamespace(routing_strategy="capacity_weighted", single_account_id=None)


# --- probe contract -----------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_forwards_the_request_shape_as_an_observation_without_a_lease() -> None:
    api_key = _api_key()
    exhausted = AccountSelection(
        account=None,
        error_message="Rate limit exceeded. Try again in 30m",
        error_code=USAGE_LIMIT_REACHED,
        resets_at=1_700_001_800,
    )
    service, admission = _service(exhausted)

    exhaustion = await probe_pool_usage_exhaustion(
        service, settings=_NON_DRAIN_SETTINGS, api_key=api_key, model="gpt-5.4", service_tier="priority"
    )

    admission.assert_awaited_once_with(
        api_key=api_key, model="gpt-5.4", service_tier="priority", lease_kind=None, observe_only=True
    )
    assert exhaustion == PoolExhaustion(resets_at=1_700_001_800, selection=exhausted)
    assert exhaustion.selection is exhausted, "the caller rebuilds today's 429 from the selector's own answer"


@pytest.mark.asyncio
async def test_probe_keeps_a_missing_reset_timestamp() -> None:
    service, _ = _service(
        AccountSelection(account=None, error_message="Usage limit reached", error_code=USAGE_LIMIT_REACHED)
    )

    exhaustion = await probe_pool_usage_exhaustion(
        service, settings=_NON_DRAIN_SETTINGS, api_key=None, model=None, service_tier=None
    )

    assert exhaustion is not None
    assert exhaustion.resets_at is None


@pytest.mark.parametrize(
    "selection",
    [
        pytest.param(
            AccountSelection(account=cast(Account, object()), error_message=None, error_code=None),
            id="account_admitted",
        ),
        pytest.param(
            AccountSelection(
                account=None,
                error_message="opportunistic burn window closed: preserve floor blocks opportunistic burn",
                error_code="opportunistic_burn_window_closed",
            ),
            id="burn_window_closed",
        ),
        pytest.param(
            AccountSelection(account=None, error_message="No active accounts available", error_code="no_accounts"),
            id="no_accounts",
        ),
        pytest.param(
            AccountSelection(account=None, error_message="No active accounts available", error_code=None),
            id="untyped_failure",
        ),
        pytest.param(
            AccountSelection(
                account=None,
                error_message="No accounts with a plan supporting model 'gpt-5.4'",
                error_code="no_plan_support_for_model",
            ),
            id="plan_gate",
        ),
        pytest.param(
            AccountSelection(account=None, error_message="quota exhausted", error_code="quota_exhausted"),
            id="additional_quota_gate",
        ),
        pytest.param(
            AccountSelection(
                account=None,
                error_message="Account stream concurrency limit reached",
                error_code="account_stream_cap",
                resets_at=1_700_000_000,
            ),
            id="local_capacity_code_even_with_a_reset",
        ),
    ],
)
@pytest.mark.asyncio
async def test_probe_reports_not_exhausted_for_every_other_selection_answer(selection: AccountSelection) -> None:
    service, admission = _service(selection)

    assert (
        await probe_pool_usage_exhaustion(
            service, settings=_NON_DRAIN_SETTINGS, api_key=_api_key(), model="gpt-5.4", service_tier=None
        )
        is None
    )
    admission.assert_awaited_once()
    assert admission.await_args is not None
    assert admission.await_args.kwargs["lease_kind"] is None
    assert admission.await_args.kwargs["observe_only"] is True


# --- detached runtime snapshot --------------------------------------------------


def _lease(
    lease_id: str, *, kind: AccountLeaseKind, acquired_at: float, tokens: float = 0.0, api_key_id: str | None = None
) -> AccountLease:
    return AccountLease(
        lease_id=lease_id,
        account_id="acc_leased",
        kind=kind,
        acquired_at=acquired_at,
        estimated_tokens=tokens,
        api_key_id=api_key_id,
    )


def test_detached_runtime_snapshot_expires_stale_leases_on_the_copy_only() -> None:
    fresh = _lease("fresh", kind="stream", acquired_at=990.0, tokens=100.0, api_key_id="key_a")
    stale_stream = _lease("stale-stream", kind="stream", acquired_at=10.0, tokens=250.0, api_key_id="key_a")
    stale_create = _lease("stale-create", kind="response_create", acquired_at=20.0, tokens=50.0)
    live = RuntimeState(
        inflight_streams=2,
        inflight_response_creates=1,
        leased_tokens=400.0,
        leases={lease.lease_id: lease for lease in (fresh, stale_stream, stale_create)},
        stream_key_inflight={"key_a": 2},
        overload_rejections=[1.0, 2.0],
        outcome_buckets={7: [3, 1]},
        health_tier=1,
        version=4,
    )
    runtime = {"acc_leased": live, "acc_idle": RuntimeState()}
    before = deepcopy(runtime)

    snapshot = detached_runtime_snapshot(
        runtime, now=1000.0, stale_lease_ttl_seconds=lambda kind: 900.0 if kind == "stream" else 300.0
    )

    # Live entries are byte-identical and share no container with the copy.
    assert runtime == before
    copy = snapshot["acc_leased"]
    assert copy is not live
    assert copy.leases is not live.leases
    assert copy.stream_key_inflight is not live.stream_key_inflight
    assert copy.overload_rejections is not live.overload_rejections
    assert copy.outcome_buckets is not live.outcome_buckets
    assert copy.outcome_buckets is not None and copy.outcome_buckets[7] is not (live.outcome_buckets or {})[7]
    # The copy carries exactly the live reclaim's bookkeeping for the two stale leases.
    assert copy.leases == {"fresh": fresh}
    assert copy.inflight_streams == 1
    assert copy.inflight_response_creates == 0
    assert copy.leased_tokens == 100.0
    assert copy.stream_key_inflight == {"key_a": 1}
    assert copy.version == 6
    assert copy.health_tier == 1
    assert snapshot["acc_idle"] == RuntimeState()
    assert snapshot["acc_idle"] is not runtime["acc_idle"]


def test_detached_runtime_snapshot_keeps_fresh_leases_and_never_goes_negative() -> None:
    fresh = _lease("fresh", kind="stream", acquired_at=999.0, tokens=10.0)
    drifted = RuntimeState(
        inflight_streams=0,
        leased_tokens=0.0,
        leases={"stale": _lease("stale", kind="stream", acquired_at=0.0, tokens=5.0)},
    )
    runtime = {
        "acc_fresh": RuntimeState(inflight_streams=1, leased_tokens=10.0, leases={"fresh": fresh}),
        "acc_drifted": drifted,
    }
    # An independent baseline: comparing the live leases against themselves would pass an in-place mutation.
    live_drifted_before = deepcopy(drifted)

    snapshot = detached_runtime_snapshot(runtime, now=1000.0, stale_lease_ttl_seconds=lambda _kind: 900.0)

    assert snapshot["acc_fresh"].leases == {"fresh": fresh}
    assert snapshot["acc_fresh"].inflight_streams == 1
    assert snapshot["acc_drifted"].leases == {}
    assert snapshot["acc_drifted"].inflight_streams == 0
    assert snapshot["acc_drifted"].leased_tokens == 0.0
    assert runtime["acc_drifted"] is drifted
    assert runtime["acc_drifted"] == live_drifted_before
    assert live_drifted_before.leases is not None and set(live_drifted_before.leases) == {"stale"}
    assert runtime["acc_drifted"].leases == live_drifted_before.leases


# --- account scope ------------------------------------------------------------


def _settings(routing_strategy: str | None, single_account_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(routing_strategy=routing_strategy, single_account_id=single_account_id)


_SCOPED_KEY = _api_key(account_assignment_scope_enabled=True, assigned_account_ids=["acc_a", "acc_b"])
_UNSCOPED_KEY = _api_key(account_assignment_scope_enabled=False, assigned_account_ids=["acc_ignored"])


@pytest.mark.parametrize(
    ("settings_snapshot", "api_key", "expected"),
    [
        pytest.param(_settings("capacity_weighted"), None, None, id="whole_pool_without_key"),
        pytest.param(_settings("usage_weighted"), _UNSCOPED_KEY, None, id="unscoped_key_sees_whole_pool"),
        pytest.param(_settings("capacity_weighted"), _SCOPED_KEY, {"acc_a", "acc_b"}, id="key_scope_applies"),
        pytest.param(_settings("single_account", "acc_a"), None, {"acc_a"}, id="single_account_without_key"),
        pytest.param(_settings("single_account", " acc_a "), None, {"acc_a"}, id="single_account_is_stripped"),
        pytest.param(_settings("single_account", "acc_a"), _SCOPED_KEY, {"acc_a"}, id="single_account_inside_scope"),
        pytest.param(_settings("single_account", "acc_z"), _SCOPED_KEY, set(), id="single_account_outside_scope"),
        pytest.param(_settings("single_account", None), None, set(), id="single_account_unset"),
        pytest.param(_settings("single_account", "   "), _SCOPED_KEY, set(), id="single_account_blank"),
        pytest.param(SimpleNamespace(), _SCOPED_KEY, {"acc_a", "acc_b"}, id="stale_snapshot_without_strategy"),
    ],
)
def test_opportunistic_admission_account_scope(
    settings_snapshot: SimpleNamespace, api_key: ApiKeyData | None, expected: set[str] | None
) -> None:
    assert opportunistic_admission_account_scope(settings_snapshot, api_key) == expected


def test_opportunistic_admission_account_scope_copies_the_key_scope() -> None:
    api_key = _api_key(account_assignment_scope_enabled=True, assigned_account_ids=["acc_a"])

    scope = opportunistic_admission_account_scope(_settings("capacity_weighted"), api_key)

    assert scope == {"acc_a"}
    assert scope is not api_key.assigned_account_ids


# --- parity property ----------------------------------------------------------

_NOW = 1_700_000_000.0
_RESET_WINDOWS: tuple[ResetPreferenceWindow, ...] = ("primary", "secondary")
_ROUTING_STRATEGIES: tuple[RoutingStrategy, ...] = (
    "capacity_weighted",
    "sequential_drain",
    "reset_drain",
    "single_account",
    "relative_availability",
    "fill_first",
    "round_robin",
    "usage_weighted",
)


def _epoch_offsets() -> st.SearchStrategy[float | None]:
    # Never exactly ``now``: the property compares two evaluations at one instant.
    return st.one_of(
        st.none(),
        st.integers(min_value=-7 * 86_400, max_value=-1).map(lambda seconds: _NOW + seconds),
        st.integers(min_value=1, max_value=7 * 86_400).map(lambda seconds: _NOW + seconds),
    )


def _epoch_ints() -> st.SearchStrategy[int | None]:
    return _epoch_offsets().map(lambda value: None if value is None else int(value))


def _used_percents() -> st.SearchStrategy[float | None]:
    return st.one_of(
        st.none(),
        st.floats(min_value=0.0, max_value=99.99),
        st.just(100.0),
        st.floats(min_value=100.0, max_value=180.0),
    )


@st.composite
def _account_states(draw: st.DrawFn, index: int) -> AccountState:
    return AccountState(
        account_id=f"acc-{index}",
        status=draw(st.sampled_from(list(AccountStatus))),
        used_percent=draw(_used_percents()),
        reset_at=draw(_epoch_offsets()),
        primary_reset_at=draw(_epoch_ints()),
        primary_window_minutes=draw(st.sampled_from([None, 300, 10_080])),
        blocked_at=draw(_epoch_offsets()),
        cooldown_until=draw(_epoch_offsets()),
        secondary_used_percent=draw(_used_percents()),
        secondary_reset_at=draw(_epoch_ints()),
        last_error_at=draw(_epoch_offsets()),
        last_selected_at=draw(_epoch_offsets()),
        error_count=draw(st.sampled_from([0, 1, 3, 4, 7])),
        plan_type=draw(st.sampled_from([None, "plus", "pro", "team"])),
        capacity_credits=draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=100.0))),
        health_tier=draw(st.sampled_from([HEALTH_TIER_HEALTHY, HEALTH_TIER_DRAINING, HEALTH_TIER_PROBING])),
        priority_used_percent=draw(_used_percents()),
        priority_secondary_used_percent=draw(_used_percents()),
        priority_reset_at=draw(_epoch_ints()),
        limit_scoped_usage=draw(st.booleans()),
        access_token_expires_at=draw(_epoch_offsets()),
        routing_policy=draw(
            st.sampled_from([ROUTING_POLICY_NORMAL, ROUTING_POLICY_BURN_FIRST, ROUTING_POLICY_PRESERVE])
        ),
        ignore_standard_quota=draw(st.booleans()),
    )


@st.composite
def _account_pools(draw: st.DrawFn) -> list[AccountState]:
    size = draw(st.integers(min_value=0, max_value=6))
    return [draw(_account_states(index)) for index in range(size)]


@given(
    states=_account_pools(),
    routing_strategy=st.sampled_from(_ROUTING_STRATEGIES),
    prefer_earlier_reset=st.booleans(),
    prefer_earlier_reset_window=st.sampled_from(_RESET_WINDOWS),
    budget_threshold_pct=st.sampled_from([50.0, 95.0, 100.0]),
    secondary_budget_threshold_pct=st.sampled_from([80.0, 100.0]),
    apply_secondary_budget_threshold=st.booleans(),
)
@settings(max_examples=400, deadline=None)
def test_probe_selection_answers_usage_exhaustion_exactly_when_foreground_selection_does(
    states: list[AccountState],
    routing_strategy: RoutingStrategy,
    prefer_earlier_reset: bool,
    prefer_earlier_reset_window: ResetPreferenceWindow,
    budget_threshold_pct: float,
    secondary_budget_threshold_pct: float,
    apply_secondary_budget_threshold: bool,
) -> None:
    """The exhaustion answer depends on the pool, never on the traffic class or the probe order.

    The probe rides on ``check_opportunistic_admission`` — the deterministic
    opportunistic selector — so, for one and the same selection question, its
    ``usage_limit_reached`` must coincide with what foreground selection would
    tell that request, ``resets_at`` included. Only the two knobs the
    opportunistic path flips are varied here (``traffic_class`` and
    ``deterministic_probe``); both sides evaluate their own copy at one frozen
    instant because ordinary selection expires elapsed windows in place. The
    budget subset the two production paths draw from differs
    (``apply_secondary_budget_threshold``): the production-knob instance holds
    outside the drain strategies (property below); under the drain strategies
    the probe declines instead (``test_probe_declines_under_the_drain_strategies_*``).
    """
    with mock.patch("time.time", return_value=_NOW):
        probe_states = deepcopy(states)
        probe = _select_account_preferring_budget_safe(
            probe_states,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            budget_threshold_pct=budget_threshold_pct,
            secondary_budget_threshold_pct=secondary_budget_threshold_pct,
            apply_secondary_budget_threshold=apply_secondary_budget_threshold,
            deterministic_probe=True,
            traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC,
            ignore_standard_quota=False,
            usage_exhaustion_states=probe_states,
        )
        foreground_states = deepcopy(states)
        foreground = _select_account_preferring_budget_safe(
            foreground_states,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            budget_threshold_pct=budget_threshold_pct,
            secondary_budget_threshold_pct=secondary_budget_threshold_pct,
            apply_secondary_budget_threshold=apply_secondary_budget_threshold,
            traffic_class=TRAFFIC_CLASS_FOREGROUND,
            ignore_standard_quota=False,
            allow_usage_exhaustion_error=True,
            usage_exhaustion_states=foreground_states,
        )

    probe_exhausted = probe.error_code == USAGE_LIMIT_REACHED
    foreground_exhausted = foreground.error_code == USAGE_LIMIT_REACHED
    assert probe_exhausted == foreground_exhausted
    if probe_exhausted:
        assert probe.account is None and foreground.account is None
        assert probe.resets_at == foreground.resets_at
        assert probe.error_message == foreground.error_message


# The complement of the probe's decline set: exactly the strategies the production-knob property below proves.
_NON_DRAIN_STRATEGIES: tuple[RoutingStrategy, ...] = tuple(
    strategy for strategy in _ROUTING_STRATEGIES if strategy not in DRAIN_ROUTING_STRATEGIES
)


def test_probe_decline_set_is_exactly_the_selectors_drain_family() -> None:
    """The decline set, the proven complement and the selector's own special-cased branch must stay one set."""

    assert DRAIN_ROUTING_STRATEGIES == frozenset({"sequential_drain", "reset_drain", "single_account"})
    assert DRAIN_ROUTING_STRATEGIES | frozenset(_NON_DRAIN_STRATEGIES) == frozenset(_ROUTING_STRATEGIES)
    assert DRAIN_ROUTING_STRATEGIES.isdisjoint(_NON_DRAIN_STRATEGIES)


@given(
    states=_account_pools(),
    routing_strategy=st.sampled_from(_NON_DRAIN_STRATEGIES),
    prefer_earlier_reset=st.booleans(),
    prefer_earlier_reset_window=st.sampled_from(_RESET_WINDOWS),
    budget_threshold_pct=st.sampled_from([50.0, 95.0, 100.0]),
    secondary_budget_threshold_pct=st.sampled_from([80.0, 100.0]),
)
@settings(max_examples=400, deadline=None)
def test_probe_selection_parity_holds_under_the_production_knobs_outside_the_drain_strategies(
    states: list[AccountState],
    routing_strategy: RoutingStrategy,
    prefer_earlier_reset: bool,
    prefer_earlier_reset_window: ResetPreferenceWindow,
    budget_threshold_pct: float,
    secondary_budget_threshold_pct: float,
) -> None:
    """The production instance of the parity property: the opportunistic admission check applies the secondary
    budget threshold (``opportunistic_admission.py``), foreground selection does not. Outside the drain strategies
    the budget subset only orders candidates and both paths fall back to the whole pool, so the exhaustion answer
    agrees; under the drain strategies the subset *is* the pool (pinned divergence below)."""

    with mock.patch("time.time", return_value=_NOW):
        probe_states = deepcopy(states)
        probe = _select_account_preferring_budget_safe(
            probe_states,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            budget_threshold_pct=budget_threshold_pct,
            secondary_budget_threshold_pct=secondary_budget_threshold_pct,
            apply_secondary_budget_threshold=True,
            deterministic_probe=True,
            traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC,
            ignore_standard_quota=False,
            usage_exhaustion_states=probe_states,
        )
        foreground_states = deepcopy(states)
        foreground = _select_account_preferring_budget_safe(
            foreground_states,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            budget_threshold_pct=budget_threshold_pct,
            secondary_budget_threshold_pct=secondary_budget_threshold_pct,
            apply_secondary_budget_threshold=False,
            traffic_class=TRAFFIC_CLASS_FOREGROUND,
            ignore_standard_quota=False,
            allow_usage_exhaustion_error=True,
            usage_exhaustion_states=foreground_states,
        )

    probe_exhausted = probe.error_code == USAGE_LIMIT_REACHED
    foreground_exhausted = foreground.error_code == USAGE_LIMIT_REACHED
    assert probe_exhausted == foreground_exhausted
    if probe_exhausted:
        assert probe.resets_at == foreground.resets_at
        assert probe.error_message == foreground.error_message


def _drain_budget_subsets(
    states: list[AccountState], *, apply_secondary_budget_threshold: bool, traffic_class: TrafficClass
) -> tuple[bool, int | None]:
    with mock.patch("time.time", return_value=_NOW):
        result = _select_account_preferring_budget_safe(
            deepcopy(states),
            prefer_earlier_reset=False,
            routing_strategy="sequential_drain",
            budget_threshold_pct=50.0,
            secondary_budget_threshold_pct=80.0,
            apply_secondary_budget_threshold=apply_secondary_budget_threshold,
            deterministic_probe=traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC,
            traffic_class=traffic_class,
            ignore_standard_quota=False,
            usage_exhaustion_states=deepcopy(states),
        )
    return result.error_code == USAGE_LIMIT_REACHED, result.resets_at


def test_drain_strategy_budget_subsets_diverge_in_mixed_quota_pools_which_is_why_the_probe_declines() -> None:
    """Premise of design decision 28, kept as a positive assertion: under the drain strategies the opportunistic
    path (``apply_secondary_budget_threshold=True``) and the foreground path (``False``) select from different
    budget subsets and return the subset's answer directly, so a pool mixing an available additional-quota-scoped
    account (exempt from the exhaustion predicate) with a usage-exhausted account is called exhausted by foreground
    selection and not by the probe's selector. The probe therefore declines under the drain family instead of
    claiming parity it cannot establish; when the selector evaluates exhaustion over the full pool this assertion
    fails and the decline can be revisited."""

    # Available, but outside both budget subsets (preserve) and outside the exhaustion
    # predicate's eligibility (additional-quota scoped).
    exempt_available = AccountState(
        account_id="acc-additional-quota",
        status=AccountStatus.ACTIVE,
        used_percent=30.0,
        secondary_used_percent=40.0,
        routing_policy=ROUTING_POLICY_PRESERVE,
        ignore_standard_quota=True,
    )
    # Usage-proven exhausted on the weekly window only: inside the foreground subset
    # (primary unknown), outside the opportunistic one (secondary above threshold).
    exhausted = AccountState(
        account_id="acc-exhausted",
        status=AccountStatus.RATE_LIMITED,
        secondary_used_percent=100.0,
        secondary_reset_at=int(_NOW + 3600),
    )
    states = [exempt_available, exhausted]

    probe_path = _drain_budget_subsets(
        states, apply_secondary_budget_threshold=True, traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC
    )
    foreground_path = _drain_budget_subsets(
        states, apply_secondary_budget_threshold=False, traffic_class=TRAFFIC_CLASS_FOREGROUND
    )

    assert probe_path != foreground_path
    assert foreground_path == (True, int(_NOW + 3600)), "foreground selection answers the structured 429"
    assert probe_path == (False, None), "the probe's selector falls back to the whole pool and sees the exempt account"


def test_probe_selection_reports_the_earliest_exhausted_window_reset() -> None:
    """Worked example of the property: two usage-proven exhausted accounts, one reset wins."""
    states = [
        AccountState(
            account_id="acc-primary",
            status=AccountStatus.QUOTA_EXCEEDED,
            used_percent=100.0,
            reset_at=_NOW + 1800,
            primary_reset_at=int(_NOW + 1800),
            secondary_used_percent=40.0,
            secondary_reset_at=int(_NOW + 6 * 86_400),
        ),
        AccountState(
            account_id="acc-secondary",
            status=AccountStatus.RATE_LIMITED,
            used_percent=20.0,
            reset_at=_NOW + 3 * 86_400,
            primary_reset_at=int(_NOW + 300),
            secondary_used_percent=100.0,
            secondary_reset_at=int(_NOW + 3 * 86_400),
        ),
    ]
    with mock.patch("time.time", return_value=_NOW):
        result = _select_account_preferring_budget_safe(
            deepcopy(states),
            prefer_earlier_reset=False,
            routing_strategy="capacity_weighted",
            budget_threshold_pct=95.0,
            apply_secondary_budget_threshold=True,
            deterministic_probe=True,
            traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC,
            usage_exhaustion_states=deepcopy(states),
        )

    assert result.error_code == USAGE_LIMIT_REACHED
    # The secondary-window exhaustion resets at +3d, not at its unrelated +300s primary reset.
    assert result.resets_at == int(_NOW + 1800)


# --- drain-strategy decline (design decision 28) --------------------------------

_EXHAUSTED_ANSWER = AccountSelection(
    account=None,
    error_message="Rate limit exceeded. Try again in 30m",
    error_code=USAGE_LIMIT_REACHED,
    resets_at=1_700_001_800,
)


class _LabelRecorder:
    """Records ``labels(**kw).inc()`` so the decline scenario can assert the exact label set."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.incs = 0

    def labels(self, **labels: str) -> _LabelRecorder:
        self.calls.append(labels)
        return self

    def inc(self, amount: float = 1) -> None:
        del amount
        self.incs += 1


@pytest.mark.parametrize("routing_strategy", sorted(DRAIN_ROUTING_STRATEGIES))
@pytest.mark.asyncio
async def test_probe_declines_under_the_drain_strategies_without_consulting_the_selector(
    routing_strategy: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """account-routing 'Drain strategies never trigger overflow in v1': not exhausted, no selector call, one
    ``codex_lb_pool_exhaustion_probe_declined_total{reason=drain_strategy}`` increment and a reason log line."""

    counter = _LabelRecorder()
    monkeypatch.setattr(exhaustion_probe_module, "pool_exhaustion_probe_declined_total", counter)
    service, admission = _service(_EXHAUSTED_ANSWER)

    with caplog.at_level(logging.INFO, logger=exhaustion_probe_module.logger.name):
        exhaustion = await probe_pool_usage_exhaustion(
            service,
            settings=_settings(routing_strategy, "acc_a"),
            api_key=_api_key(),
            model="gpt-5.4",
            service_tier="priority",
        )

    assert exhaustion is None
    admission.assert_not_awaited()
    assert counter.calls == [{"reason": PROBE_DECLINED_DRAIN_STRATEGY}]
    assert counter.incs == 1
    declined = [record for record in caplog.records if "pool_exhaustion_probe_declined" in record.getMessage()]
    assert len(declined) == 1
    message = declined[0].getMessage()
    assert f"reason={PROBE_DECLINED_DRAIN_STRATEGY}" in message
    assert f"routing_strategy={routing_strategy}" in message
    assert "model=gpt-5.4" in message


@pytest.mark.parametrize("routing_strategy", _NON_DRAIN_STRATEGIES)
@pytest.mark.asyncio
async def test_probe_follows_the_selector_outside_the_drain_strategies(
    routing_strategy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _LabelRecorder()
    monkeypatch.setattr(exhaustion_probe_module, "pool_exhaustion_probe_declined_total", counter)
    service, admission = _service(_EXHAUSTED_ANSWER)

    exhaustion = await probe_pool_usage_exhaustion(
        service, settings=_settings(routing_strategy), api_key=_api_key(), model="gpt-5.4", service_tier=None
    )

    assert exhaustion == PoolExhaustion(resets_at=1_700_001_800, selection=_EXHAUSTED_ANSWER)
    admission.assert_awaited_once()
    assert counter.calls == []


@pytest.mark.parametrize(
    "settings_snapshot",
    [
        pytest.param(SimpleNamespace(), id="stale_snapshot_without_strategy"),
        pytest.param(_settings(None), id="unset_strategy"),
        pytest.param(_settings("not-a-strategy"), id="unknown_value_falls_back_like_the_check"),
    ],
)
@pytest.mark.asyncio
async def test_probe_consults_the_selector_when_the_snapshot_names_no_drain_strategy(
    settings_snapshot: SimpleNamespace,
) -> None:
    """The admission check itself falls back to ``capacity_weighted`` for a missing or unknown value."""

    service, admission = _service(_EXHAUSTED_ANSWER)

    exhaustion = await probe_pool_usage_exhaustion(
        service, settings=settings_snapshot, api_key=None, model="gpt-5.4", service_tier=None
    )

    assert exhaustion is not None
    admission.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_decline_metric_failure_never_breaks_the_decline(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken:
        def labels(self, **labels: str) -> _Broken:
            raise RuntimeError("metrics client down")

    monkeypatch.setattr(exhaustion_probe_module, "pool_exhaustion_probe_declined_total", _Broken())
    service, admission = _service(_EXHAUSTED_ANSWER)

    assert (
        await probe_pool_usage_exhaustion(
            service, settings=_settings("reset_drain"), api_key=None, model=None, service_tier=None
        )
        is None
    )
    admission.assert_not_awaited()
