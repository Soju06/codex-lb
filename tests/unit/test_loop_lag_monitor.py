from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.core.resilience import loop_lag_monitor

pytestmark = pytest.mark.unit


class _StubGauge:
    def __init__(self) -> None:
        self.values: list[float] = []

    def set(self, value: float) -> None:
        self.values.append(value)


class _StubCounter:
    def __init__(self) -> None:
        self.count = 0

    def inc(self, amount: float = 1) -> None:
        self.count += amount


@pytest.fixture
def stub_metrics(monkeypatch):
    gauge = _StubGauge()
    counter = _StubCounter()
    monkeypatch.setattr(loop_lag_monitor.prometheus_metrics, "event_loop_lag_seconds", gauge)
    monkeypatch.setattr(loop_lag_monitor.prometheus_metrics, "event_loop_lag_warnings_total", counter)
    monkeypatch.setattr(loop_lag_monitor, "_SAMPLE_INTERVAL_SECONDS", 0.01)
    return gauge, counter


async def _run_monitor_briefly(*, warn_threshold_seconds: float, block_seconds: float) -> asyncio.Task[None]:
    task = asyncio.create_task(
        loop_lag_monitor.run_event_loop_lag_monitor(warn_threshold_seconds=warn_threshold_seconds)
    )
    # Let the monitor enter its first sleep, then starve the loop synchronously
    # so the sleep resumes late — exactly what a callback storm looks like.
    await asyncio.sleep(0)
    if block_seconds:
        time.sleep(block_seconds)
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return task


async def test_starved_loop_emits_warning_and_metrics(stub_metrics, caplog):
    gauge, counter = stub_metrics
    with caplog.at_level(logging.WARNING, logger=loop_lag_monitor.logger.name):
        await _run_monitor_briefly(warn_threshold_seconds=0.05, block_seconds=0.15)
    assert any(v >= 0.05 for v in gauge.values)
    assert counter.count >= 1
    assert any("event_loop_lag" in record.message for record in caplog.records)


async def test_healthy_loop_stays_quiet(stub_metrics, caplog):
    gauge, counter = stub_metrics
    with caplog.at_level(logging.WARNING, logger=loop_lag_monitor.logger.name):
        await _run_monitor_briefly(warn_threshold_seconds=0.5, block_seconds=0.0)
    assert gauge.values, "gauge should be sampled even when healthy"
    assert counter.count == 0
    assert not [r for r in caplog.records if "event_loop_lag" in r.message]


async def test_warning_log_is_rate_limited(stub_metrics, caplog):
    _, counter = stub_metrics
    task = asyncio.create_task(loop_lag_monitor.run_event_loop_lag_monitor(warn_threshold_seconds=0.05))
    with caplog.at_level(logging.WARNING, logger=loop_lag_monitor.logger.name):
        await asyncio.sleep(0)
        time.sleep(0.1)
        await asyncio.sleep(0.05)
        time.sleep(0.1)
        await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    warning_lines = [r for r in caplog.records if "event_loop_lag" in r.message]
    assert len(warning_lines) == 1, "second spike within the window must be suppressed"
    assert counter.count >= 2, "counter still tracks every over-threshold sample"
