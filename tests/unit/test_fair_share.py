from __future__ import annotations

import pytest

from app.modules.proxy.fair_share import (
    API_KEY_STREAM_FAIR_SHARE_ERROR_CODE,
    MIN_GUARANTEE_STREAMS,
    effective_stream_pool_capacity,
    evaluate_stream_fair_share,
    fair_share_denial_message,
)

pytestmark = pytest.mark.unit


def test_zero_threshold_admits_and_is_never_congested_regardless_of_load() -> None:
    decision = evaluate_stream_fair_share(
        pool_capacity=4,
        pool_inflight=400,
        requester_inflight=400,
        other_active_key_count=0,
        threshold_pct=0,
    )
    assert decision.admitted is True
    assert decision.congested is False


def test_zero_pool_capacity_disables_the_gate() -> None:
    decision = evaluate_stream_fair_share(
        pool_capacity=0,
        pool_inflight=50,
        requester_inflight=50,
        other_active_key_count=3,
        threshold_pct=80,
    )
    assert decision.admitted is True
    assert decision.congested is False


def test_congestion_boundary_is_inclusive() -> None:
    # 2 * 100 == 4 * 50: exactly at the threshold counts as congested.
    at_threshold = evaluate_stream_fair_share(
        pool_capacity=4,
        pool_inflight=2,
        requester_inflight=0,
        other_active_key_count=0,
        threshold_pct=50,
    )
    below_threshold = evaluate_stream_fair_share(
        pool_capacity=4,
        pool_inflight=1,
        requester_inflight=0,
        other_active_key_count=0,
        threshold_pct=50,
    )
    assert at_threshold.congested is True
    assert below_threshold.congested is False


def test_threshold_100_congests_only_at_full_capacity() -> None:
    below = evaluate_stream_fair_share(
        pool_capacity=8,
        pool_inflight=7,
        requester_inflight=7,
        other_active_key_count=0,
        threshold_pct=100,
    )
    full = evaluate_stream_fair_share(
        pool_capacity=8,
        pool_inflight=8,
        requester_inflight=7,
        other_active_key_count=0,
        threshold_pct=100,
    )
    assert below.congested is False
    assert below.admitted is True
    assert full.congested is True


def test_effective_stream_pool_capacity_subtracts_the_reserve_per_account() -> None:
    assert (
        effective_stream_pool_capacity(
            candidate_account_count=3,
            stream_limit=8,
            stream_reserve_slots=1,
        )
        == 21
    )


def test_effective_stream_pool_capacity_floors_each_account_at_one_slot() -> None:
    assert (
        effective_stream_pool_capacity(
            candidate_account_count=5,
            stream_limit=1,
            stream_reserve_slots=1,
        )
        == 5
    )


def test_effective_stream_pool_capacity_zero_stream_limit_disables_the_gate() -> None:
    assert (
        effective_stream_pool_capacity(
            candidate_account_count=4,
            stream_limit=0,
            stream_reserve_slots=0,
        )
        == 0
    )


def test_effective_stream_pool_capacity_without_candidates_is_zero() -> None:
    assert (
        effective_stream_pool_capacity(
            candidate_account_count=0,
            stream_limit=8,
            stream_reserve_slots=1,
        )
        == 0
    )


def test_effective_stream_pool_capacity_clamps_a_negative_reserve() -> None:
    assert (
        effective_stream_pool_capacity(
            candidate_account_count=2,
            stream_limit=8,
            stream_reserve_slots=-5,
        )
        == 16
    )


def test_fair_share_uses_floor_division_over_active_keys() -> None:
    decision = evaluate_stream_fair_share(
        pool_capacity=10,
        pool_inflight=10,
        requester_inflight=3,
        other_active_key_count=2,
        threshold_pct=50,
    )
    assert decision.fair_share == 3  # 10 // 3
    assert decision.admitted is False  # 3 + 1 > 3


