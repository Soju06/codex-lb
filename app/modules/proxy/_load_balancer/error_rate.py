"""Replica-local recent error rate per account, feeding selection weights.

The generic ``error_count`` is a latch: it is zeroed by the next success, so
an account that fails one request in three never looks unhealthy to the
weighted strategies even though a third of the traffic routed there pays the
failure (and, for overload rejections, the 30-90 s wait before failover).

This module keeps a small minute-bucket window of upstream outcomes per
account -- successes and the account-attributable transient failures that
reach ``LoadBalancer.record_errors`` (rate limits, quota, permanent failures
and account-neutral rejections are handled by their own paths and never
counted here) -- and derives a draw-weight multiplier
``max(floor, 1 - error_rate)`` once the window holds enough samples. Weighted
strategies multiply each candidate's weight by it, so a flaky account keeps
receiving *some* traffic (the floor) and recovers its share as soon as its
window clears. Deterministic strategies are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.proxy._load_balancer.types import RuntimeState

_BUCKET_SECONDS = 60
# Ten minutes of outcomes: long enough to smooth a burst, short enough that a
# recovered account regains its full share within one window.
ERROR_RATE_WINDOW_SECONDS = 600.0
# Below this many outcomes the rate is noise; the multiplier stays neutral.
ERROR_RATE_MIN_SAMPLES = 10
# A fully failing account keeps 5% of its weight so the window keeps sampling
# it and the discount lifts as soon as it recovers.
ERROR_RATE_WEIGHT_FLOOR = 0.05


@dataclass(frozen=True, slots=True)
class ErrorRateWeightingPolicy:
    """Operator knob: the dashboard setting ``proxy_account_error_rate_weighting_enabled``
    (environment fallback ``CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED``),
    resolved by the caller into ``RoutingTunables`` and passed in -- this module
    never reads settings itself (C2-2 routing/overload)."""

    enabled: bool = True
    window_seconds: float = ERROR_RATE_WINDOW_SECONDS
    min_samples: int = ERROR_RATE_MIN_SAMPLES
    weight_floor: float = ERROR_RATE_WEIGHT_FLOOR


def _bucket(now: float) -> int:
    return int(now // _BUCKET_SECONDS)


def _prune_locked(runtime: RuntimeState, now: float, window_seconds: float) -> dict[int, list[int]]:
    buckets = runtime.outcome_buckets
    if buckets is None:
        buckets = runtime.outcome_buckets = {}
        return buckets
    oldest_kept = _bucket(now - window_seconds)
    stale = [key for key in buckets if key < oldest_kept]
    for key in stale:
        del buckets[key]
    return buckets


def record_outcome_locked(
    runtime: RuntimeState,
    now: float,
    *,
    success: bool,
    count: int = 1,
    window_seconds: float | None = None,
) -> None:
    """Record ``count`` outcomes at ``now``. Caller holds the per-account lock."""
    if count < 1:
        return
    window = window_seconds if window_seconds is not None else ERROR_RATE_WINDOW_SECONDS
    buckets = _prune_locked(runtime, now, window)
    entry = buckets.setdefault(_bucket(now), [0, 0])
    entry[0 if success else 1] += count


def recent_outcomes(runtime: RuntimeState | None, now: float, *, window_seconds: float) -> tuple[int, int]:
    """Return ``(successes, failures)`` observed inside the window (read-only)."""
    if runtime is None or not runtime.outcome_buckets:
        return 0, 0
    oldest_kept = _bucket(now - window_seconds)
    successes = failures = 0
    for key, (ok, failed) in runtime.outcome_buckets.items():
        if key >= oldest_kept:
            successes += ok
            failures += failed
    return successes, failures


def error_rate_weight_multiplier(
    runtime: RuntimeState | None,
    now: float,
    *,
    policy: ErrorRateWeightingPolicy,
) -> float:
    """Draw-weight multiplier in ``[floor, 1.0]`` for the account's recent error rate.

    Neutral (``1.0``) while disabled or while the window holds fewer than
    ``min_samples`` outcomes, so a quiet or brand-new account is never
    penalized on thin evidence.
    """
    effective = policy
    if not effective.enabled:
        return 1.0
    successes, failures = recent_outcomes(runtime, now, window_seconds=effective.window_seconds)
    samples = successes + failures
    if samples < max(1, effective.min_samples):
        return 1.0
    error_rate = failures / samples
    floor = min(max(effective.weight_floor, 0.0), 1.0)
    return max(floor, 1.0 - error_rate)
