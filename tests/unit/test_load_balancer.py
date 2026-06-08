from __future__ import annotations

import random
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import usage as usage_core
from app.core.balancer import (
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    AccountState,
    RoutingCost,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    select_account,
)
from app.core.usage.quota import apply_usage_quota
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy.load_balancer import (
    RuntimeState,
    _additional_quota_applies_to_plan,
    _AdditionalLimitFilterResult,
    _build_states,
    _extract_credit_status,
    _select_account_preferring_budget_safe,
    _select_long_window_entry,
    _state_above_sticky_budget_threshold,
    _state_from_account,
    background_recovery_state_from_account,
)

pytestmark = pytest.mark.unit


def test_select_account_picks_lowest_used_percent():
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=50.0),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states, routing_strategy="usage_weighted")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_applies_planner_cold_start_penalty():
    states = [
        AccountState("cold", AccountStatus.ACTIVE, used_percent=0.0),
        AccountState("active", AccountStatus.ACTIVE, used_percent=20.0),
    ]

    result = select_account(
        states,
        routing_strategy="usage_weighted",
        routing_costs={"cold": RoutingCost(total=40.0, reason="cold_start_outside_work")},
    )

    assert result.account is not None
    assert result.account.account_id == "active"


def test_select_account_prefers_expiring_active_window_bonus():
    states = [
        AccountState("expiring", AccountStatus.ACTIVE, used_percent=60.0),
        AccountState("fresh", AccountStatus.ACTIVE, used_percent=10.0),
    ]

    result = select_account(
        states,
        routing_strategy="usage_weighted",
        routing_costs={"expiring": RoutingCost(total=-20.0, reason="expiring_active_window")},
    )

    assert result.account is not None
    assert result.account.account_id == "expiring"


def test_select_account_prefers_earlier_secondary_reset_bucket():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 3 * 24 * 3600),
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            secondary_used_percent=50.0,
            secondary_reset_at=int(now + 2 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=True, routing_strategy="usage_weighted")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_secondary_reset_is_bucketed_by_day():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=20.0,
            secondary_used_percent=20.0,
            secondary_reset_at=int(now + 23 * 3600),
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 1 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=True, routing_strategy="usage_weighted")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_prefers_lower_secondary_used_with_same_reset_bucket():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=5.0,
            secondary_used_percent=80.0,
            secondary_reset_at=int(now + 6 * 3600),
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 1 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=True, routing_strategy="usage_weighted")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_deprioritizes_missing_secondary_reset_at():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=0.0,
            secondary_used_percent=0.0,
            secondary_reset_at=None,
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=90.0,
            secondary_used_percent=90.0,
            secondary_reset_at=int(now + 1 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=True, routing_strategy="usage_weighted")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_ignores_reset_when_disabled():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 5 * 24 * 3600),
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            secondary_used_percent=50.0,
            secondary_reset_at=int(now + 1 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=False, routing_strategy="usage_weighted")
    assert result.account is not None
    assert result.account.account_id == "a"


def test_select_account_prefers_burn_first_policy_before_usage():
    states = [
        AccountState("normal", AccountStatus.ACTIVE, used_percent=1.0, routing_policy="normal"),
        AccountState("temp", AccountStatus.ACTIVE, used_percent=80.0, routing_policy="burn_first"),
    ]

    result = select_account(states, routing_strategy="usage_weighted")

    assert result.account is not None
    assert result.account.account_id == "temp"


def test_select_account_preserves_accounts_until_no_others_are_available():
    states = [
        AccountState("review", AccountStatus.ACTIVE, used_percent=1.0, routing_policy="preserve"),
        AccountState("normal", AccountStatus.ACTIVE, used_percent=95.0, routing_policy="normal"),
    ]

    result = select_account(states, routing_strategy="usage_weighted")

    assert result.account is not None
    assert result.account.account_id == "normal"


def test_select_account_falls_back_to_preserve_policy_when_needed():
    states = [
        AccountState("review", AccountStatus.ACTIVE, used_percent=70.0, routing_policy="preserve"),
        AccountState("normal", AccountStatus.RATE_LIMITED, used_percent=1.0, reset_at=int(time.time() + 60)),
    ]

    result = select_account(states, routing_strategy="usage_weighted")

    assert result.account is not None
    assert result.account.account_id == "review"


def test_select_account_treats_unknown_routing_policy_as_normal():
    states = [
        AccountState("review", AccountStatus.ACTIVE, used_percent=1.0, routing_policy="preserve"),
        AccountState("legacy", AccountStatus.ACTIVE, used_percent=95.0, routing_policy="unexpected"),
    ]

    result = select_account(states, routing_strategy="usage_weighted")

    assert result.account is not None
    assert result.account.account_id == "legacy"


def test_select_account_can_ignore_standard_quota_for_additional_pool():
    states = [
        AccountState(
            "spark",
            AccountStatus.QUOTA_EXCEEDED,
            used_percent=100.0,
            reset_at=int(time.time() + 3600),
        )
    ]

    result = select_account(states, routing_strategy="usage_weighted", ignore_standard_quota=True)

    assert result.account is not None
    assert result.account.account_id == "spark"


def test_select_account_can_ignore_standard_rate_limit_for_additional_pool():
    states = [
        AccountState(
            "spark",
            AccountStatus.RATE_LIMITED,
            used_percent=100.0,
            reset_at=int(time.time() + 3600),
        )
    ]

    result = select_account(states, routing_strategy="usage_weighted", ignore_standard_quota=True)

    assert result.account is not None
    assert result.account.account_id == "spark"


def test_select_account_does_not_ignore_live_cooldown_for_additional_pool():
    now = time.time()
    states = [
        AccountState(
            "spark",
            AccountStatus.ACTIVE,
            used_percent=1.0,
            cooldown_until=now + 60,
        )
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted", ignore_standard_quota=True)

    assert result.account is None


def test_budget_safe_selection_keeps_burn_first_ahead_of_threshold():
    states = [
        AccountState("normal", AccountStatus.ACTIVE, used_percent=1.0, routing_policy="normal"),
        AccountState("temp", AccountStatus.ACTIVE, used_percent=99.0, routing_policy="burn_first"),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "temp"


def test_budget_safe_selection_applies_burn_first_after_health_tier_filtering():
    states = [
        AccountState(
            "normal",
            AccountStatus.ACTIVE,
            used_percent=1.0,
            routing_policy="normal",
            health_tier=HEALTH_TIER_HEALTHY,
        ),
        AccountState(
            "temp",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            routing_policy="burn_first",
            health_tier=HEALTH_TIER_DRAINING,
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "normal"


def test_budget_safe_selection_falls_back_when_burn_first_unavailable():
    states = [
        AccountState(
            "temp",
            AccountStatus.QUOTA_EXCEEDED,
            used_percent=100.0,
            reset_at=int(time.time() + 300_000),
            routing_policy="burn_first",
        ),
        AccountState("normal", AccountStatus.ACTIVE, used_percent=1.0, routing_policy="normal"),
        AccountState("review", AccountStatus.ACTIVE, used_percent=1.0, routing_policy="preserve"),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "normal"


def test_budget_safe_selection_keeps_preserve_behind_over_budget_normal():
    states = [
        AccountState("review", AccountStatus.ACTIVE, used_percent=1.0, routing_policy="preserve"),
        AccountState("normal", AccountStatus.ACTIVE, used_percent=99.0, routing_policy="normal"),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "normal"


def test_opportunistic_burn_first_can_reach_zero_when_another_account_remains():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "normal",
            AccountStatus.ACTIVE,
            used_percent=20.0,
            secondary_used_percent=20.0,
            routing_policy="normal",
        ),
        AccountState(
            "temp",
            AccountStatus.ACTIVE,
            used_percent=100.0,
            secondary_used_percent=100.0,
            routing_policy="burn_first",
        ),
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted", traffic_class="opportunistic")

    assert result.account is not None
    assert result.account.account_id == "temp"


def test_opportunistic_normal_can_reach_zero_when_preserve_has_foreground_reserve():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "normal",
            AccountStatus.ACTIVE,
            used_percent=100.0,
            secondary_used_percent=100.0,
            routing_policy="normal",
        ),
        AccountState(
            "review",
            AccountStatus.ACTIVE,
            used_percent=20.0,
            reset_at=now + 3 * 3600,
            secondary_used_percent=20.0,
            secondary_reset_at=int(now + 3 * 24 * 3600),
            routing_policy="preserve",
        ),
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted", traffic_class="opportunistic")

    assert result.account is not None
    assert result.account.account_id == "normal"


def test_opportunistic_last_normal_keeps_emergency_floor():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "normal",
            AccountStatus.ACTIVE,
            used_percent=96.0,
            secondary_used_percent=96.0,
            routing_policy="normal",
        )
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted", traffic_class="opportunistic")

    assert result.account is None
    assert result.error_message == (
        "opportunistic burn window closed: no expendable account has emergency foreground reserve"
    )


def test_opportunistic_backoff_fallback_rechecks_emergency_floor():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "normal-a",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=99.0,
            routing_policy="normal",
            error_count=3,
            last_error_at=now - 1,
        ),
        AccountState(
            "normal-b",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=99.0,
            routing_policy="normal",
            error_count=3,
            last_error_at=now - 2,
        ),
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted", traffic_class="opportunistic")

    assert result.account is None
    assert result.error_message == (
        "opportunistic burn window closed: no expendable account has emergency foreground reserve"
    )


def test_opportunistic_preserve_skips_when_weekly_floor_would_be_crossed():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "review",
            AccountStatus.ACTIVE,
            used_percent=20.0,
            reset_at=now + 3 * 3600,
            secondary_used_percent=96.0,
            secondary_reset_at=int(now + 3 * 24 * 3600),
            routing_policy="preserve",
        )
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted", traffic_class="opportunistic")

    assert result.account is None
    assert result.error_message == (
        "opportunistic burn window closed: preserve floor or stale usage data blocks opportunistic burn"
    )


def test_opportunistic_preserve_skips_when_short_window_floor_would_be_crossed():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "review",
            AccountStatus.ACTIVE,
            used_percent=92.0,
            reset_at=now + 3 * 3600,
            secondary_used_percent=20.0,
            secondary_reset_at=int(now + 3 * 24 * 3600),
            routing_policy="preserve",
        )
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted", traffic_class="opportunistic")

    assert result.account is None
    assert result.error_message == (
        "opportunistic burn window closed: preserve floor or stale usage data blocks opportunistic burn"
    )


def test_opportunistic_preserve_weekly_floor_decreases_near_reset_when_pace_is_behind():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "review",
            AccountStatus.ACTIVE,
            used_percent=30.0,
            reset_at=now + 3 * 3600,
            secondary_used_percent=90.0,
            secondary_reset_at=int(now + 5 * 3600),
            routing_policy="preserve",
        )
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted", traffic_class="opportunistic")

    assert result.account is not None
    assert result.account.account_id == "review"


def test_opportunistic_preserve_short_window_floor_remains_nonzero_near_weekly_reset():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "review",
            AccountStatus.ACTIVE,
            used_percent=96.0,
            reset_at=now + 30 * 60,
            secondary_used_percent=94.0,
            secondary_reset_at=int(now + 5 * 3600),
            routing_policy="preserve",
        )
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted", traffic_class="opportunistic")

    assert result.account is None
    assert result.error_message == (
        "opportunistic burn window closed: preserve floor or stale usage data blocks opportunistic burn"
    )


def test_select_account_skips_rate_limited_until_reset():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.RATE_LIMITED, used_percent=5.0, reset_at=int(now + 60)),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_reports_paused_and_deactivated_without_reauth_reason():
    states = [
        AccountState("paused", AccountStatus.PAUSED, used_percent=5.0),
        AccountState("deactivated", AccountStatus.DEACTIVATED, used_percent=5.0),
    ]

    result = select_account(states)

    assert result.account is None
    assert result.error_message == "All accounts are paused or deactivated"