def test_min_guarantee_dominates_a_smaller_divided_share() -> None:
    # 4 // 3 == 1 < MIN_GUARANTEE_STREAMS: the guarantee wins.
    light = evaluate_stream_fair_share(
        pool_capacity=4,
        pool_inflight=4,
        requester_inflight=1,
        other_active_key_count=2,
        threshold_pct=50,
    )
    heavy = evaluate_stream_fair_share(
        pool_capacity=4,
        pool_inflight=4,
        requester_inflight=2,
        other_active_key_count=2,
        threshold_pct=50,
    )
    assert MIN_GUARANTEE_STREAMS == 2
    assert light.fair_share == 2
    assert light.admitted is True  # 1 + 1 <= 2
    assert heavy.fair_share == 2
    assert heavy.admitted is False  # 2 + 1 > 2


def test_requester_exactly_at_fair_share_is_denied_and_one_below_is_admitted() -> None:
    at_share = evaluate_stream_fair_share(
        pool_capacity=12,
        pool_inflight=12,
        requester_inflight=4,
        other_active_key_count=2,
        threshold_pct=80,
    )
    below_share = evaluate_stream_fair_share(
        pool_capacity=12,
        pool_inflight=12,
        requester_inflight=3,
        other_active_key_count=2,
        threshold_pct=80,
    )
    assert at_share.fair_share == 4  # 12 // 3
    assert at_share.congested is True
    assert at_share.admitted is False
    assert below_share.admitted is True


def test_divisor_counts_the_requester_exactly_once() -> None:
    # The requester joins the divisor once whether it is already active or new.
    alone = evaluate_stream_fair_share(
        pool_capacity=12,
        pool_inflight=12,
        requester_inflight=0,
        other_active_key_count=0,
        threshold_pct=50,
    )
    with_others = evaluate_stream_fair_share(
        pool_capacity=12,
        pool_inflight=12,
        requester_inflight=5,
        other_active_key_count=2,
        threshold_pct=50,
    )
    assert alone.active_key_count == 1
    assert alone.fair_share == 12  # 12 // 1
    assert with_others.active_key_count == 3
    assert with_others.fair_share == 4  # 12 // 3, not 12 // 2


def test_denial_message_carries_all_five_decision_numbers() -> None:
    decision = evaluate_stream_fair_share(
        pool_capacity=40,
        pool_inflight=31,
        requester_inflight=7,
        other_active_key_count=5,
        threshold_pct=50,
    )
    assert decision.admitted is False
    message = fair_share_denial_message(decision)
    assert "holds 7" in message
    assert "fair share of 6" in message  # max(2, 40 // 6)
    assert "31/40 pool streams in flight" in message
    assert "6 active keys" in message


def test_error_code_is_registered_with_local_capacity_and_overload_sets() -> None:
    # Membership in these two sets is what gives fair-share denials the
    # transport park-and-retry loop and the 429 + Retry-After rendering.
    from app.core.resilience.overload import LOCAL_OVERLOAD_CODES, is_local_overload_error_code
    from app.modules.proxy._service.support import (
        _LOCAL_ACCOUNT_CAP_ERROR_CODES,
        _account_selection_recovery_sleep_seconds_from_message,
        _is_local_account_cap_code,
    )

    assert API_KEY_STREAM_FAIR_SHARE_ERROR_CODE in _LOCAL_ACCOUNT_CAP_ERROR_CODES
    assert API_KEY_STREAM_FAIR_SHARE_ERROR_CODE in LOCAL_OVERLOAD_CODES
    assert _is_local_account_cap_code(API_KEY_STREAM_FAIR_SHARE_ERROR_CODE)
    assert is_local_overload_error_code(API_KEY_STREAM_FAIR_SHARE_ERROR_CODE)
    sleep_seconds = _account_selection_recovery_sleep_seconds_from_message(
        "API key stream fair share exceeded under pool congestion",
        error_code=API_KEY_STREAM_FAIR_SHARE_ERROR_CODE,
    )
    assert sleep_seconds is not None and sleep_seconds > 0
