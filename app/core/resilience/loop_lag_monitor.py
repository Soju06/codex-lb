"""Event-loop lag watchdog.

Samples scheduling delay by measuring ``asyncio.sleep`` drift. When the loop
is starved (a callback storm, synchronous work on the loop, CPU saturation),
every request and health check degrades at once while per-request logs stay
quiet: nothing says "the loop itself is busy". The 2026-08-20 incident — an
``asyncio.shield`` callback storm pinning one core for hours — surfaced only
as mysterious global slowness and health-check flapping. This monitor turns
that state into an explicit, rate-limited warning log plus Prometheus
signals (``codex_lb_event_loop_lag_seconds`` gauge and
``codex_lb_event_loop_lag_warnings_total`` counter) so operators and alerts
can distinguish "loop starved" from "upstream slow".
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.core.metrics import prometheus as prometheus_metrics

logger = logging.getLogger(__name__)

_SAMPLE_INTERVAL_SECONDS = 1.0
# One warning line per window at most; the gauge/counter stay per-sample. The
# worst lag seen inside a suppressed window is carried into the next line so
# suppression never hides the magnitude of a spike.
_WARN_LOG_INTERVAL_SECONDS = 60.0


async def run_event_loop_lag_monitor(*, warn_threshold_seconds: float) -> None:
    """Sample loop lag forever; the caller owns and cancels the task."""
    last_warn_monotonic = float("-inf")
    worst_suppressed_lag = 0.0
    while True:
        started = time.monotonic()
        await asyncio.sleep(_SAMPLE_INTERVAL_SECONDS)
        lag = max(0.0, time.monotonic() - started - _SAMPLE_INTERVAL_SECONDS)
        gauge = prometheus_metrics.event_loop_lag_seconds
        if gauge is not None:
            gauge.set(lag)
        if lag < warn_threshold_seconds:
            continue
        counter = prometheus_metrics.event_loop_lag_warnings_total
        if counter is not None:
            counter.inc()
        now = time.monotonic()
        if now - last_warn_monotonic < _WARN_LOG_INTERVAL_SECONDS:
            worst_suppressed_lag = max(worst_suppressed_lag, lag)
            continue
        logger.warning(
            "event_loop_lag lag_seconds=%.3f worst_suppressed_seconds=%.3f threshold_seconds=%.3f "
            "(event loop starved: callback storm, sync work on the loop, or CPU saturation)",
            lag,
            worst_suppressed_lag,
            warn_threshold_seconds,
        )
        last_warn_monotonic = now
        worst_suppressed_lag = 0.0