def test_select_account_round_robin_prefers_least_recently_selected():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=90.0, last_selected_at=now - 2),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0, last_selected_at=now - 30),
        AccountState("c", AccountStatus.ACTIVE, used_percent=5.0, last_selected_at=now - 5),
    ]
    result = select_account(states, now=now, routing_strategy="round_robin")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_round_robin_prefers_never_selected():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=1.0, last_selected_at=now - 1),
        AccountState("b", AccountStatus.ACTIVE, used_percent=99.0, last_selected_at=None),
    ]
    result = select_account(states, now=now, routing_strategy="round_robin")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_single_account_returns_selected_candidate():
    states = [
        AccountState("selected", AccountStatus.ACTIVE, used_percent=10.0, secondary_used_percent=20.0),
    ]
    result = select_account(states, routing_strategy="single_account")
    assert result.account is not None
    assert result.account.account_id == "selected"


def test_select_account_single_account_rejects_exhausted_candidate():
    states = [
        AccountState("selected", AccountStatus.ACTIVE, used_percent=100.0, secondary_used_percent=20.0),
    ]
    result = select_account(states, routing_strategy="single_account")
    assert result.account is None
    assert result.error_message == "Selected account is exhausted or unavailable"


def test_select_account_sequential_drain_uses_lowest_capacity_first_until_exhausted():
    states = [
        AccountState(
            "pro", AccountStatus.ACTIVE, used_percent=0.0, secondary_used_percent=0.0, capacity_credits=50_400.0
        ),
        AccountState(
            "plus", AccountStatus.ACTIVE, used_percent=0.0, secondary_used_percent=0.0, capacity_credits=7_560.0
        ),
        AccountState(
            "free", AccountStatus.ACTIVE, used_percent=95.0, secondary_used_percent=95.0, capacity_credits=1_134.0
        ),
    ]
    result = select_account(states, routing_strategy="sequential_drain")
    assert result.account is not None
    assert result.account.account_id == "free"


def test_select_account_sequential_drain_skips_exhausted_lowest_capacity_account():
    states = [
        AccountState(
            "free", AccountStatus.ACTIVE, used_percent=100.0, secondary_used_percent=99.0, capacity_credits=1_134.0
        ),
        AccountState(
            "plus", AccountStatus.ACTIVE, used_percent=0.0, secondary_used_percent=0.0, capacity_credits=7_560.0
        ),
        AccountState(
            "pro", AccountStatus.ACTIVE, used_percent=0.0, secondary_used_percent=0.0, capacity_credits=50_400.0
        ),
    ]
    result = select_account(states, routing_strategy="sequential_drain")
    assert result.account is not None
    assert result.account.account_id == "plus"


def test_select_account_sequential_drain_does_not_switch_on_draining_health_tier():
    states = [
        AccountState(
            "free",
            AccountStatus.ACTIVE,
            used_percent=90.0,
            secondary_used_percent=90.0,
            health_tier=1,
            capacity_credits=1_134.0,
        ),
        AccountState(
            "plus",
            AccountStatus.ACTIVE,
            used_percent=0.0,
            secondary_used_percent=0.0,
            health_tier=0,
            capacity_credits=7_560.0,
        ),
    ]
    result = select_account(states, routing_strategy="sequential_drain")
    assert result.account is not None
    assert result.account.account_id == "free"


def test_select_account_sequential_drain_stable_with_equal_capacity_accounts():
    states = [
        AccountState(
            "plus-a", AccountStatus.ACTIVE, used_percent=0.0, secondary_used_percent=0.0, capacity_credits=7_560.0
        ),
        AccountState(
            "plus-b", AccountStatus.ACTIVE, used_percent=0.0, secondary_used_percent=0.0, capacity_credits=7_560.0
        ),
    ]
    first = select_account(states, routing_strategy="sequential_drain")
    second = select_account(list(reversed(states)), routing_strategy="sequential_drain")
    assert first.account is not None
    assert second.account is not None
    assert first.account.account_id == second.account.account_id


def test_select_account_reset_drain_prefers_nearest_weekly_reset_with_remaining_quota():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "soon-5h-late-weekly",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            reset_at=int(now + 300),
            secondary_reset_at=int(now + 5 * 24 * 3600),
        ),
        AccountState(
            "later-5h-soon-weekly",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            secondary_used_percent=20.0,
            reset_at=int(now + 7200),
            secondary_reset_at=int(now + 2 * 24 * 3600),
        ),
        AccountState(
            "middle",
            AccountStatus.ACTIVE,
            used_percent=0.0,
            secondary_used_percent=0.0,
            reset_at=int(now + 1800),
            secondary_reset_at=int(now + 3 * 24 * 3600),
        ),
    ]
    result = select_account(states, now=now, routing_strategy="reset_drain")
    assert result.account is not None
    assert result.account.account_id == "later-5h-soon-weekly"


def test_select_account_reset_drain_skips_exhausted_nearest_reset_account():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "soon-exhausted",
            AccountStatus.ACTIVE,
            used_percent=100.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 300),
        ),
        AccountState(
            "next",
            AccountStatus.ACTIVE,
            used_percent=0.0,
            secondary_used_percent=0.0,
            secondary_reset_at=int(now + 1800),
        ),
    ]
    result = select_account(states, now=now, routing_strategy="reset_drain")
    assert result.account is not None
    assert result.account.account_id == "next"


def test_select_account_reset_drain_drains_highest_remaining_inside_same_reset_bucket():
    now = 1_700_000_000.0
    reset_at = int(now + 300)
    states = [
        AccountState(
            "low-left",
            AccountStatus.ACTIVE,
            used_percent=90.0,
            secondary_used_percent=90.0,
            secondary_reset_at=reset_at,
        ),
        AccountState(
            "high-left",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=reset_at,
        ),
    ]
    result = select_account(states, now=now, routing_strategy="reset_drain")
    assert result.account is not None
    assert result.account.account_id == "high-left"


def test_select_account_reset_drain_uses_bucket_before_exact_reset_time():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "soon-low-left",
            AccountStatus.ACTIVE,
            used_percent=95.0,
            secondary_used_percent=95.0,
            secondary_reset_at=int(now + 300),
        ),
        AccountState(
            "later-high-left",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 1800),
        ),
    ]
    result = select_account(states, now=now, routing_strategy="reset_drain")
    assert result.account is not None
    assert result.account.account_id == "later-high-left"


def test_select_account_reset_drain_falls_back_to_primary_reset_when_weekly_unknown():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "late-primary", AccountStatus.ACTIVE, used_percent=0.0, secondary_used_percent=0.0, reset_at=int(now + 1800)
        ),
        AccountState(
            "soon-primary", AccountStatus.ACTIVE, used_percent=0.0, secondary_used_percent=0.0, reset_at=int(now + 300)
        ),
    ]
    result = select_account(states, now=now, routing_strategy="reset_drain")
    assert result.account is not None
    assert result.account.account_id == "soon-primary"


def test_handle_rate_limit_sets_reset_at_from_message(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_rate_limit(state, {"message": "Try again in 1.5s"})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 1.5)


def test_handle_rate_limit_uses_backoff_when_no_delay(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    monkeypatch.setattr("app.core.balancer.logic.backoff_seconds", lambda _: 0.2)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_rate_limit(state, {"message": "Rate limit exceeded."})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 0.2)


def test_select_account_skips_cooldown_until_expired():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=5.0, cooldown_until=now + 60),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_resets_error_count_when_cooldown_expires():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        cooldown_until=now - 1,
        last_error_at=now - 10,
        error_count=4,
    )
    result = select_account([state], now=now)
    assert result.account is not None
    assert state.cooldown_until is None
    assert state.last_error_at is None
    assert state.error_count == 0


def test_select_account_reports_cooldown_wait_time():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=5.0, cooldown_until=now + 30),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0, cooldown_until=now + 60),
    ]
    result = select_account(states, now=now)
    assert result.account is None
    assert result.error_message is not None
    assert "Try again in" in result.error_message


def test_select_account_caps_quota_exceeded_retry_hint():
    now = 1_700_000_000.0
    far_future_reset = int(now + 89_872)
    states = [
        AccountState(
            "a",
            AccountStatus.QUOTA_EXCEEDED,
            used_percent=100.0,
            reset_at=far_future_reset,
        ),
        AccountState(
            "b",
            AccountStatus.QUOTA_EXCEEDED,
            used_percent=100.0,
            reset_at=int(now + 271_819),
        ),
    ]
    result = select_account(states, now=now)
    assert result.account is None
    assert result.error_message == "Rate limit exceeded. Try again in 300s"
    # The underlying state values are intentionally not clamped — only the
    # surfaced hint is.
    assert states[0].reset_at == far_future_reset


def test_select_account_preserves_short_quota_exceeded_retry_hint():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "a",
            AccountStatus.QUOTA_EXCEEDED,
            used_percent=100.0,
            reset_at=int(now + 60),
        ),
    ]
    result = select_account(states, now=now)
    assert result.account is None
    assert result.error_message == "Rate limit exceeded. Try again in 60s"


def test_select_account_caps_cooldown_retry_hint():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=5.0,
            cooldown_until=now + 86_400,
        ),
    ]
    result = select_account(states, now=now)
    assert result.account is None
    assert result.error_message == "Rate limit exceeded. Try again in 300s"
    assert states[0].cooldown_until == now + 86_400


def test_apply_usage_quota_sets_fallback_reset_for_primary_window(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=100.0,
        primary_reset=None,
        primary_window_minutes=1,
        runtime_reset=None,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 100.0
    assert reset_at is not None
    assert reset_at == pytest.approx(now + 60.0)


def test_apply_usage_quota_secondary_exhausted_without_credits_sets_quota_exceeded():
    secondary_reset = 1_700_000_000
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=40.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=None,
        secondary_used=100.0,
        secondary_reset=secondary_reset,
        credits_has=False,
        credits_unlimited=False,
        credits_balance=0.0,
    )
    assert status == AccountStatus.QUOTA_EXCEEDED
    assert used_percent == 100.0
    assert reset_at == secondary_reset


def test_apply_usage_quota_secondary_exhausted_with_credits_reactivates_account():
    future_reset = 1_700_000_000.0
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.QUOTA_EXCEEDED,
        primary_used=40.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=future_reset,
        secondary_used=100.0,
        secondary_reset=int(future_reset),
        credits_has=False,
        credits_unlimited=False,
        credits_balance=25.0,
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 40.0
    assert reset_at is None


def test_handle_quota_exceeded_sets_used_percent_and_cooldown():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_quota_exceeded(state, {})
    assert state.status == AccountStatus.QUOTA_EXCEEDED
    assert state.used_percent == 100.0
    assert state.cooldown_until is not None


def test_handle_permanent_failure_sets_reason():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_permanent_failure(state, "refresh_token_expired")
    assert state.status == AccountStatus.REAUTH_REQUIRED
    assert state.deactivation_reason is not None


def test_handle_permanent_failure_sets_reauth_required_for_token_invalidated():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_permanent_failure(state, "token_invalidated")
    assert state.status == AccountStatus.REAUTH_REQUIRED
    assert state.deactivation_reason == "Authentication token invalidated - re-login required"


def test_handle_permanent_failure_sets_reason_for_account_deactivated():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_permanent_failure(state, "account_deactivated")
    assert state.status == AccountStatus.DEACTIVATED
    assert state.deactivation_reason == "Account has been deactivated"


def test_apply_usage_quota_respects_runtime_reset_for_quota_exceeded(monkeypatch):
    now = 1_700_000_000.0
    future = now + 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    # Normally 50% used would reset it to ACTIVE, but runtime_reset is in future
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.QUOTA_EXCEEDED,
        primary_used=50.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=future,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.QUOTA_EXCEEDED
    assert used_percent == 50.0
    assert reset_at == future


