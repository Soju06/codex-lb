from __future__ import annotations

import pytest

from app.core.resilience import memory_monitor

pytestmark = pytest.mark.unit


def test_get_rss_bytes_returns_positive_number():
    rss = memory_monitor.get_rss_bytes()
    assert isinstance(rss, int)
    assert rss > 0


def test_is_memory_pressure_returns_false_when_threshold_disabled():
    memory_monitor.configure(warning_threshold_mb=0, reject_threshold_mb=0)
    assert memory_monitor.is_memory_pressure() is False
