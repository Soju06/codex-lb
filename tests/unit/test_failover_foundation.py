from __future__ import annotations

import pytest

from app.core.balancer.logic import (
    BURST_SAME_ACCOUNT_BASE_SECONDS,
    BURST_SAME_ACCOUNT_MAX_RETRIES,
    BURST_SAME_ACCOUNT_MAX_WAIT_SECONDS,
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    ROUTING_POLICY_BURN_FIRST,
    AccountState,
    burst_same_account_backoff_seconds,
    evaluate_health_tier,
    failover_decision,
    select_account,
)
from app.core.balancer.types import FailureClass, UpstreamError
from app.db.models import AccountStatus
from app.modules.proxy.helpers import classify_upstream_failure, is_upstream_burst_rejection

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

    def test_overloaded_error(self) -> None:
        # Regression for #565: upstream "Our servers are currently overloaded.
        # Please try again later" is delivered with code=overloaded_error and
        # may surface without a 5xx status (e.g. on streamed responses where
        # the HTTP status was already 200 before the error envelope).
        # Classifying it as non_retryable made the agent stop mid-task instead
        # of failing over to another account or surfacing a retryable error.
        result = classify_upstream_failure(
            error_code="overloaded_error",
            error=UpstreamError(message="Our servers are currently overloaded. Please try again later"),
            http_status=None,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_server_is_overloaded(self) -> None:
        result = classify_upstream_failure(
            error_code="server_is_overloaded",
            error=UpstreamError(message="Our servers are currently overloaded. Please try again later"),
            http_status=None,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_selected_model_capacity_message(self) -> None:
        result = classify_upstream_failure(
            error_code="invalid_request_error",
            error=UpstreamError(message="Selected model is at capacity. Please try a different model."),
            http_status=400,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_generic_capacity_message_is_not_model_capacity_retry(self) -> None:
        result = classify_upstream_failure(
            error_code="invalid_request_error",
            error=UpstreamError(
                message=("This model has a fixed context capacity; reduce input size or try a different model."),
            ),
            http_status=400,
            phase="first_event",
        )
        assert result["failure_class"] == "non_retryable"

    def test_rate_limit_code_takes_precedence_over_capacity_message(self) -> None:
        result = classify_upstream_failure(
            error_code="rate_limit_exceeded",
            error=UpstreamError(message="Selected model is at capacity. Please try a different model."),
            http_status=429,
            phase="first_event",
        )
        assert result["failure_class"] == "rate_limit"

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


class TestIsUpstreamBurstRejection:
    def test_truth_table(self) -> None:
        # Only a code-less HTTP 429 (classified retryable_transient) is a burst.
        assert is_upstream_burst_rejection(failure_class="retryable_transient", http_status=429) is True
        assert is_upstream_burst_rejection(failure_class="rate_limit", http_status=429) is False
        assert is_upstream_burst_rejection(failure_class="quota", http_status=429) is False
        assert is_upstream_burst_rejection(failure_class="non_retryable", http_status=429) is False
        assert is_upstream_burst_rejection(failure_class="retryable_transient", http_status=500) is False
        assert is_upstream_burst_rejection(failure_class="retryable_transient", http_status=503) is False
        assert is_upstream_burst_rejection(failure_class="retryable_transient", http_status=None) is False

    def test_composes_with_classify_for_the_prod_shape(self) -> None:
        # Prod: upstream 429 body carries only a message -> code normalizes to
        # ``upstream_error`` -> retryable_transient -> burst. Note that
        # ``classify_upstream_failure`` itself is unchanged: a bare
        # ``upstream_error`` with http_status=429 is still classified by the
        # transient code table, not by the status.
        codeless = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="Rate limit exceeded"),
            http_status=429,
            phase="connect",
        )
        assert codeless["failure_class"] == "retryable_transient"
        assert is_upstream_burst_rejection(failure_class=codeless["failure_class"], http_status=codeless["http_status"])
        coded = classify_upstream_failure(
            error_code="rate_limit_exceeded",
            error=UpstreamError(message="Try again in 1.5s"),
            http_status=429,
            phase="connect",
        )
        assert coded["failure_class"] == "rate_limit"
        assert not is_upstream_burst_rejection(failure_class=coded["failure_class"], http_status=coded["http_status"])


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
    def _make_state(self, *, health_tier: int = 0, **kwargs) -> AccountState:
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

    def test_prefers_healthy_normal_over_draining_burn_first(self) -> None:
        states = [
            AccountState(
                "drain",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_DRAINING,
                routing_policy=ROUTING_POLICY_BURN_FIRST,
            ),
            AccountState("healthy", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, routing_strategy="fill_first")
        assert result.account is not None
        assert result.account.account_id == "healthy"

    def test_prefers_healthy_over_probing(self) -> None:
        states = [
            AccountState(
                "a",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=990.0,
            ),
            AccountState("b", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, now=1000.0, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_due_probing_account_precedes_healthy(self) -> None:
        states = [
            AccountState(
                "probing",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=900.0,
            ),
            AccountState("healthy", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]

        result = select_account(states, now=1000.0, routing_strategy="usage_weighted")

        assert result.account is not None
        assert result.account.account_id == "probing"

    def test_never_selected_probing_account_is_due(self) -> None:
        states = [
            AccountState(
                "probing",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_PROBING,
            ),
            AccountState("healthy", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]

        result = select_account(states, now=1000.0, routing_strategy="usage_weighted")

        assert result.account is not None
        assert result.account.account_id == "probing"

    def test_oldest_due_probing_account_is_deterministic(self) -> None:
        states = [
            AccountState(
                "probing-later",
                AccountStatus.ACTIVE,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=850.0,
            ),
            AccountState(
                "probing-tie-b",
                AccountStatus.ACTIVE,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=800.0,
            ),
            AccountState(
                "probing-tie-a",
                AccountStatus.ACTIVE,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=800.0,
            ),
            AccountState("healthy", AccountStatus.ACTIVE, health_tier=HEALTH_TIER_HEALTHY),
        ]

        result = select_account(states, now=1000.0, routing_strategy="usage_weighted")

        assert result.account is not None
        assert result.account.account_id == "probing-tie-a"

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


class TestFailoverDecisionOwnerBound:
    """Owner-bound requests never fail over: retry the same account or surface."""

    @pytest.mark.parametrize("failure_class", ["rate_limit", "quota", "retryable_transient", "non_retryable"])
    def test_owner_bound_without_same_account_retry_surfaces(self, failure_class: FailureClass) -> None:
        assert (
            failover_decision(
                failure_class=failure_class,
                downstream_visible=False,
                candidates_remaining=5,
                owner_bound=True,
            )
            == "surface"
        )

    def test_owner_bound_with_same_account_retry_retries_same_account(self) -> None:
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=False,
                candidates_remaining=0,
                owner_bound=True,
                same_account_retry_available=True,
            )
            == "retry_same_account"
        )

    def test_downstream_visible_overrides_owner_bound_retry(self) -> None:
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=True,
                candidates_remaining=3,
                owner_bound=True,
                same_account_retry_available=True,
            )
            == "surface"
        )

    def test_same_account_retry_flag_is_ignored_when_not_owner_bound(self) -> None:
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=False,
                candidates_remaining=2,
                owner_bound=False,
                same_account_retry_available=True,
            )
            == "failover_next"
        )
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=False,
                candidates_remaining=0,
                owner_bound=False,
                same_account_retry_available=True,
            )
            == "surface"
        )

    def test_defaults_keep_legacy_positional_free_callers(self) -> None:
        # websocket/mixin.py and compact.py call without the new keywords.
        assert (
            failover_decision(
                failure_class="rate_limit",
                downstream_visible=False,
                candidates_remaining=1,
            )
            == "failover_next"
        )