def test_apply_usage_quota_respects_runtime_reset_for_rate_limited(monkeypatch):
    now = 1_700_000_000.0
    future = now + 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    # Normally 50% used would reset it to ACTIVE, but runtime_reset is in future
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=50.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=future,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 50.0
    assert reset_at == future


def test_apply_usage_quota_resets_to_active_if_runtime_reset_expired(monkeypatch):
    now = 1_700_000_000.0
    past = now - 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=50.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=past,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 50.0
    assert reset_at is None


def test_select_account_resets_used_percent_when_rate_limit_expires():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.RATE_LIMITED,
        used_percent=100.0,
        reset_at=now - 10,
    )

    result = select_account([state], now=now)

    assert result.account is not None
    assert state.status == AccountStatus.ACTIVE
    assert state.used_percent == 0.0
    assert state.reset_at is None


def test_select_account_resets_secondary_used_percent_when_quota_exceeded_expires():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.QUOTA_EXCEEDED,
        used_percent=100.0,
        secondary_used_percent=100.0,
        reset_at=now - 10,
    )

    result = select_account([state], now=now)

    assert result.account is not None
    assert state.status == AccountStatus.ACTIVE
    assert state.used_percent == 0.0
    assert state.secondary_used_percent == 0.0
    assert state.reset_at is None


def test_apply_usage_quota_clears_quota_exceeded_when_runtime_reset_is_none(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.QUOTA_EXCEEDED,
        primary_used=30.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=None,
        secondary_used=5.0,
        secondary_reset=int(now + 3600),
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 30.0
    assert reset_at is None


def test_apply_usage_quota_clears_rate_limited_when_runtime_reset_is_none(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=10.0,
        primary_reset=int(now + 3600),
        primary_window_minutes=60,
        runtime_reset=None,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 10.0
    assert reset_at is None


def test_quota_exceeded_cooldown_blocks_selection_despite_low_usage():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        cooldown_until=now + 120.0,
    )
    result = select_account([state], now=now)
    assert result.account is None


def test_quota_exceeded_cooldown_allows_selection_after_expiry():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        cooldown_until=now - 1.0,
    )
    result = select_account([state], now=now)
    assert result.account is not None
    assert result.account.account_id == "a"


def test_bypass_quota_exceeded_keeps_account_in_pool():
    """When bypass_quota_exceeded=True, a QUOTA_EXCEEDED account should not be filtered out."""
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.QUOTA_EXCEEDED,
        used_percent=100.0,
        reset_at=int(now) + 3600,
    )
    # Default (bypass=False) → account is excluded.
    result_default = select_account([state], now=now)
    assert result_default.account is None

    # With bypass=True → account stays in the pool.
    result_bypass = select_account([state], now=now, bypass_quota_exceeded=True)
    assert result_bypass.account is not None
    assert result_bypass.account.account_id == "a"


def test_bypass_quota_exceeded_still_recovers_expired_quota_state():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.QUOTA_EXCEEDED,
        used_percent=100.0,
        secondary_used_percent=100.0,
        reset_at=int(now) - 1,
    )

    result = select_account([state], now=now, bypass_quota_exceeded=True)

    assert result.account is not None
    assert result.account.account_id == "a"
    assert state.status == AccountStatus.ACTIVE
    assert state.used_percent == 0.0
    assert state.secondary_used_percent == 0.0
    assert state.reset_at is None


def test_bypass_quota_exceeded_does_not_bypass_error_backoff_fallback():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.QUOTA_EXCEEDED,
        used_percent=100.0,
        reset_at=int(now) + 3600,
        error_count=3,
        last_error_at=now - 1.0,
    )

    result = select_account(
        [state],
        now=now,
        allow_backoff_fallback=True,
        bypass_quota_exceeded=True,
    )

    assert result.account is None


def test_bypass_quota_exceeded_can_be_scoped_to_account_ids():
    now = 1_700_000_000.0
    blocked = AccountState("blocked", AccountStatus.QUOTA_EXCEEDED, used_percent=100.0, reset_at=int(now) + 3600)
    allowed = AccountState("allowed", AccountStatus.QUOTA_EXCEEDED, used_percent=100.0, reset_at=int(now) + 3600)

    result = select_account(
        [blocked, allowed],
        now=now,
        routing_strategy="round_robin",
        bypass_quota_exceeded_account_ids={"allowed"},
    )

    assert result.account is not None
    assert result.account.account_id == "allowed"


def test_scoped_quota_bypass_ignores_quota_cooldown_only_for_allowed_account():
    now = 1_700_000_000.0
    blocked = AccountState(
        "blocked",
        AccountStatus.QUOTA_EXCEEDED,
        used_percent=100.0,
        reset_at=int(now) + 3600,
        cooldown_until=now + 120.0,
    )
    allowed = AccountState(
        "allowed",
        AccountStatus.QUOTA_EXCEEDED,
        used_percent=100.0,
        reset_at=int(now) + 3600,
        cooldown_until=now + 120.0,
    )

    result = select_account(
        [blocked, allowed],
        now=now,
        routing_strategy="round_robin",
        bypass_quota_exceeded_account_ids={"allowed"},
    )

    assert result.account is None
    assert allowed.cooldown_until == now + 120.0


def test_scoped_quota_bypass_does_not_ignore_active_account_cooldown():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        cooldown_until=now + 120.0,
    )

    result = select_account([state], now=now, bypass_quota_exceeded_account_ids={"a"})

    assert result.account is None


def test_requested_limit_relative_availability_uses_requested_secondary_only_when_available():
    now = int(time.time())
    states = [
        AccountState(
            "limited-high-second",
            AccountStatus.ACTIVE,
            used_percent=1.0,
            secondary_used_percent=100.0,
            secondary_reset_at=now + 3_600,
            priority_used_percent=1.0,
            priority_capacity_credits=100.0,
            limit_scoped_usage=True,
        ),
        AccountState(
            "not-limited",
            AccountStatus.ACTIVE,
            used_percent=80.0,
            secondary_used_percent=20.0,
            secondary_reset_at=now + 3_600,
            priority_capacity_credits=100.0,
        ),
    ]

    result = select_account(
        states,
        routing_strategy="relative_availability",
        deterministic_probe=True,
        now=now,
    )

    assert result.account is not None
    assert result.account.account_id == "limited-high-second"


def test_requested_limit_relative_availability_uses_requested_reset_window():
    now = int(time.time())
    states = [
        AccountState(
            "ordinary-late-requested-soon",
            AccountStatus.ACTIVE,
            used_percent=0.0,
            secondary_used_percent=90.0,
            secondary_reset_at=now + 7 * 24 * 3600,
            priority_used_percent=0.0,
            priority_secondary_used_percent=0.0,
            priority_reset_at=now + 3_600,
            priority_capacity_credits=100.0,
            limit_scoped_usage=True,
        ),
        AccountState(
            "ordinary-soon-requested-late",
            AccountStatus.ACTIVE,
            used_percent=0.0,
            secondary_used_percent=0.0,
            secondary_reset_at=now + 3_600,
            priority_used_percent=0.0,
            priority_secondary_used_percent=0.0,
            priority_reset_at=now + 7 * 24 * 3600,
            priority_capacity_credits=100.0,
            limit_scoped_usage=True,
        ),
    ]

    result = select_account(
        states,
        routing_strategy="relative_availability",
        deterministic_probe=True,
        now=now,
    )

    assert result.account is not None
    assert result.account.account_id == "ordinary-late-requested-soon"


def test_bypass_quota_exceeded_does_not_affect_other_statuses():
    """bypass_quota_exceeded should only affect QUOTA_EXCEEDED, not hard-blocked states."""
    now = 1_700_000_000.0
    paused = AccountState("p", AccountStatus.PAUSED, used_percent=5.0)
    reauth = AccountState("r", AccountStatus.REAUTH_REQUIRED, used_percent=5.0)
    deactivated = AccountState("d", AccountStatus.DEACTIVATED, used_percent=5.0)
    quota = AccountState("q", AccountStatus.QUOTA_EXCEEDED, used_percent=100.0, reset_at=int(now) + 3600)

    result = select_account([paused, reauth, deactivated, quota], now=now, bypass_quota_exceeded=True)
    # Hard-blocked states still excluded; QUOTA_EXCEEDED is kept.
    assert result.account is not None
    assert result.account.account_id == "q"


def _make_test_account(
    account_id: str = "a",
    status: AccountStatus = AccountStatus.ACTIVE,
    reset_at: int | None = None,
    blocked_at: int | None = None,
    plan_type: str = "plus",
) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id="chatgpt-" + account_id,
        email=f"{account_id}@test.com",
        plan_type=plan_type,
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"r",
        id_token_encrypted=b"i",
        last_refresh=datetime(2025, 1, 1),
        status=status,
        reset_at=reset_at,
        blocked_at=blocked_at,
    )


def _make_test_usage(
    account_id: str = "a",
    window: str = "secondary",
    used_percent: float = 10.0,
    reset_at: int | None = None,
    recorded_at: datetime | None = None,
    window_minutes: int | None = None,
    credits_has: bool | None = None,
    credits_unlimited: bool | None = None,
    credits_balance: float | None = None,
) -> UsageHistory:
    return UsageHistory(
        id=1,
        account_id=account_id,
        recorded_at=recorded_at or datetime(2025, 1, 1),
        window=window,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=window_minutes if window_minutes is not None else (10080 if window == "secondary" else 300),
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )


def _epoch_to_naive_utc(epoch: float) -> datetime:
    from datetime import timezone

    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


