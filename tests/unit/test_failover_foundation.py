from __future__ import annotations

import pytest

from app.core.balancer.logic import (
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    AccountState,
    evaluate_health_tier,
    failover_decision,
    select_account,
)
from app.core.balancer.types import UpstreamError
from app.db.models import AccountStatus
from app.modules.proxy.helpers import classify_upstream_failure

pytestmark = pytest.mark.unit


class TestClassifyUpstreamFailure:
    def test_rate_limit_exceeded(self) -> None:
        result = classify_upstream_failure(
            error_code="rate_limit_exceeded",
            error=UpstreamError(message="Try again in 1.5s"),
            http_status=429,
            phase="connect",
        )
        assert result["failure_class"] == "rate_limit"
        assert result["phase"] == "connect"

    def test_usage_limit_reached(self) -> None:
        result = classify_upstream_failure(
            error_code="usage_limit_reached",
            error=UpstreamError(message="Usage limit"),
            http_status=429,
            phase="first_event",
        )
        assert result["failure_class"] == "rate_limit"

    def test_insufficient_quota(self) -> None:
        result = classify_upstream_failure(
            error_code="insufficient_quota",
            error=UpstreamError(message="Quota exceeded"),
            http_status=429,
            phase="connect",
        )
        assert result["failure_class"] == "quota"

    def test_quota_exceeded(self) -> None:
        result = classify_upstream_failure(
            error_code="quota_exceeded",
            error=UpstreamError(message=""),
            http_status=429,
            phase="connect",
        )
        assert result["failure_class"] == "quota"

    def test_usage_not_included(self) -> None:
        result = classify_upstream_failure(
            error_code="usage_not_included",
            error=UpstreamError(message=""),
            http_status=403,
            phase="first_event",
        )
        assert result["failure_class"] == "quota"

    def test_server_error(self) -> None:
        result = classify_upstream_failure(
            error_code="server_error",
            error=UpstreamError(message="Internal error"),
            http_status=500,
            phase="mid_stream",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_http_500_unknown_code(self) -> None:
        result = classify_upstream_failure(
            error_code="unknown_thing",
            error=UpstreamError(message=""),
            http_status=500,
            phase="connect",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_http_502(self) -> None:
        result = classify_upstream_failure(
            error_code="bad_gateway",
            error=UpstreamError(message=""),
            http_status=502,
            phase="connect",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_non_retryable_bad_request(self) -> None:
        result = classify_upstream_failure(
            error_code="invalid_request",
            error=UpstreamError(message="Bad request"),
            http_status=400,
            phase="connect",
        )
        assert result["failure_class"] == "non_retryable"

    def test_non_retryable_auth(self) -> None:
        result = classify_upstream_failure(
            error_code="authentication_error",
            error=UpstreamError(message=""),
            http_status=401,
            phase="connect",
        )
        assert result["failure_class"] == "non_retryable"

    def test_preserves_error_payload(self) -> None:
        error: UpstreamError = {"message": "Try again", "resets_at": 1234567890}
        result = classify_upstream_failure(
            error_code="rate_limit_exceeded",
            error=error,
            http_status=429,
            phase="connect",
        )
        assert result["error"] is error
        assert result["http_status"] == 429
        assert result["error_code"] == "rate_limit_exceeded"

    def test_stream_incomplete_is_transient(self) -> None:
        result = classify_upstream_failure(
            error_code="stream_incomplete",
            error=UpstreamError(message=""),
            http_status=None,
            phase="mid_stream",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_upstream_error_is_transient(self) -> None:
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message=""),
            http_status=None,
            phase="connect",
        )
        assert result["failure_class"] == "retryable_transient"


class TestFailoverDecision:
    def test_surface_when_downstream_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="rate_limit",
                downstream_visible=True,
                candidates_remaining=5,
            )
            == "surface"
        )

    def test_surface_when_no_candidates(self) -> None:
        assert (
            failover_decision(
                failure_class="rate_limit",
                downstream_visible=False,
                candidates_remaining=0,
            )
            == "surface"
        )

    def test_failover_rate_limit_pre_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="rate_limit",
                downstream_visible=False,
                candidates_remaining=2,
            )
            == "failover_next"
        )

    def test_failover_quota_pre_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="quota",
                downstream_visible=False,
                candidates_remaining=1,
            )
            == "failover_next"
        )

    def test_failover_transient_pre_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=False,
                candidates_remaining=1,
            )
            == "failover_next"
        )

    def test_surface_non_retryable_pre_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="non_retryable",
                downstream_visible=False,
                candidates_remaining=5,
            )
            == "surface"
        )

    def test_visible_overrides_everything(self) -> None:
        for fc in ("rate_limit", "quota", "retryable_transient", "non_retryable"):
            assert (
                failover_decision(
                    failure_class=fc,
                    downstream_visible=True,
                    candidates_remaining=10,
                )
                == "surface"
            )


