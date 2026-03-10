from __future__ import annotations

from dataclasses import dataclass as _dc
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.usage.depletion_service import (
    DepletionMetrics,
    compute_aggregate_depletion,
    compute_depletion_for_account,
    reset_ewma_state,
)

pytestmark = pytest.mark.unit

BASE_TIME = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)


@_dc
class _FakeEntry:
    account_id: str
    used_percent: float
    recorded_at: datetime
    reset_at: int | None
    window_minutes: int | None


def _entry(
    used_percent: float,
    recorded_at: datetime,
    reset_at: int | None = None,
    window_minutes: int | None = 300,
    account_id: str = "acc1",
) -> _FakeEntry:
    return _FakeEntry(
        account_id=account_id,
        used_percent=used_percent,
        recorded_at=recorded_at,
        reset_at=reset_at,
        window_minutes=window_minutes,
    )


def test_depletion_metrics_dataclass_shape() -> None:
    m = DepletionMetrics(
        risk=0.5,
        risk_level="warning",
        rate_per_second=0.001,
        burn_rate=1.5,
        safe_usage_percent=50.0,
        projected_exhaustion_at=None,
        seconds_until_exhaustion=None,
    )
    assert m.risk == pytest.approx(0.5)
    assert m.risk_level == "warning"
    assert m.rate_per_second == pytest.approx(0.001)


def test_compute_depletion_insufficient_history() -> None:
    reset_ewma_state()
    history = [_entry(10.0, BASE_TIME)]  # only 1 point
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=5)
    )
    assert result is None


def test_compute_depletion_sufficient_history() -> None:
    reset_ewma_state()
    history = [
        _entry(10.0, BASE_TIME),
        _entry(15.0, BASE_TIME + timedelta(minutes=1)),
    ]
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=2)
    )
    assert result is not None
    assert isinstance(result, DepletionMetrics)
    assert 0.0 <= result.risk <= 1.0
    assert result.risk_level in ("safe", "warning", "danger", "critical")


def test_compute_depletion_zero_rate_is_safe() -> None:
    reset_ewma_state()
    # Flat usage — no increase → rate=0 → risk = used_percent/100
    history = [
        _entry(50.0, BASE_TIME),
        _entry(50.0, BASE_TIME + timedelta(minutes=1)),
        _entry(50.0, BASE_TIME + timedelta(minutes=2)),
    ]
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=3)
    )
    assert result is not None
    # used=50%, rate=0 → projected=50% → risk=0.5
    assert result.risk == pytest.approx(0.5, abs=0.01)


def test_compute_depletion_window_reset_handled() -> None:
    reset_ewma_state()
    # Usage drops from 90% to 5% — window reset
    history = [
        _entry(90.0, BASE_TIME),
        _entry(95.0, BASE_TIME + timedelta(minutes=1)),
        _entry(5.0, BASE_TIME + timedelta(minutes=2)),  # reset
    ]
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=3)
    )
    # After reset, EWMA state resets — may return None or low risk
    if result is not None:
        assert 0.0 <= result.risk <= 1.0


def test_compute_depletion_empty_history() -> None:
    reset_ewma_state()
    result = compute_depletion_for_account("acc1", "codex_other", "primary", [], now=BASE_TIME)
    assert result is None


def test_aggregate_depletion_max_risk() -> None:
    metrics = [
        DepletionMetrics(
            risk=0.3,
            risk_level="safe",
            rate_per_second=0.001,
            burn_rate=0.5,
            safe_usage_percent=50.0,
            projected_exhaustion_at=None,
            seconds_until_exhaustion=None,
        ),
        DepletionMetrics(
            risk=0.8,
            risk_level="danger",
            rate_per_second=0.005,
            burn_rate=2.0,
            safe_usage_percent=50.0,
            projected_exhaustion_at=None,
            seconds_until_exhaustion=None,
        ),
        DepletionMetrics(
            risk=0.5,
            risk_level="warning",
            rate_per_second=0.002,
            burn_rate=1.0,
            safe_usage_percent=50.0,
            projected_exhaustion_at=None,
            seconds_until_exhaustion=None,
        ),
    ]
    result = compute_aggregate_depletion(metrics)
    assert result is not None
    assert result.risk == pytest.approx(0.8)
    assert result.risk_level == "danger"


def test_aggregate_depletion_empty_returns_none() -> None:
    result = compute_aggregate_depletion([])
    assert result is None


def test_aggregate_depletion_all_none_returns_none() -> None:
    result = compute_aggregate_depletion([None, None])
    assert result is None