def test_state_from_account_zeroes_stale_exhausted_primary_usage_after_reset(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    state = _state_from_account(
        account=_make_test_account(status=AccountStatus.ACTIVE),
        primary_entry=_make_test_usage(
            window="primary",
            used_percent=100.0,
            reset_at=int(now - 10),
            recorded_at=_epoch_to_naive_utc(now - 30),
        ),
        secondary_entry=None,
        runtime=RuntimeState(),
    )

    assert state.used_percent == 0.0
    assert state.reset_at is None


def test_state_from_account_zeroes_stale_exhausted_secondary_usage_after_reset(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    state = _state_from_account(
        account=_make_test_account(status=AccountStatus.ACTIVE),
        primary_entry=None,
        secondary_entry=_make_test_usage(
            window="secondary",
            used_percent=100.0,
            reset_at=int(now - 10),
            recorded_at=_epoch_to_naive_utc(now - 30),
        ),
        runtime=RuntimeState(),
    )

    assert state.secondary_used_percent == 0.0
    assert state.secondary_reset_at is None


def test_state_from_account_treats_monthly_usage_as_long_window_quota(monkeypatch):
    now = 1_700_000_000.0
    future_reset = int(now + 30 * 24 * 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    state = _state_from_account(
        account=_make_test_account(status=AccountStatus.ACTIVE, plan_type="free"),
        primary_entry=None,
        secondary_entry=_make_test_usage(
            window="monthly",
            used_percent=100.0,
            reset_at=future_reset,
            recorded_at=_epoch_to_naive_utc(now - 30),
            window_minutes=43200,
        ),
        runtime=RuntimeState(),
    )

    assert state.status == AccountStatus.QUOTA_EXCEEDED
    assert state.secondary_used_percent == 100.0
    assert state.secondary_reset_at == future_reset
    assert state.capacity_credits == usage_core.capacity_for_plan("free", "monthly")


def test_state_from_account_ignores_stale_monthly_usage_after_upgrade(monkeypatch):
    now = 1_700_000_000.0
    weekly_reset = int(now + 7 * 24 * 3600)
    monthly_reset = int(now + 30 * 24 * 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.ACTIVE, plan_type="plus")

    selected_entry = _select_long_window_entry(
        account=account,
        monthly_entry=_make_test_usage(
            window="monthly",
            used_percent=100.0,
            reset_at=monthly_reset,
            recorded_at=_epoch_to_naive_utc(now - 120),
            window_minutes=43200,
        ),
        secondary_entry=_make_test_usage(
            window="secondary",
            used_percent=20.0,
            reset_at=weekly_reset,
            recorded_at=_epoch_to_naive_utc(now - 30),
            window_minutes=10080,
        ),
    )
    state = _state_from_account(
        account=account,
        primary_entry=_make_test_usage(
            window="primary",
            used_percent=5.0,
            reset_at=int(now + 5 * 3600),
            recorded_at=_epoch_to_naive_utc(now - 30),
            window_minutes=300,
        ),
        secondary_entry=selected_entry,
        runtime=RuntimeState(),
    )

    assert state.status == AccountStatus.ACTIVE
    assert state.secondary_used_percent == 20.0
    assert state.secondary_reset_at == weekly_reset
    assert state.capacity_credits == usage_core.capacity_for_plan("plus", "secondary")


def test_state_from_account_ignores_zero_capacity_monthly_primary_window(monkeypatch):
    now = 1_700_000_000.0
    future_reset = int(now + 14 * 24 * 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.RATE_LIMITED, reset_at=future_reset)
    account.plan_type = "free"

    state = _state_from_account(
        account=account,
        primary_entry=_make_test_usage(
            window="primary",
            used_percent=100.0,
            reset_at=future_reset,
            recorded_at=_epoch_to_naive_utc(now - 30),
            window_minutes=43200,
        ),
        secondary_entry=_make_test_usage(
            window="secondary",
            used_percent=10.0,
            reset_at=future_reset,
            recorded_at=_epoch_to_naive_utc(now - 30),
            window_minutes=10080,
        ),
        runtime=RuntimeState(),
    )

    assert state.status == AccountStatus.ACTIVE
    assert state.used_percent is None
    assert state.reset_at is None
    assert state.secondary_used_percent == 10.0
    assert state.secondary_reset_at == future_reset


def test_state_from_account_ignores_zero_capacity_primary_for_active_free_account(monkeypatch):
    now = 1_700_000_000.0
    future_reset = int(now + 14 * 24 * 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.ACTIVE, reset_at=None)
    account.plan_type = "free"

    state = _state_from_account(
        account=account,
        primary_entry=_make_test_usage(
            window="primary",
            used_percent=100.0,
            reset_at=future_reset,
            recorded_at=_epoch_to_naive_utc(now - 30),
            window_minutes=43200,
        ),
        secondary_entry=None,
        runtime=RuntimeState(),
    )

    assert state.status == AccountStatus.ACTIVE
    assert state.used_percent is None
    assert state.reset_at is None


def test_state_from_account_preserves_free_rate_limit_without_weekly_usage_signal(monkeypatch):
    now = 1_700_000_000.0
    future_reset = int(now + 14 * 24 * 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.RATE_LIMITED, reset_at=future_reset)
    account.plan_type = "free"

    state = _state_from_account(
        account=account,
        primary_entry=_make_test_usage(
            window="primary",
            used_percent=100.0,
            reset_at=future_reset,
            recorded_at=_epoch_to_naive_utc(now - 30),
            window_minutes=43200,
        ),
        secondary_entry=None,
        runtime=RuntimeState(),
    )

    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at == future_reset


def test_state_from_account_preserves_free_rate_limit_for_legacy_unknown_primary_window(monkeypatch):
    now = 1_700_000_000.0
    future_reset = int(now + 14 * 24 * 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.RATE_LIMITED, reset_at=future_reset)
    account.plan_type = "free"

    state = _state_from_account(
        account=account,
        primary_entry=_make_test_usage(
            window="primary",
            used_percent=100.0,
            reset_at=future_reset,
            recorded_at=_epoch_to_naive_utc(now - 30),
            window_minutes=None,
        ),
        secondary_entry=_make_test_usage(
            window="secondary",
            used_percent=10.0,
            reset_at=future_reset,
            recorded_at=_epoch_to_naive_utc(now - 30),
            window_minutes=10080,
        ),
        runtime=RuntimeState(),
    )

    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at == future_reset


def test_state_from_account_recovers_quota_exceeded_on_restart_without_blocked_at_when_usage_shows_new_reset_window(
    monkeypatch,
):
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    next_reset = int(now + 7200)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=next_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.ACTIVE


def test_state_from_account_uses_secondary_credits_when_primary_lacks_credit_fields(monkeypatch):
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    primary = _make_test_usage(
        window="primary",
        used_percent=40.0,
        reset_at=None,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )
    secondary = _make_test_usage(
        used_percent=100.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
        credits_balance=25.0,
    )

    state = _state_from_account(
        account=account,
        primary_entry=primary,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.ACTIVE
    assert state.used_percent == 40.0
    assert state.reset_at is None
    assert state.blocked_at is None


def test_state_from_account_keeps_quota_exceeded_on_restart_when_fresh_usage_is_missing_and_no_blocked_at(
    monkeypatch,
):
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 600),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_preserves_credits_when_weekly_primary_replaces_secondary(monkeypatch):
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    weekly_primary = _make_test_usage(
        window="primary",
        used_percent=100.0,
        reset_at=int(now + 7200),
        recorded_at=_epoch_to_naive_utc(now - 30),
        window_minutes=10080,
    )
    previous_secondary_with_credits = _make_test_usage(
        window="secondary",
        used_percent=95.0,
        reset_at=int(now + 5400),
        recorded_at=_epoch_to_naive_utc(now - 60),
        window_minutes=10080,
        credits_has=True,
        credits_unlimited=False,
        credits_balance=1.0,
    )

    state = _state_from_account(
        account=account,
        primary_entry=weekly_primary,
        secondary_entry=previous_secondary_with_credits,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.ACTIVE
    assert state.reset_at is None
    assert state.secondary_used_percent == 100.0


def test_state_from_account_uses_freshest_credit_snapshot(monkeypatch):
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    stale_primary_with_credits = _make_test_usage(
        window="primary",
        used_percent=100.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 120),
        credits_has=True,
        credits_unlimited=False,
        credits_balance=10.0,
    )
    fresh_secondary_without_credits = _make_test_usage(
        window="secondary",
        used_percent=100.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
        credits_has=False,
        credits_unlimited=False,
        credits_balance=0.0,
    )

    state = _state_from_account(
        account=account,
        primary_entry=stale_primary_with_credits,
        secondary_entry=fresh_secondary_without_credits,
        runtime=RuntimeState(),
    )

    assert state.status == AccountStatus.QUOTA_EXCEEDED
    assert _extract_credit_status(stale_primary_with_credits, fresh_secondary_without_credits) == (
        False,
        False,
        0.0,
    )


def test_state_from_account_keeps_quota_exceeded_without_blocked_at_when_usage_stays_on_same_reset_window(
    monkeypatch,
):
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_clears_quota_exceeded_after_restart_with_persisted_blocked_at(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=future_reset,
        blocked_at=int(blocked),
    )
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.ACTIVE
    assert state.blocked_at is None


def test_state_from_account_keeps_quota_exceeded_after_restart_when_persisted_blocked_at_is_recent(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 60.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=future_reset,
        blocked_at=int(blocked),
    )
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_keeps_quota_exceeded_after_restart_when_secondary_usage_is_older_than_block(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=future_reset,
        blocked_at=int(blocked),
    )
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(blocked - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_clears_quota_exceeded_after_cooldown_expiry(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=runtime,
    )
    assert state.status == AccountStatus.ACTIVE


def test_state_from_account_keeps_quota_exceeded_during_active_cooldown(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 10.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 5),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now + 60.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=runtime,
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_keeps_quota_exceeded_when_usage_is_stale(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 60.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(blocked - 30),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=runtime,
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_keeps_quota_exceeded_when_no_usage_data(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=None,
        runtime=runtime,
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_rate_limited_checks_primary_freshness(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.RATE_LIMITED, reset_at=future_reset)
    stale_primary = _make_test_usage(
        window="primary",
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(blocked - 30),
    )
    fresh_secondary = _make_test_usage(
        window="secondary",
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=stale_primary,
        secondary_entry=fresh_secondary,
        runtime=runtime,
    )
    assert state.status == AccountStatus.RATE_LIMITED


def test_state_from_account_rate_limited_clears_with_fresh_primary(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.RATE_LIMITED, reset_at=future_reset)
    fresh_primary = _make_test_usage(
        window="primary",
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=fresh_primary,
        secondary_entry=None,
        runtime=runtime,
    )
    assert state.status == AccountStatus.ACTIVE


def test_background_recovery_state_preserves_rate_limit_cooldown_when_reset_is_in_future(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 300.0
    future_reset = int(now + 1500)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.RATE_LIMITED,
        reset_at=future_reset,
        blocked_at=int(blocked),
    )
    fresh_primary = _make_test_usage(
        window="primary",
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    state = background_recovery_state_from_account(
        account=account,
        primary_entry=fresh_primary,
        secondary_entry=None,
    )

    assert state.status == AccountStatus.RATE_LIMITED
    assert state.cooldown_until == pytest.approx(future_reset)


def test_background_recovery_state_recovers_rate_limited_after_reset_elapses(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 7200.0
    past_reset = int(now - 300)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.RATE_LIMITED,
        reset_at=past_reset,
        blocked_at=int(blocked),
    )
    fresh_primary = _make_test_usage(
        window="primary",
        used_percent=10.0,
        reset_at=past_reset,
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    state = background_recovery_state_from_account(
        account=account,
        primary_entry=fresh_primary,
        secondary_entry=None,
    )

    assert state.status == AccountStatus.ACTIVE


def test_background_recovery_state_recovers_monthly_only_rate_limited_after_reset_elapses(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(
        status=AccountStatus.RATE_LIMITED,
        reset_at=int(now - 10),
        plan_type="free",
    )
    fresh_monthly = _make_test_usage(
        window="monthly",
        used_percent=40.0,
        reset_at=int(now + 30 * 24 * 3600),
        recorded_at=_epoch_to_naive_utc(now - 30),
        window_minutes=43200,
    )

    state = background_recovery_state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=fresh_monthly,
    )

    assert state.status == AccountStatus.ACTIVE
    assert state.reset_at is None


def test_background_recovery_state_prefers_fresh_monthly_over_stale_primary(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 7200.0
    past_reset = int(now - 300)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(
        status=AccountStatus.RATE_LIMITED,
        reset_at=past_reset,
        blocked_at=int(blocked),
        plan_type="free",
    )
    stale_primary = _make_test_usage(
        window="primary",
        used_percent=100.0,
        reset_at=past_reset,
        recorded_at=_epoch_to_naive_utc(blocked - 30),
        window_minutes=43200,
    )
    fresh_monthly = _make_test_usage(
        window="monthly",
        used_percent=40.0,
        reset_at=int(now + 30 * 24 * 3600),
        recorded_at=_epoch_to_naive_utc(now - 30),
        window_minutes=43200,
    )

    state = background_recovery_state_from_account(
        account=account,
        primary_entry=stale_primary,
        secondary_entry=fresh_monthly,
    )

    assert state.status == AccountStatus.ACTIVE
    assert state.reset_at is None


def test_background_recovery_state_keeps_rate_limited_when_primary_predates_block(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 7200.0
    past_reset = int(now - 300)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.RATE_LIMITED,
        reset_at=past_reset,
        blocked_at=int(blocked),
    )
    stale_primary = _make_test_usage(
        window="primary",
        used_percent=10.0,
        reset_at=past_reset,
        recorded_at=_epoch_to_naive_utc(blocked - 30),
    )

    state = background_recovery_state_from_account(
        account=account,
        primary_entry=stale_primary,
        secondary_entry=None,
    )

    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at == pytest.approx(past_reset)
    assert state.blocked_at == pytest.approx(blocked)
    assert state.cooldown_until == pytest.approx(past_reset)


def test_background_recovery_state_keeps_rate_limited_without_persisted_reset(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 7200.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.RATE_LIMITED,
        reset_at=None,
        blocked_at=int(blocked),
    )
    fresh_primary = _make_test_usage(
        window="primary",
        used_percent=10.0,
        reset_at=int(now + 300),
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    state = background_recovery_state_from_account(
        account=account,
        primary_entry=fresh_primary,
        secondary_entry=None,
    )

    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at is None
    assert state.blocked_at == pytest.approx(blocked)


def test_state_from_account_uses_configured_drain_primary_threshold(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings",
        lambda: SimpleNamespace(
            soft_drain_enabled=True,
            drain_primary_threshold_pct=75.0,
            drain_secondary_threshold_pct=90.0,
            drain_error_window_seconds=60.0,
            drain_error_count_threshold=2,
            probe_quiet_seconds=60.0,
            probe_success_streak_required=3,
        ),
    )

    account = _make_test_account(status=AccountStatus.ACTIVE)
    primary = _make_test_usage(
        window="primary",
        used_percent=80.0,
        reset_at=int(now + 3600),
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    state = _state_from_account(
        account=account,
        primary_entry=primary,
        secondary_entry=None,
        runtime=RuntimeState(),
    )

    assert state.health_tier == 1


def test_state_from_account_uses_configured_probe_quiet_seconds(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings",
        lambda: SimpleNamespace(
            soft_drain_enabled=True,
            drain_primary_threshold_pct=85.0,
            drain_secondary_threshold_pct=90.0,
            drain_error_window_seconds=60.0,
            drain_error_count_threshold=2,
            probe_quiet_seconds=10.0,
            probe_success_streak_required=3,
        ),
    )

    account = _make_test_account(status=AccountStatus.ACTIVE)
    runtime = RuntimeState(
        health_tier=1,
        drain_entered_at=now - 11.0,
        probe_success_streak=0,
    )
    primary = _make_test_usage(
        window="primary",
        used_percent=50.0,
        reset_at=int(now + 3600),
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    state = _state_from_account(
        account=account,
        primary_entry=primary,
        secondary_entry=None,
        runtime=runtime,
    )

    assert state.health_tier == 2


def test_error_backoff_resets_error_count_when_expired():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        error_count=7,
        last_error_at=now - 400,
    )
    result = select_account([state], now=now)
    assert result.account is not None
    assert result.account.account_id == "a"
    assert state.error_count == 0
    assert state.last_error_at is None


def test_error_backoff_does_not_reset_when_still_active():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        error_count=5,
        last_error_at=now - 60,
    )
    result = select_account([state], now=now)
    assert result.account is None
    assert state.error_count == 5


def test_error_backoff_expired_account_does_not_immediately_relock():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        error_count=7,
        last_error_at=now - 400,
    )
    result = select_account([state], now=now)
    assert result.account is not None
    assert state.error_count == 0

    state.error_count = 2
    state.last_error_at = now + 1

    result2 = select_account([state], now=now + 2)
    assert result2.account is not None
    assert result2.account.account_id == "a"


@pytest.mark.asyncio
async def test_load_selection_inputs_parallelizes_usage_queries():
    """Verify that independent usage queries are parallelized with asyncio.gather()."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.modules.proxy.load_balancer import LoadBalancer

    # Create mock repositories
    mock_accounts_repo = AsyncMock()
    mock_accounts_repo.list_accounts = AsyncMock(return_value=[])

    mock_usage_repo = AsyncMock()

    async def slow_query():
        await asyncio.sleep(0.2)
        return {}

    mock_usage_repo.latest_by_account = AsyncMock(side_effect=slow_query)

    mock_repos = MagicMock()
    mock_repos.accounts = mock_accounts_repo
    mock_repos.usage = mock_usage_repo
    mock_repos.__aenter__ = AsyncMock(return_value=mock_repos)
    mock_repos.__aexit__ = AsyncMock(return_value=None)

    # Create LoadBalancer with mocked repo factory
    balancer = LoadBalancer(repo_factory=lambda: mock_repos)

    # Measure execution time
    start = time.time()
    result = await balancer._load_selection_inputs(model=None)
    elapsed = time.time() - start

    # If queries were sequential, elapsed would be ~0.4s (0.2 + 0.2)
    # If queries are parallel, elapsed should be ~0.2s
    # We use a generous threshold of 0.35s to account for test environment overhead
    assert elapsed < 0.35, f"Queries appear to be sequential (took {elapsed:.3f}s, expected <0.35s)"
    assert result.latest_primary == {}
    assert result.latest_secondary == {}


@pytest.mark.asyncio
async def test_load_selection_inputs_sets_burn_first_override_for_additional_quota():
    from app.modules.proxy.load_balancer import ROUTING_POLICY_BURN_FIRST, LoadBalancer

    async def _mocked_additional_filter(
        self,
        accounts: list[Account],
        *,
        model: str | None,
        limit_name: str,
        explicit_limit: bool,
        repos,
    ) -> _AdditionalLimitFilterResult:
        return _AdditionalLimitFilterResult(
            accounts=accounts,
            latest_primary={},
            latest_secondary={},
        )

    mock_accounts_repo = AsyncMock()
    mock_accounts_repo.list_accounts = AsyncMock(
        return_value=[_make_test_account(account_id="a", status=AccountStatus.ACTIVE)]
    )
    mock_repos = MagicMock()
    mock_repos.accounts = mock_accounts_repo
    mock_repos.usage.latest_by_account = AsyncMock(return_value={})
    mock_repos.additional_usage = AsyncMock()
    mock_repos.__aenter__ = AsyncMock(return_value=mock_repos)
    mock_repos.__aexit__ = AsyncMock(return_value=None)

    balancer = LoadBalancer(repo_factory=lambda: mock_repos)
    with pytest.MonkeyPatch().context() as monkeypatch:
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.LoadBalancer._filter_accounts_for_additional_limit",
            _mocked_additional_filter,
        )
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.get_settings_cache",
            lambda: SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(additional_quota_routing_policies_json='{"codex-spark":"burn_first"}')
                )
            ),
        )
        selection_inputs = await balancer._load_selection_inputs(model=None, additional_limit_name="codex-spark")

    states, _ = _build_states(
        accounts=selection_inputs.accounts,
        latest_primary=selection_inputs.latest_primary,
        latest_secondary=selection_inputs.latest_secondary,
        latest_monthly=selection_inputs.latest_monthly,
        runtime={},
        routing_policy_override=selection_inputs.routing_policy_override,
        ignore_standard_quota_account_ids=selection_inputs.ignore_standard_quota_account_ids,
    )

    assert selection_inputs.ignore_standard_quota_status is True
    assert selection_inputs.routing_policy_override == ROUTING_POLICY_BURN_FIRST
    assert states[0].routing_policy == ROUTING_POLICY_BURN_FIRST


@pytest.mark.asyncio
async def test_security_work_filter_preserves_additional_quota_metadata():
    from app.modules.proxy.load_balancer import LoadBalancer

    account = _make_test_account(account_id="acc-security", status=AccountStatus.QUOTA_EXCEEDED)
    account.security_work_authorized = True

    async def _mocked_additional_filter(
        self,
        accounts: list[Account],
        *,
        model: str | None,
        limit_name: str,
        explicit_limit: bool,
        repos,
    ) -> _AdditionalLimitFilterResult:
        return _AdditionalLimitFilterResult(
            accounts=accounts,
            latest_primary={},
            latest_secondary={},
        )

    mock_accounts_repo = AsyncMock()
    mock_accounts_repo.list_accounts = AsyncMock(return_value=[account])
    mock_repos = MagicMock()
    mock_repos.accounts = mock_accounts_repo
    mock_repos.usage.latest_by_account = AsyncMock(return_value={})
    mock_repos.__aenter__ = AsyncMock(return_value=mock_repos)
    mock_repos.__aexit__ = AsyncMock(return_value=None)

    balancer = LoadBalancer(repo_factory=lambda: mock_repos)
    with pytest.MonkeyPatch().context() as monkeypatch:
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.LoadBalancer._filter_accounts_for_additional_limit",
            _mocked_additional_filter,
        )
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.get_settings_cache",
            lambda: SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(additional_quota_routing_policies_json="{}"))
            ),
        )

        selection = await balancer.select_account(
            additional_limit_name="codex-spark",
            require_security_work_authorized=True,
        )

    assert selection.account is not None
    assert selection.account.id == account.id


@pytest.mark.asyncio
async def test_load_selection_inputs_uses_canonicalized_additional_quota_alias_key():
    from app.modules.proxy.load_balancer import ROUTING_POLICY_BURN_FIRST, LoadBalancer

    async def _mocked_additional_filter(
        self,
        accounts: list[Account],
        *,
        model: str | None,
        limit_name: str,
        explicit_limit: bool,
        repos,
    ) -> _AdditionalLimitFilterResult:
        return _AdditionalLimitFilterResult(
            accounts=accounts,
            latest_primary={},
            latest_secondary={},
        )

    mock_accounts_repo = AsyncMock()
    mock_accounts_repo.list_accounts = AsyncMock(
        return_value=[_make_test_account(account_id="a", status=AccountStatus.ACTIVE)]
    )
    mock_repos = MagicMock()
    mock_repos.accounts = mock_accounts_repo
    mock_repos.usage.latest_by_account = AsyncMock(return_value={})
    mock_repos.additional_usage = AsyncMock()
    mock_repos.__aenter__ = AsyncMock(return_value=mock_repos)
    mock_repos.__aexit__ = AsyncMock(return_value=None)

    balancer = LoadBalancer(repo_factory=lambda: mock_repos)
    with pytest.MonkeyPatch().context() as monkeypatch:
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.LoadBalancer._filter_accounts_for_additional_limit",
            _mocked_additional_filter,
        )
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.get_settings_cache",
            lambda: SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(additional_quota_routing_policies_json='{"codex-spark":"burn_first"}')
                )
            ),
        )
        selection_inputs = await balancer._load_selection_inputs(
            model=None,
            additional_limit_name="gpt-5.3-codex-spark",
        )

    states, _ = _build_states(
        accounts=selection_inputs.accounts,
        latest_primary=selection_inputs.latest_primary,
        latest_secondary=selection_inputs.latest_secondary,
        latest_monthly=selection_inputs.latest_monthly,
        runtime={},
        routing_policy_override=selection_inputs.routing_policy_override,
        ignore_standard_quota_account_ids=selection_inputs.ignore_standard_quota_account_ids,
    )

    assert selection_inputs.ignore_standard_quota_status is True
    assert selection_inputs.routing_policy_override == ROUTING_POLICY_BURN_FIRST
    assert states[0].routing_policy == ROUTING_POLICY_BURN_FIRST


@pytest.mark.asyncio
async def test_load_selection_inputs_uses_registry_additional_quota_routing_policy_by_default():
    from app.modules.proxy.load_balancer import ROUTING_POLICY_BURN_FIRST, LoadBalancer

    async def _mocked_additional_filter(
        self,
        accounts: list[Account],
        *,
        model: str | None,
        limit_name: str,
        explicit_limit: bool,
        repos,
    ) -> _AdditionalLimitFilterResult:
        return _AdditionalLimitFilterResult(
            accounts=accounts,
            latest_primary={},
            latest_secondary={},
        )

    mock_accounts_repo = AsyncMock()
    mock_accounts_repo.list_accounts = AsyncMock(
        return_value=[_make_test_account(account_id="a", status=AccountStatus.ACTIVE)]
    )
    mock_repos = MagicMock()
    mock_repos.accounts = mock_accounts_repo
    mock_repos.usage.latest_by_account = AsyncMock(return_value={})
    mock_repos.additional_usage = AsyncMock()
    mock_repos.__aenter__ = AsyncMock(return_value=mock_repos)
    mock_repos.__aexit__ = AsyncMock(return_value=None)

    balancer = LoadBalancer(repo_factory=lambda: mock_repos)
    with pytest.MonkeyPatch().context() as monkeypatch:
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.LoadBalancer._filter_accounts_for_additional_limit",
            _mocked_additional_filter,
        )
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.get_settings_cache",
            lambda: SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(additional_quota_routing_policies_json="{}"))
            ),
        )
        selection_inputs = await balancer._load_selection_inputs(model=None, additional_limit_name="codex-spark")

    states, _ = _build_states(
        accounts=selection_inputs.accounts,
        latest_primary=selection_inputs.latest_primary,
        latest_secondary=selection_inputs.latest_secondary,
        latest_monthly=selection_inputs.latest_monthly,
        runtime={},
        routing_policy_override=selection_inputs.routing_policy_override,
        ignore_standard_quota_account_ids=selection_inputs.ignore_standard_quota_account_ids,
    )

    assert selection_inputs.routing_policy_override == ROUTING_POLICY_BURN_FIRST
    assert states[0].routing_policy == ROUTING_POLICY_BURN_FIRST


@pytest.mark.asyncio
async def test_load_selection_inputs_inherits_account_policy_for_additional_quota_by_default():
    from app.modules.proxy.load_balancer import LoadBalancer

    async def _mocked_additional_filter(
        self,
        accounts: list[Account],
        *,
        model: str | None,
        limit_name: str,
        explicit_limit: bool,
        repos,
    ) -> _AdditionalLimitFilterResult:
        return _AdditionalLimitFilterResult(
            accounts=accounts,
            latest_primary={},
            latest_secondary={},
        )

    account = _make_test_account(account_id="a", status=AccountStatus.ACTIVE)
    account.routing_policy = "preserve"
    mock_accounts_repo = AsyncMock()
    mock_accounts_repo.list_accounts = AsyncMock(return_value=[account])
    mock_repos = MagicMock()
    mock_repos.accounts = mock_accounts_repo
    mock_repos.usage.latest_by_account = AsyncMock(return_value={})
    mock_repos.additional_usage = AsyncMock()
    mock_repos.__aenter__ = AsyncMock(return_value=mock_repos)
    mock_repos.__aexit__ = AsyncMock(return_value=None)

    balancer = LoadBalancer(repo_factory=lambda: mock_repos)
    with pytest.MonkeyPatch().context() as monkeypatch:
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.LoadBalancer._filter_accounts_for_additional_limit",
            _mocked_additional_filter,
        )
        monkeypatch.setattr(
            "app.modules.proxy.load_balancer.get_settings_cache",
            lambda: SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(additional_quota_routing_policies_json='{"codex-spark":"inherit"}')
                )
            ),
        )
        selection_inputs = await balancer._load_selection_inputs(model=None, additional_limit_name="codex-spark")

    states, _ = _build_states(
        accounts=selection_inputs.accounts,
        latest_primary=selection_inputs.latest_primary,
        latest_secondary=selection_inputs.latest_secondary,
        latest_monthly=selection_inputs.latest_monthly,
        runtime={},
        routing_policy_override=selection_inputs.routing_policy_override,
    )

    assert selection_inputs.routing_policy_override is None
    assert states[0].routing_policy == "preserve"


@pytest.mark.asyncio
async def test_load_selection_inputs_preserves_sync_quota_planner_settings():
    from unittest.mock import AsyncMock, MagicMock

    from app.modules.proxy.load_balancer import LoadBalancer
    from app.modules.quota_planner.logic import PlannerSettings

    planner_settings = PlannerSettings(mode="enforce", timezone="Asia/Tbilisi")
    mock_repos = MagicMock()
    mock_repos.accounts.list_accounts = AsyncMock(return_value=[])
    mock_repos.quota_planner.get_settings = lambda: planner_settings
    mock_repos.__aenter__ = AsyncMock(return_value=mock_repos)
    mock_repos.__aexit__ = AsyncMock(return_value=None)
    balancer = LoadBalancer(repo_factory=lambda: mock_repos)

    result = await balancer._load_selection_inputs(model="gpt-sync-planner-settings")

    assert result.quota_planner_settings is planner_settings


def test_select_account_capacity_weighted_pro_plus_same_usage_prefers_pro_by_capacity():
    random.seed(11)
    n = 2000
    pro = AccountState(
        "pro",
        AccountStatus.ACTIVE,
        used_percent=50.0,
        secondary_used_percent=10.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=50.0,
        secondary_used_percent=10.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts = {"pro": 0, "plus": 0}
    for _ in range(n):
        result = select_account([pro, plus], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    pro_ratio = counts["pro"] / n
    expected_pro_ratio = 50400.0 / (50400.0 + 7560.0)
    assert abs(pro_ratio - expected_pro_ratio) <= 0.05


def test_select_account_capacity_weighted_same_tier_lower_usage_selected_more():
    random.seed(22)
    n = 2000
    low_usage = AccountState(
        "plus-low",
        AccountStatus.ACTIVE,
        used_percent=20.0,
        secondary_used_percent=20.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )
    high_usage = AccountState(
        "plus-high",
        AccountStatus.ACTIVE,
        used_percent=80.0,
        secondary_used_percent=80.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts = {"plus-low": 0, "plus-high": 0}
    for _ in range(n):
        result = select_account([low_usage, high_usage], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    low_ratio = counts["plus-low"] / n
    expected_low_ratio = 0.8
    assert abs(low_ratio - expected_low_ratio) <= 0.05


def test_select_account_capacity_weighted_all_exhausted_falls_back_deterministically():
    a = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=60.0,
        secondary_used_percent=100.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )
    b = AccountState(
        "b",
        AccountStatus.ACTIVE,
        used_percent=40.0,
        secondary_used_percent=100.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )

    for _ in range(50):
        result = select_account([a, b], routing_strategy="capacity_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"


def test_select_account_capacity_weighted_single_account_always_selected():
    only = AccountState(
        "only",
        AccountStatus.ACTIVE,
        used_percent=77.0,
        secondary_used_percent=55.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    for _ in range(100):
        result = select_account([only], routing_strategy="capacity_weighted")
        assert result.account is not None
        assert result.account.account_id == "only"


def test_select_account_capacity_weighted_zero_capacity_treated_as_zero_weight():
    random.seed(33)
    zero_capacity = AccountState(
        "zero-capacity",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        plan_type="plus",
        capacity_credits=0.0,
    )
    weighted = AccountState(
        "weighted",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    for _ in range(200):
        result = select_account([zero_capacity, weighted], routing_strategy="capacity_weighted")
        assert result.account is not None
        assert result.account.account_id == "weighted"


def test_select_account_capacity_weighted_unknown_plan_uses_conservative_fallback_weight():
    random.seed(34)
    n = 2000
    unknown_plan = AccountState(
        "unknown-plan",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        plan_type="unknown",
        capacity_credits=None,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts = {"unknown-plan": 0, "plus": 0}
    for _ in range(n):
        result = select_account([unknown_plan, plus], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    unknown_ratio = counts["unknown-plan"] / n
    assert 0.05 <= unknown_ratio <= 0.25
    assert counts["plus"] > counts["unknown-plan"]


@pytest.mark.parametrize("plan_type", ["pro", "prolite", "team", "business", "enterprise"])
def test_additional_quota_applies_to_quota_enforced_and_unmapped_plans(plan_type):
    assert _additional_quota_applies_to_plan(quota_key="codex_spark", plan_type=plan_type) is True


def test_additional_quota_applies_conservatively_when_plan_is_missing():
    assert _additional_quota_applies_to_plan(quota_key="codex_spark", plan_type=None) is True


def test_additional_quota_applies_conservatively_when_plan_is_unknown():
    assert _additional_quota_applies_to_plan(quota_key="codex_spark", plan_type="unknown") is True


@pytest.mark.parametrize("plan_type", ["free", "plus", "edu"])
def test_additional_quota_does_not_apply_to_known_non_additional_quota_plans(plan_type):
    assert _additional_quota_applies_to_plan(quota_key="codex_spark", plan_type=plan_type) is False


def test_select_account_capacity_weighted_education_alias_uses_edu_capacity():
    random.seed(35)
    n = 2000
    education = AccountState(
        "education",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        plan_type="education",
        capacity_credits=None,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts = {"education": 0, "plus": 0}
    for _ in range(n):
        result = select_account([education, plus], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    education_ratio = counts["education"] / n
    assert 0.45 <= education_ratio <= 0.55


def test_select_account_capacity_weighted_three_tiers_distribution_matches_capacity():
    random.seed(44)
    n = 2000
    pro = AccountState(
        "pro",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=0.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=0.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )
    free = AccountState(
        "free",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=0.0,
        plan_type="free",
        capacity_credits=1134.0,
    )

    counts = {"pro": 0, "plus": 0, "free": 0}
    for _ in range(n):
        result = select_account([pro, plus, free], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    pro_ratio = counts["pro"] / n
    plus_ratio = counts["plus"] / n
    free_ratio = counts["free"] / n
    total_capacity = 50400.0 + 7560.0 + 1134.0

    assert abs(pro_ratio - (50400.0 / total_capacity)) <= 0.05
    assert abs(plus_ratio - (7560.0 / total_capacity)) <= 0.05
    assert abs(free_ratio - (1134.0 / total_capacity)) <= 0.05
    assert pro_ratio > plus_ratio > free_ratio


def test_select_account_capacity_weighted_prefers_earlier_reset_bucket():
    random.seed(55)
    now = time.time()
    early = AccountState(
        "early",
        AccountStatus.ACTIVE,
        used_percent=80.0,
        secondary_used_percent=80.0,
        secondary_reset_at=int(now + 2 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    late = AccountState(
        "late",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        secondary_reset_at=int(now + 4 * 24 * 3600),
        plan_type="pro",
        capacity_credits=50400.0,
    )

    for _ in range(100):
        result = select_account(
            [early, late],
            now=now,
            prefer_earlier_reset=True,
            routing_strategy="capacity_weighted",
        )
        assert result.account is not None
        assert result.account.account_id == "early"


def test_all_primary_pressured_fallback_skips_unavailable_account():
    now = time.time()
    states = [
        AccountState(
            "blocked",
            AccountStatus.ACTIVE,
            used_percent=96.0,
            secondary_used_percent=5.0,
            cooldown_until=now + 60,
        ),
        AccountState(
            "available",
            AccountStatus.ACTIVE,
            used_percent=98.0,
            secondary_used_percent=90.0,
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "available"


def test_budget_safe_selection_preserves_secondary_first_when_all_primary_safe():
    states = [
        AccountState("secondary-high", AccountStatus.ACTIVE, used_percent=10.0, secondary_used_percent=90.0),
        AccountState("secondary-low", AccountStatus.ACTIVE, used_percent=20.0, secondary_used_percent=1.0),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "secondary-low"


def test_all_primary_pressured_fallback_prefers_healthy_over_draining():
    states = [
        AccountState(
            "draining",
            AccountStatus.ACTIVE,
            used_percent=96.0,
            secondary_used_percent=5.0,
            health_tier=1,
        ),
        AccountState(
            "healthy",
            AccountStatus.ACTIVE,
            used_percent=98.0,
            secondary_used_percent=90.0,
            health_tier=0,
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "healthy"


def test_primary_pressured_fallback_ignores_unavailable_safe_accounts():
    states = [
        AccountState(
            "safe-but-exhausted",
            AccountStatus.QUOTA_EXCEEDED,
            used_percent=10.0,
            secondary_used_percent=10.0,
        ),
        AccountState(
            "higher-primary",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=1.0,
        ),
        AccountState(
            "lower-primary",
            AccountStatus.ACTIVE,
            used_percent=96.0,
            secondary_used_percent=99.0,
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "lower-primary"


def test_primary_pressured_fallback_preserves_additional_quota_standard_ignore():
    states = [
        AccountState(
            "additional-quota-available",
            AccountStatus.QUOTA_EXCEEDED,
            used_percent=96.0,
            secondary_used_percent=97.0,
            reset_at=int(time.time() + 3600),
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
        ignore_standard_quota=True,
    )

    assert result.account is not None
    assert result.account.account_id == "additional-quota-available"


def test_all_primary_pressured_fallback_honors_primary_reset_preference():
    now = time.time()
    states = [
        AccountState(
            "late",
            AccountStatus.ACTIVE,
            used_percent=98.0,
            secondary_used_percent=50.0,
            primary_reset_at=int(now + 4 * 3600),
            secondary_reset_at=int(now + 3600),
        ),
        AccountState(
            "early",
            AccountStatus.ACTIVE,
            used_percent=98.0,
            secondary_used_percent=50.0,
            primary_reset_at=int(now + 30 * 60),
            secondary_reset_at=int(now + 7 * 24 * 3600),
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=True,
        prefer_earlier_reset_window="primary",
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "early"


def test_drain_budget_safe_selection_filters_over_threshold_accounts():
    states = [
        AccountState(
            "over-budget-lowest-capacity",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=10.0,
        ),
        AccountState(
            "under-budget",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="sequential_drain",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "under-budget"


def test_burn_first_selection_honors_primary_reset_preference():
    now = time.time()
    states = [
        AccountState(
            "secondary-early-primary-late",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            primary_reset_at=int(now + 6 * 3600),
            secondary_reset_at=int(now + 30 * 60),
            routing_policy="burn_first",
        ),
        AccountState(
            "primary-early-secondary-late",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            primary_reset_at=int(now + 30 * 60),
            secondary_reset_at=int(now + 6 * 3600),
            routing_policy="burn_first",
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=True,
        prefer_earlier_reset_window="primary",
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "primary-early-secondary-late"


def test_primary_pressured_fallback_honors_reset_bucket_before_primary_usage():
    now = time.time()
    states = [
        AccountState(
            "earlier-reset-higher-primary",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=1.0,
            secondary_reset_at=int(now + 3600),
        ),
        AccountState(
            "later-reset-lower-primary",
            AccountStatus.ACTIVE,
            used_percent=96.0,
            secondary_used_percent=99.0,
            secondary_reset_at=int(now + 7 * 24 * 3600),
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=True,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "earlier-reset-higher-primary"


def test_sticky_budget_threshold_still_counts_secondary_pressure():
    state = AccountState(
        "sticky",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=99.0,
    )

    assert _state_above_sticky_budget_threshold(state, 95.0) is True


def test_select_account_uses_requested_limit_usage_before_account_usage():
    states = [
        AccountState(
            "account-high-limit-low",
            AccountStatus.ACTIVE,
            used_percent=90.0,
            secondary_used_percent=90.0,
            priority_used_percent=10.0,
            priority_secondary_used_percent=10.0,
        ),
        AccountState(
            "account-low-limit-high",
            AccountStatus.ACTIVE,
            used_percent=20.0,
            secondary_used_percent=20.0,
            priority_used_percent=80.0,
            priority_secondary_used_percent=80.0,
        ),
    ]

    result = select_account(states, routing_strategy="usage_weighted")

    assert result.account is not None
    assert result.account.account_id == "account-high-limit-low"


def test_select_account_uses_requested_limit_reset_for_burn_first():
    now = time.time()
    states = [
        AccountState(
            "base-early-limit-late",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 3600),
            priority_used_percent=10.0,
            priority_secondary_used_percent=10.0,
            priority_reset_at=int(now + 5 * 24 * 3600),
        ),
        AccountState(
            "base-late-limit-early",
            AccountStatus.ACTIVE,
            used_percent=90.0,
            secondary_used_percent=90.0,
            secondary_reset_at=int(now + 5 * 24 * 3600),
            priority_used_percent=90.0,
            priority_secondary_used_percent=90.0,
            priority_reset_at=int(now + 3600),
        ),
    ]

    result = select_account(
        states,
        now=now,
        prefer_earlier_reset=True,
        routing_strategy="usage_weighted",
    )

    assert result.account is not None
    assert result.account.account_id == "base-late-limit-early"


def test_budget_safe_selection_uses_requested_limit_pressure():
    states = [
        AccountState(
            "base-pressured-limit-safe",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=99.0,
            priority_used_percent=10.0,
            priority_secondary_used_percent=10.0,
        ),
        AccountState(
            "base-safe-limit-pressured",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            priority_used_percent=99.0,
            priority_secondary_used_percent=99.0,
        ),
    ]

    result = _select_account_preferring_budget_safe(
        states,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "base-pressured-limit-safe"


def test_sticky_budget_threshold_uses_requested_limit_pressure():
    state = AccountState(
        "sticky",
        AccountStatus.ACTIVE,
        used_percent=99.0,
        secondary_used_percent=99.0,
        priority_used_percent=10.0,
        priority_secondary_used_percent=10.0,
    )

    assert _state_above_sticky_budget_threshold(state, 95.0) is False


def test_select_account_capacity_weighted_prefers_capacity_within_same_reset_bucket():
    random.seed(66)
    n = 2000
    now = time.time()
    pro = AccountState(
        "pro",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        secondary_reset_at=int(now + 3 * 3600),
        plan_type="pro",
        capacity_credits=50400.0,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        secondary_reset_at=int(now + 2 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    late = AccountState(
        "late",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 5 * 24 * 3600),
        plan_type="enterprise",
        capacity_credits=50400.0,
    )

    counts = {"pro": 0, "plus": 0, "late": 0}
    for _ in range(n):
        result = select_account(
            [pro, plus, late],
            now=now,
            prefer_earlier_reset=True,
            routing_strategy="capacity_weighted",
        )
        assert result.account is not None
        counts[result.account.account_id] += 1

    assert counts["late"] == 0
    pro_ratio = counts["pro"] / n
    expected_pro_ratio = 50400.0 / (50400.0 + 7560.0)
    assert abs(pro_ratio - expected_pro_ratio) <= 0.05


def test_select_account_capacity_weighted_preserves_sampling_with_equal_planner_costs():
    random.seed(67)
    n = 2000
    pro = AccountState(
        "pro",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts = {"pro": 0, "plus": 0}
    for _ in range(n):
        result = select_account(
            [pro, plus],
            routing_strategy="capacity_weighted",
            routing_costs={
                "pro": RoutingCost(total=1.0, reason="same_planner_cost"),
                "plus": RoutingCost(total=1.0, reason="same_planner_cost"),
            },
        )
        assert result.account is not None
        counts[result.account.account_id] += 1

    pro_ratio = counts["pro"] / n
    expected_pro_ratio = 50400.0 / (50400.0 + 7560.0)
    assert abs(pro_ratio - expected_pro_ratio) <= 0.05


def test_select_account_capacity_weighted_filters_higher_planner_costs_before_sampling():
    random.seed(68)
    low_cost_plus = AccountState(
        "low-cost-plus",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )
    high_cost_pro = AccountState(
        "high-cost-pro",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )

    for _ in range(100):
        result = select_account(
            [low_cost_plus, high_cost_pro],
            routing_strategy="capacity_weighted",
            routing_costs={
                "low-cost-plus": RoutingCost(total=1.0, reason="inside_work"),
                "high-cost-pro": RoutingCost(total=5.0, reason="cold_start"),
            },
        )
        assert result.account is not None
        assert result.account.account_id == "low-cost-plus"


def test_select_account_capacity_weighted_with_prefer_deprioritizes_missing_reset():
    random.seed(77)
    now = time.time()
    missing_reset = AccountState(
        "missing-reset",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=None,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    known_reset = AccountState(
        "known-reset",
        AccountStatus.ACTIVE,
        used_percent=95.0,
        secondary_used_percent=95.0,
        secondary_reset_at=int(now + 2 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )

    for _ in range(100):
        result = select_account(
            [missing_reset, known_reset],
            now=now,
            prefer_earlier_reset=True,
            routing_strategy="capacity_weighted",
        )
        assert result.account is not None
        assert result.account.account_id == "known-reset"


def test_select_account_capacity_weighted_with_prefer_falls_back_when_earliest_bucket_zero_weight():
    random.seed(88)
    now = time.time()
    earliest_high_usage = AccountState(
        "earliest-high-usage",
        AccountStatus.ACTIVE,
        used_percent=30.0,
        secondary_used_percent=100.0,
        secondary_reset_at=int(now + 2 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    earliest_lower_usage = AccountState(
        "earliest-lower-usage",
        AccountStatus.ACTIVE,
        used_percent=20.0,
        secondary_used_percent=100.0,
        secondary_reset_at=int(now + 3 * 3600),
        plan_type="pro",
        capacity_credits=50400.0,
    )
    later_healthy = AccountState(
        "later-healthy",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 3 * 24 * 3600),
        plan_type="enterprise",
        capacity_credits=50400.0,
    )

    for _ in range(100):
        result = select_account(
            [earliest_high_usage, earliest_lower_usage, later_healthy],
            now=now,
            prefer_earlier_reset=True,
            routing_strategy="capacity_weighted",
        )
        assert result.account is not None
        assert result.account.account_id == "earliest-lower-usage"


def test_apply_usage_quota_allows_secondary_100_when_credits_exist():
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=11.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=None,
        secondary_used=100.0,
        secondary_reset=1_700_010_000,
        credits_has=True,
        credits_unlimited=False,
        credits_balance=959.0,
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 11.0
    assert reset_at is None


def test_apply_usage_quota_keeps_primary_100_rate_limited_with_credits():
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=100.0,
        primary_reset=1_700_005_000,
        primary_window_minutes=None,
        runtime_reset=None,
        secondary_used=100.0,
        secondary_reset=1_700_010_000,
        credits_has=True,
        credits_unlimited=False,
        credits_balance=959.0,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 100.0
    assert reset_at == 1_700_005_000


def test_apply_usage_quota_keeps_primary_100_rate_limited_without_credits():
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=100.0,
        primary_reset=1_700_005_000,
        primary_window_minutes=None,
        runtime_reset=None,
        secondary_used=99.0,
        secondary_reset=1_700_010_000,
        credits_has=False,
        credits_unlimited=False,
        credits_balance=0.0,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 100.0
    assert reset_at == 1_700_005_000


def test_apply_usage_quota_preserves_rate_limited_runtime_reset_when_credits_balance_positive(monkeypatch):
    now = 1_700_000_000.0
    future = now + 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=99.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=future,
        secondary_used=100.0,
        secondary_reset=1_700_010_000,
        credits_has=None,
        credits_unlimited=None,
        credits_balance=1.0,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 99.0
    assert reset_at == future


def test_apply_usage_quota_preserves_rate_limited_when_status_rate_limited_with_primary_100_and_credits(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    future = now + 120.0

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=100.0,
        primary_reset=1_700_005_000,
        primary_window_minutes=None,
        runtime_reset=future,
        secondary_used=None,
        secondary_reset=None,
        credits_has=True,
        credits_unlimited=False,
        credits_balance=959.0,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 100.0
    assert reset_at == 1_700_005_000


def test_apply_usage_quota_clears_quota_exceeded_when_credits_balance_positive(monkeypatch):
    now = 1_700_000_000.0
    future = now + 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.QUOTA_EXCEEDED,
        primary_used=20.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=future,
        secondary_used=100.0,
        secondary_reset=1_700_010_000,
        credits_has=None,
        credits_unlimited=None,
        credits_balance=1.0,
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 20.0
    assert reset_at is None


def test_select_account_relative_availability_prefers_more_urgent_weekly_capacity():
    random.seed(101)
    now = time.time()
    soon_plus = AccountState(
        "soon-plus",
        AccountStatus.ACTIVE,
        used_percent=60.0,
        secondary_used_percent=60.0,
        secondary_reset_at=int(now + 6 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    later_pro = AccountState(
        "later-pro",
        AccountStatus.ACTIVE,
        used_percent=60.0,
        secondary_used_percent=60.0,
        secondary_reset_at=int(now + 72 * 3600),
        plan_type="pro",
        capacity_credits=50400.0,
    )

    counts = {"soon-plus": 0, "later-pro": 0}
    for _ in range(2000):
        result = select_account([soon_plus, later_pro], now=now, routing_strategy="relative_availability")
        assert result.account is not None
        counts[result.account.account_id] += 1

    assert counts["soon-plus"] > counts["later-pro"]


def test_select_account_relative_availability_filters_higher_planner_costs():
    now = time.time()
    low_cost = AccountState(
        "low-cost",
        AccountStatus.ACTIVE,
        used_percent=90.0,
        secondary_used_percent=90.0,
        secondary_reset_at=int(now + 24 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    high_cost_urgent = AccountState(
        "high-cost-urgent",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 3600),
        plan_type="pro",
        capacity_credits=50400.0,
    )

    result = select_account(
        [low_cost, high_cost_urgent],
        now=now,
        routing_strategy="relative_availability",
        deterministic_probe=True,
        routing_costs={
            "low-cost": RoutingCost(total=1.0, reason="budget_safe"),
            "high-cost-urgent": RoutingCost(total=9.0, reason="expensive"),
        },
    )
    assert result.account is not None
    assert result.account.account_id == "low-cost"


def test_select_account_relative_availability_ignores_prefer_earlier_reset_bucket():
    now = time.time()
    early_low = AccountState(
        "early-low",
        AccountStatus.ACTIVE,
        used_percent=90.0,
        secondary_used_percent=90.0,
        secondary_reset_at=int(now + 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    later_high = AccountState(
        "later-high",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 6 * 3600),
        plan_type="pro",
        capacity_credits=50400.0,
    )

    result = select_account(
        [early_low, later_high],
        now=now,
        prefer_earlier_reset=True,
        routing_strategy="relative_availability",
        deterministic_probe=True,
    )
    assert result.account is not None
    assert result.account.account_id == "later-high"


def test_select_account_relative_availability_missing_reset_uses_seven_day_fallback():
    now = time.time()
    missing_reset = AccountState(
        "missing-reset",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=None,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    known_reset = AccountState(
        "known-reset",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 24 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )

    result = select_account(
        [missing_reset, known_reset],
        now=now,
        routing_strategy="relative_availability",
        deterministic_probe=True,
    )
    assert result.account is not None
    assert result.account.account_id == "known-reset"


def test_select_account_relative_availability_clamps_divisor_floor_to_five_minutes():
    now = time.time()
    first = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 5),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    second = AccountState(
        "b",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 299),
        plan_type="plus",
        capacity_credits=7560.0,
    )

    result = select_account(
        [first, second],
        now=now,
        routing_strategy="relative_availability",
        deterministic_probe=True,
    )
    assert result.account is not None
    assert result.account.account_id == "a"


def test_select_account_fill_first_picks_highest_primary_used_percent():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=30.0),
        AccountState("b", AccountStatus.ACTIVE, used_percent=5.0),
        AccountState("c", AccountStatus.ACTIVE, used_percent=0.0),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "a"


def test_select_account_fill_first_breaks_ties_by_account_id():
    now = 1_700_000_000.0
    states = [
        AccountState("zeta", AccountStatus.ACTIVE, used_percent=0.0),
        AccountState("alpha", AccountStatus.ACTIVE, used_percent=0.0),
        AccountState("mike", AccountStatus.ACTIVE, used_percent=0.0),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "alpha"


def test_select_account_fill_first_treats_none_used_percent_as_zero():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=10.0),
        AccountState("b", AccountStatus.ACTIVE, used_percent=None),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "a"


def test_select_account_ordered_fallback_uses_manual_priority_order():
    states = [
        AccountState("low-usage", AccountStatus.ACTIVE, used_percent=1.0),
        AccountState("preferred", AccountStatus.ACTIVE, used_percent=80.0),
        AccountState("backup", AccountStatus.ACTIVE, used_percent=20.0),
    ]

    result = select_account(
        states,
        routing_strategy="ordered_fallback",
        ordered_account_ids=("preferred", "backup", "low-usage"),
    )

    assert result.account is not None
    assert result.account.account_id == "preferred"


def test_select_account_ordered_fallback_skips_exhausted_and_unlisted_accounts():
    states = [
        AccountState("exhausted", AccountStatus.ACTIVE, used_percent=100.0),
        AccountState("backup", AccountStatus.ACTIVE, used_percent=20.0),
        AccountState("unlisted", AccountStatus.ACTIVE, used_percent=1.0),
    ]

    result = select_account(
        states,
        routing_strategy="ordered_fallback",
        ordered_account_ids=("exhausted", "backup"),
    )

    assert result.account is not None
    assert result.account.account_id == "backup"


def test_select_account_ordered_fallback_denies_when_no_ordered_account_available():
    states = [
        AccountState("exhausted", AccountStatus.ACTIVE, used_percent=100.0),
        AccountState("unlisted", AccountStatus.ACTIVE, used_percent=1.0),
    ]

    result = select_account(
        states,
        routing_strategy="ordered_fallback",
        ordered_account_ids=("exhausted", "missing"),
    )

    assert result.account is None
    assert result.error_message == "No ordered fallback accounts are available"


def test_select_account_fill_first_is_deterministic_across_calls():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=12.0),
        AccountState("b", AccountStatus.ACTIVE, used_percent=4.0),
        AccountState("c", AccountStatus.ACTIVE, used_percent=80.0),
    ]
    selections: set[str] = set()
    for _ in range(50):
        result = select_account(states, now=now, routing_strategy="fill_first")
        assert result.account is not None
        selections.add(result.account.account_id)
    assert selections == {"c"}


def test_select_account_fill_first_skips_rate_limited_account():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.RATE_LIMITED, used_percent=0.0, reset_at=int(now + 60)),
        AccountState("b", AccountStatus.ACTIVE, used_percent=5.0),
        AccountState("c", AccountStatus.ACTIVE, used_percent=20.0),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "c"


def test_select_account_fill_first_skips_quota_exceeded_account():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "a",
            AccountStatus.QUOTA_EXCEEDED,
            used_percent=100.0,
            reset_at=int(now + 3600),
            cooldown_until=now + 60,
        ),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
        AccountState("c", AccountStatus.ACTIVE, used_percent=70.0),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "c"


def test_select_account_fill_first_prefers_healthy_over_draining():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "draining-low",
            AccountStatus.ACTIVE,
            used_percent=1.0,
            health_tier=1,
        ),
        AccountState(
            "healthy-mid",
            AccountStatus.ACTIVE,
            used_percent=40.0,
            health_tier=0,
        ),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "healthy-mid"


def test_select_account_fill_first_falls_back_to_draining_when_no_healthy():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "draining-low",
            AccountStatus.ACTIVE,
            used_percent=1.0,
            health_tier=1,
        ),
        AccountState(
            "draining-mid",
            AccountStatus.ACTIVE,
            used_percent=40.0,
            health_tier=1,
        ),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "draining-mid"


def test_select_account_fill_first_prefer_earlier_reset_filters_pool():
    now = 1_700_000_000.0
    early_high_usage = AccountState(
        "early-high",
        AccountStatus.ACTIVE,
        used_percent=80.0,
        secondary_reset_at=int(now + 2 * 3600),
    )
    early_low_usage = AccountState(
        "early-low",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_reset_at=int(now + 3 * 3600),
    )
    late_zero_usage = AccountState(
        "late-zero",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_reset_at=int(now + 5 * 24 * 3600),
    )
    result = select_account(
        [early_high_usage, early_low_usage, late_zero_usage],
        now=now,
        prefer_earlier_reset=True,
        routing_strategy="fill_first",
    )
    assert result.account is not None
    assert result.account.account_id == "early-high"


def test_select_account_fill_first_returns_no_available_when_pool_empty():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.PAUSED),
        AccountState("b", AccountStatus.DEACTIVATED),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is None
    assert result.error_message is not None


def test_select_account_fill_first_cycle_after_account_drops_out():
    now = 1_700_000_000.0
    a = AccountState("a", AccountStatus.ACTIVE, used_percent=0.0)
    b = AccountState("b", AccountStatus.ACTIVE, used_percent=0.0)
    c = AccountState("c", AccountStatus.ACTIVE, used_percent=0.0)
    states = [a, b, c]

    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "a"

    a.used_percent = 50.0
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "a"

    a.status = AccountStatus.RATE_LIMITED
    a.reset_at = int(now + 600)
    b.used_percent = 60.0
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "b"

    a.status = AccountStatus.ACTIVE
    a.used_percent = 0.0
    a.reset_at = None
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_relative_availability_top_k_limits_weighted_draw():
    random.seed(202)
    now = time.time()
    leader = AccountState(
        "leader",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    runner_up = AccountState(
        "runner-up",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 2 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    tail = AccountState(
        "tail",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 3 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )

    for _ in range(200):
        result = select_account(
            [leader, runner_up, tail],
            now=now,
            routing_strategy="relative_availability",
            relative_availability_top_k=1,
        )
        assert result.account is not None
        assert result.account.account_id == "leader"


def test_select_account_relative_availability_power_sharpens_preference_for_the_leader():
    now = time.time()
    leader = AccountState(
        "leader",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    close_second = AccountState(
        "close-second",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 4500),
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts_power_1 = {"leader": 0, "close-second": 0}
    random.seed(303)
    for _ in range(3000):
        result = select_account(
            [leader, close_second],
            now=now,
            routing_strategy="relative_availability",
            relative_availability_power=1.0,
        )
        assert result.account is not None
        counts_power_1[result.account.account_id] += 1

    counts_power_4 = {"leader": 0, "close-second": 0}
    random.seed(303)
    for _ in range(3000):
        result = select_account(
            [leader, close_second],
            now=now,
            routing_strategy="relative_availability",
            relative_availability_power=4.0,
        )
        assert result.account is not None
        counts_power_4[result.account.account_id] += 1

    leader_ratio_power_1 = counts_power_1["leader"] / 3000
    leader_ratio_power_4 = counts_power_4["leader"] / 3000
    assert leader_ratio_power_4 > leader_ratio_power_1 + 0.05


def test_select_account_fill_first_secondary_used_breaks_primary_tie_high_first():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "alpha",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=29.0,
        ),
        AccountState(
            "bravo",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=98.0,
        ),
        AccountState(
            "charlie",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=93.0,
        ),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    # bravo has the highest secondary_used (98%), so least remaining weekly
    # capacity, so it gets drained first.
    assert result.account.account_id == "bravo"


def test_select_account_fill_first_secondary_tie_falls_back_to_account_id():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "zeta",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=98.0,
        ),
        AccountState(
            "alpha",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=98.0,
        ),
        AccountState(
            "mike",
            AccountStatus.ACTIVE,
            used_percent=99.0,
            secondary_used_percent=98.0,
        ),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    assert result.account.account_id == "alpha"


def test_select_account_fill_first_secondary_none_treated_as_zero():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            secondary_used_percent=None,
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            secondary_used_percent=10.0,
        ),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    # b has secondary 10% > a's None-as-0, so b drains first.
    assert result.account.account_id == "b"


def test_select_account_fill_first_primary_dominates_over_secondary():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "high-secondary",
            AccountStatus.ACTIVE,
            used_percent=80.0,
            secondary_used_percent=99.0,
        ),
        AccountState(
            "low-primary",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=5.0,
        ),
    ]
    result = select_account(states, now=now, routing_strategy="fill_first")
    assert result.account is not None
    # Primary still wins -- only ties break on secondary.
    assert result.account.account_id == "high-secondary"