class TestEvaluateHealthTier:
    def _make_state(self, *, health_tier: int = 0, **kwargs) -> AccountState:  # type: ignore[no-untyped-def]
        defaults: dict = {
            "account_id": "test",
            "status": AccountStatus.ACTIVE,
            "health_tier": health_tier,
        }
        defaults.update(kwargs)
        return AccountState(**defaults)

    def test_healthy_stays_healthy_low_usage(self) -> None:
        state = self._make_state(used_percent=50.0, secondary_used_percent=60.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_HEALTHY

    def test_healthy_to_draining_high_primary(self) -> None:
        state = self._make_state(used_percent=90.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_healthy_to_draining_high_secondary(self) -> None:
        state = self._make_state(secondary_used_percent=95.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_healthy_to_draining_error_spike(self) -> None:
        state = self._make_state(error_count=2, last_error_at=990.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_error_spike_outside_window_stays_healthy(self) -> None:
        state = self._make_state(error_count=2, last_error_at=900.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_HEALTHY

    def test_draining_stays_draining_while_condition_holds(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_DRAINING, used_percent=90.0)
        assert evaluate_health_tier(state, now=1000.0, drain_entered_at=950.0) == HEALTH_TIER_DRAINING

    def test_draining_to_probing_after_quiet_period(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_DRAINING, used_percent=50.0)
        assert evaluate_health_tier(state, now=1000.0, drain_entered_at=930.0) == HEALTH_TIER_PROBING

    def test_draining_stays_if_quiet_period_not_elapsed(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_DRAINING, used_percent=50.0)
        assert evaluate_health_tier(state, now=1000.0, drain_entered_at=980.0) == HEALTH_TIER_DRAINING

    def test_probing_to_healthy_after_streak(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_PROBING)
        assert evaluate_health_tier(state, now=1000.0, probe_success_streak=3) == HEALTH_TIER_HEALTHY

    def test_probing_stays_probing_insufficient_streak(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_PROBING)
        assert evaluate_health_tier(state, now=1000.0, probe_success_streak=2) == HEALTH_TIER_PROBING

    def test_probing_to_draining_on_new_error(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_PROBING, error_count=2, last_error_at=990.0)
        assert evaluate_health_tier(state, now=1000.0, probe_success_streak=1) == HEALTH_TIER_DRAINING

    def test_hard_blocked_preserves_tier(self) -> None:
        for status in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED, AccountStatus.PAUSED):
            state = self._make_state(status=status, health_tier=HEALTH_TIER_DRAINING)
            assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_none_usage_stays_healthy(self) -> None:
        state = self._make_state()
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_HEALTHY

    def test_draining_no_drain_entered_at_stays_draining(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_DRAINING, used_percent=50.0)
        assert evaluate_health_tier(state, now=1000.0, drain_entered_at=None) == HEALTH_TIER_DRAINING

    def test_exactly_at_primary_threshold(self) -> None:
        state = self._make_state(used_percent=85.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_just_below_primary_threshold(self) -> None:
        state = self._make_state(used_percent=84.9)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_HEALTHY

    def test_exactly_at_secondary_threshold(self) -> None:
        state = self._make_state(secondary_used_percent=90.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING


class TestSelectAccountHealthTier:
    def test_prefers_healthy_over_draining(self) -> None:
        states = [
            AccountState("a", AccountStatus.ACTIVE, used_percent=50.0, health_tier=HEALTH_TIER_DRAINING),
            AccountState("b", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_prefers_healthy_over_probing(self) -> None:
        states = [
            AccountState("a", AccountStatus.ACTIVE, used_percent=10.0, health_tier=HEALTH_TIER_PROBING),
            AccountState("b", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_prefers_probing_over_draining(self) -> None:
        states = [
            AccountState("a", AccountStatus.ACTIVE, used_percent=10.0, health_tier=HEALTH_TIER_DRAINING),
            AccountState("b", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_PROBING),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_falls_back_to_draining_when_no_healthy(self) -> None:
        states = [
            AccountState("a", AccountStatus.ACTIVE, used_percent=90.0, health_tier=HEALTH_TIER_DRAINING),
            AccountState("b", AccountStatus.ACTIVE, used_percent=50.0, health_tier=HEALTH_TIER_DRAINING),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_all_healthy_normal_selection(self) -> None:
        states = [
            AccountState("a", AccountStatus.ACTIVE, used_percent=50.0, health_tier=HEALTH_TIER_HEALTHY),
            AccountState("b", AccountStatus.ACTIVE, used_percent=10.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_capacity_weighted_respects_tier(self) -> None:
        states = [
            AccountState(
                "drain",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_DRAINING,
                plan_type="plus",
                capacity_credits=7560.0,
            ),
            AccountState(
                "healthy",
                AccountStatus.ACTIVE,
                used_percent=80.0,
                health_tier=HEALTH_TIER_HEALTHY,
                plan_type="plus",
                capacity_credits=7560.0,
            ),
        ]
        result = select_account(states, routing_strategy="capacity_weighted", deterministic_probe=True)
        assert result.account is not None
        assert result.account.account_id == "healthy"


class TestSelectWithStickinessDrain:
    def test_draining_pinned_skipped_for_prompt_cache(self) -> None:
        states = [
            AccountState("pinned", AccountStatus.ACTIVE, used_percent=90.0, health_tier=HEALTH_TIER_DRAINING),
            AccountState("other", AccountStatus.ACTIVE, used_percent=50.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "other"