def test_aggregate_depletion_single_metric() -> None:
    metrics = [
        DepletionMetrics(
            risk=0.7,
            risk_level="warning",
            rate_per_second=0.003,
            burn_rate=1.5,
            safe_usage_percent=60.0,
            projected_exhaustion_at=None,
            seconds_until_exhaustion=None,
        )
    ]
    result = compute_aggregate_depletion(metrics)
    assert result is not None
    assert result.risk == pytest.approx(0.7)
    assert result.risk_level == "warning"


def test_reset_ewma_state_clears_state() -> None:
    reset_ewma_state()
    history = [
        _entry(10.0, BASE_TIME),
        _entry(20.0, BASE_TIME + timedelta(minutes=1)),
    ]
    # First call — builds state
    compute_depletion_for_account("acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=2))
    # Reset
    reset_ewma_state()
    # After reset, single point returns None
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", [_entry(10.0, BASE_TIME)], now=BASE_TIME + timedelta(minutes=3)
    )
    assert result is None


def test_repeated_calls_with_same_history_are_idempotent() -> None:
    """R5-F1: Replaying the same history must not cause EWMA drift."""
    reset_ewma_state()
    history = [
        _entry(10.0, BASE_TIME),
        _entry(15.0, BASE_TIME + timedelta(minutes=1)),
        _entry(20.0, BASE_TIME + timedelta(minutes=2)),
    ]
    now = BASE_TIME + timedelta(minutes=3)

    # First call computes initial metrics
    result1 = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert result1 is not None

    # Repeated calls with same history must return identical risk (no drift)
    result2 = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert result2 is not None
    assert result2.risk == pytest.approx(result1.risk)
    assert result2.rate_per_second == pytest.approx(result1.rate_per_second)

    result3 = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert result3 is not None
    assert result3.risk == pytest.approx(result1.risk)
    assert result3.rate_per_second == pytest.approx(result1.rate_per_second)


def test_new_entries_still_update_ewma_state() -> None:
    """R5-F1: New entries beyond the last timestamp must still be processed."""
    reset_ewma_state()
    history_batch1 = [
        _entry(10.0, BASE_TIME),
        _entry(15.0, BASE_TIME + timedelta(minutes=1)),
    ]
    now1 = BASE_TIME + timedelta(minutes=2)
    result1 = compute_depletion_for_account("acc1", "codex_other", "primary", history_batch1, now=now1)
    assert result1 is not None

    # Second call with additional newer entries
    history_batch2 = history_batch1 + [
        _entry(25.0, BASE_TIME + timedelta(minutes=2)),
        _entry(35.0, BASE_TIME + timedelta(minutes=3)),
    ]
    now2 = BASE_TIME + timedelta(minutes=4)
    result2 = compute_depletion_for_account("acc1", "codex_other", "primary", history_batch2, now=now2)
    assert result2 is not None
    # Rate should be higher now (usage accelerated from 5%/min to 10%/min)
    assert result2.rate_per_second > result1.rate_per_second


def test_aged_out_samples_do_not_keep_stale_ewma_influence() -> None:
    reset_ewma_state()
    full_window_history = [
        _entry(10.0, BASE_TIME),
        _entry(70.0, BASE_TIME + timedelta(minutes=1)),
        _entry(80.0, BASE_TIME + timedelta(minutes=2)),
    ]
    full_window_result = compute_depletion_for_account(
        "acc1",
        "codex_other",
        "primary",
        full_window_history,
        now=BASE_TIME + timedelta(minutes=3),
    )
    assert full_window_result is not None

    in_window_history = full_window_history[1:]
    in_window_result = compute_depletion_for_account(
        "acc1",
        "codex_other",
        "primary",
        in_window_history,
        now=BASE_TIME + timedelta(minutes=3),
    )
    assert in_window_result is not None
    assert in_window_result.rate_per_second == pytest.approx(10.0 / 60.0)
    assert in_window_result.rate_per_second < full_window_result.rate_per_second


def test_post_reset_window_returns_none() -> None:
    """R30-F1: When reset_at is in the past, depletion should be None (window expired)."""
    reset_ewma_state()
    reset_epoch = int((BASE_TIME + timedelta(minutes=5)).timestamp())
    history = [
        _entry(10.0, BASE_TIME, reset_at=reset_epoch, window_minutes=300),
        _entry(50.0, BASE_TIME + timedelta(minutes=1), reset_at=reset_epoch, window_minutes=300),
        _entry(80.0, BASE_TIME + timedelta(minutes=2), reset_at=reset_epoch, window_minutes=300),
    ]
    # 'now' is after the reset — the window has already expired
    now = BASE_TIME + timedelta(minutes=10)
    result = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert result is None
