from __future__ import annotations

import pytest

from app.core.usage import (
    capacity_for_plan,
    normalize_usage_window,
    overall_used_percent,
    used_credits_from_percent,
)
from app.core.usage.types import UsageWindowSummary

pytestmark = pytest.mark.unit


def test_used_credits_from_percent():
    assert used_credits_from_percent(25.0, 200.0) == 50.0
    assert used_credits_from_percent(None, 200.0) is None


def test_overall_used_percent():
    entries = [(10.0, 100.0), (50.0, 200.0)]
    expected = ((10.0 + 100.0) / 300.0) * 100.0
    assert overall_used_percent(entries) == pytest.approx(expected)


def test_normalize_usage_window_defaults():
    summary = UsageWindowSummary(
        used_percent=None,
        capacity_credits=0.0,
        used_credits=0.0,
        reset_at=None,
        window_minutes=None,
    )
    window = normalize_usage_window(summary)
    assert window.used_percent == 0.0
    assert window.capacity_credits == 0.0
    assert window.used_credits == 0.0


def test_capacity_for_plan():
    assert capacity_for_plan("plus", "5h") is not None
    assert capacity_for_plan("plus", "7d") is not None
    assert capacity_for_plan("unknown", "5h") is None
