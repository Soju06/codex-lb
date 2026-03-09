from __future__ import annotations

import pytest

from app.core.usage.depletion import (
    EWMAState,
    aggregate_risks,
    classify_risk,
    compute_burn_rate,
    compute_depletion_risk,
    compute_safe_usage_percent,
    ewma_update,
)

pytestmark = pytest.mark.unit

ALPHA = 0.4


def test_ewma_state_dataclass_shape() -> None:
    state = EWMAState(rate=0.1, last_used_percent=42.0, last_timestamp=1000.0)

    assert state.rate == pytest.approx(0.1)
    assert state.last_used_percent == pytest.approx(42.0)
    assert state.last_timestamp == pytest.approx(1000.0)


def test_ewma_first_observation_has_no_rate() -> None:
    state = ewma_update(None, used_percent=10.0, timestamp=0.0)

    assert state is not None
    assert state.rate is None


def test_ewma_second_observation_computes_rate() -> None:
    s1 = ewma_update(None, used_percent=10.0, timestamp=0.0)
    s2 = ewma_update(s1, used_percent=15.0, timestamp=60.0)

    assert s2.rate is not None
    assert s2.rate == pytest.approx(0.0833, abs=0.01)


def test_ewma_smooths_multiple_observations() -> None:
    s1 = ewma_update(None, used_percent=10.0, timestamp=0.0)
    s2 = ewma_update(s1, used_percent=15.0, timestamp=60.0)
    s3 = ewma_update(s2, used_percent=22.0, timestamp=120.0)

    assert s3.rate is not None
    assert s3.rate == pytest.approx(0.0967, abs=0.01)


def test_ewma_resets_on_window_reset() -> None:
    s1 = ewma_update(None, used_percent=90.0, timestamp=0.0)
    s2 = ewma_update(s1, used_percent=95.0, timestamp=60.0)
    s3 = ewma_update(s2, used_percent=5.0, timestamp=120.0)

    assert s2.rate is not None
    assert s3.rate is None or s3.rate >= 0.0


def test_ewma_zero_time_delta_is_skipped() -> None:
    s1 = ewma_update(None, used_percent=50.0, timestamp=100.0)
    s2 = ewma_update(s1, used_percent=55.0, timestamp=100.0)

    assert s2 is not None
    assert s2 == s1


def test_ewma_negative_rate_is_clamped_before_smoothing() -> None:
    s1 = ewma_update(None, used_percent=60.0, timestamp=0.0)
    s2 = ewma_update(s1, used_percent=70.0, timestamp=100.0)
    s3 = ewma_update(s2, used_percent=69.0, timestamp=200.0)

    assert s2.rate is not None
    assert s3.rate is not None
    assert s3.rate == pytest.approx((1 - ALPHA) * s2.rate, abs=0.01)


def test_ewma_stores_last_values() -> None:
    s1 = ewma_update(None, used_percent=42.0, timestamp=1000.0)

    assert s1.last_used_percent == 42.0
    assert s1.last_timestamp == 1000.0


def test_burn_rate_sustainable_pace() -> None:
    rate = 0.01667

    burn = compute_burn_rate(
        current_rate=rate,
        remaining_percent=60.0,
        seconds_until_reset=3600,
    )

    assert burn == pytest.approx(1.0, abs=0.01)


def test_burn_rate_twice_sustainable() -> None:
    rate = 0.01667 * 2

    burn = compute_burn_rate(
        current_rate=rate,
        remaining_percent=60.0,
        seconds_until_reset=3600,
    )

    assert burn == pytest.approx(2.0, abs=0.05)


def test_burn_rate_zero_remaining_time() -> None:
    burn = compute_burn_rate(
        current_rate=0.1,
        remaining_percent=50.0,
        seconds_until_reset=0.0,
    )

    assert burn >= 0.0


def test_burn_rate_zero_rate() -> None:
    burn = compute_burn_rate(
        current_rate=0.0,
        remaining_percent=50.0,
        seconds_until_reset=3600,
    )

    assert burn == pytest.approx(0.0, abs=0.001)


def test_depletion_risk_safe() -> None:
    risk = compute_depletion_risk(
        used_percent=30.0,
        rate_per_second=0.001,
        seconds_until_reset=3600,
    )

    assert risk == pytest.approx(0.336, abs=0.01)


def test_depletion_risk_capped_at_one() -> None:
    risk = compute_depletion_risk(
        used_percent=100.0,
        rate_per_second=0.1,
        seconds_until_reset=3600,
    )

    assert risk == pytest.approx(1.0, abs=0.001)


def test_depletion_risk_zero_rate() -> None:
    risk = compute_depletion_risk(
        used_percent=50.0,
        rate_per_second=0.0,
        seconds_until_reset=3600,
    )

    assert risk == pytest.approx(0.5, abs=0.001)


def test_depletion_risk_negative_rate() -> None:
    risk = compute_depletion_risk(
        used_percent=50.0,
        rate_per_second=-0.05,
        seconds_until_reset=3600,
    )

    assert risk == pytest.approx(0.5, abs=0.001)


def test_safe_usage_percent_midway() -> None:
    safe = compute_safe_usage_percent(seconds_elapsed=1800, total_window_seconds=3600)

    assert safe == pytest.approx(50.0, abs=0.1)


def test_safe_usage_percent_at_start() -> None:
    safe = compute_safe_usage_percent(seconds_elapsed=0.0, total_window_seconds=3600)

    assert safe == pytest.approx(0.0, abs=0.001)


def test_safe_usage_percent_at_end() -> None:
    safe = compute_safe_usage_percent(seconds_elapsed=3600.0, total_window_seconds=3600)

    assert safe == pytest.approx(100.0, abs=0.001)


def test_safe_usage_percent_zero_window() -> None:
    safe = compute_safe_usage_percent(seconds_elapsed=100.0, total_window_seconds=0.0)

    assert safe >= 0.0


def test_safe_usage_percent_clamps_above_window() -> None:
    safe = compute_safe_usage_percent(seconds_elapsed=5400.0, total_window_seconds=3600)

    assert safe == pytest.approx(100.0, abs=0.001)


def test_classify_risk_safe() -> None:
    assert classify_risk(0.0) == "safe"
    assert classify_risk(0.59) == "safe"


def test_classify_risk_warning() -> None:
    assert classify_risk(0.60) == "warning"
    assert classify_risk(0.79) == "warning"


def test_classify_risk_danger() -> None:
    assert classify_risk(0.80) == "danger"
    assert classify_risk(0.94) == "danger"


def test_classify_risk_critical() -> None:
    assert classify_risk(0.95) == "critical"
    assert classify_risk(1.0) == "critical"


def test_aggregate_risks_max() -> None:
    assert aggregate_risks([0.3, 0.8, 0.5]) == pytest.approx(0.8)


def test_aggregate_risks_empty() -> None:
    assert aggregate_risks([]) == pytest.approx(0.0)


def test_aggregate_risks_single() -> None:
    assert aggregate_risks([0.7]) == pytest.approx(0.7)


def test_aggregate_risks_all_zero() -> None:
    assert aggregate_risks([0.0, 0.0, 0.0]) == pytest.approx(0.0)