class TestBurstSameAccountBackoffSeconds:
    def test_exponential_schedule_without_retry_after(self) -> None:
        assert [
            burst_same_account_backoff_seconds(index, retry_after_seconds=None)
            for index in range(1, BURST_SAME_ACCOUNT_MAX_RETRIES + 1)
        ] == [1.0, 2.0, 4.0]
        assert BURST_SAME_ACCOUNT_BASE_SECONDS == 1.0

    def test_retry_after_is_a_floor_not_a_ceiling(self) -> None:
        assert burst_same_account_backoff_seconds(1, retry_after_seconds=3) == 3.0
        assert burst_same_account_backoff_seconds(3, retry_after_seconds=3) == 4.0
        assert burst_same_account_backoff_seconds(1, retry_after_seconds=0) == 1.0
        assert burst_same_account_backoff_seconds(1, retry_after_seconds=-7) == 1.0

    def test_wait_is_capped(self) -> None:
        assert burst_same_account_backoff_seconds(1, retry_after_seconds=120) == BURST_SAME_ACCOUNT_MAX_WAIT_SECONDS
        assert burst_same_account_backoff_seconds(10, retry_after_seconds=None) == BURST_SAME_ACCOUNT_MAX_WAIT_SECONDS

    def test_retry_index_below_one_is_clamped(self) -> None:
        assert burst_same_account_backoff_seconds(0, retry_after_seconds=None) == 1.0
