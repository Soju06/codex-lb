"""Replica-local, fleet-relative first-token latency weight per account.

The balancer has no latency signal: two accounts with equal remaining credits
receive the same share of fresh weighted draws even when one of them answers
the same small prompts noticeably later (a cohort whose upstream reasons on a
large share of turns, or one on a slower upstream path). Every conversation
that lands there pays that cohort for its whole sticky lifetime.

This module keeps a bounded list of recent first-token latencies per account
for one narrow slice of traffic -- successful ``normal`` turns with a small
uncached input, no or low reasoning effort, a single upstream attempt and no
local queueing (the request kinds ``warmup``, ``compaction`` and
``realtime_live``, any row that waited on the response-create gate or bridge
queue, and any WebSocket/bridge row whose first-token latency spans a retried
send or an account-capacity wait are excluded) -- and derives a
draw-weight multiplier relative to the fleet: an account whose estimate sits
above the fleet median by more than the deadband is discounted to
``max(floor, fleet / account)``. ``latency_cohort.apply_latency_cohort_weights``
combines it with the per-model output-throughput multiplier
(``throughput_cohort.py``) as the minimum of the two and applies it to fresh
weighted draws; sticky owners, deterministic probes and deterministic
strategies never consult it. The signal is replica-local and never persisted.

Estimator: the per-account estimate is the mean of the samples below the top
decile (an upper-trimmed mean). The slow cohort observed in production is
bimodal -- a large share of its turns reason before the first token -- so a
plain median sits on the no-reasoning mode and misses the share, while a plain
mean is dominated by a single 30 s stall; trimming the tail keeps the mode
share visible and drops the stall. The fleet reference is the median of the
per-account estimates, so a uniformly slow fleet (upstream-wide degradation)
stays neutral. Estimator changes are one-function edits here, not settings.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from statistics import median
from typing import Any

from app.modules.proxy._load_balancer.types import RuntimeState

# One hour of eligible turns: long enough for a quiet account to keep evidence,
# short enough that an account changing cohort is re-weighted within the hour.
TTFT_SAMPLE_WINDOW_SECONDS = 3600.0
# Memory bound per account when an account exceeds ~one eligible turn a
# minute; the window bounds staleness, the cap bounds the list.
TTFT_MAX_SAMPLES = 64
# Below this many samples an account's estimate is noise and stays neutral.
TTFT_MIN_SAMPLES = 8
# Below this many accounts with evidence there is no fleet to be relative to.
TTFT_MIN_ACCOUNTS = 3
# Only the small-input slice is comparable across accounts; the cap applies
# to uncached input tokens (input minus cached prefix) so long-lived sticky
# sessions with a large cached prefix keep sampling.
TTFT_SAMPLE_MAX_INPUT_TOKENS = 20_000
TTFT_SAMPLE_EFFORTS: frozenset[str | None] = frozenset({None, "minimal", "low"})
TTFT_SAMPLE_REQUEST_KIND = "normal"
# Share of the slowest samples dropped from each account's estimate.
TTFT_TRIM_FRACTION = 0.1
# An account within 15% of the fleet reference is not discounted at all.
TTFT_WEIGHT_DEADBAND = 0.15
# A 10x outlier still keeps half of its weight: the window keeps sampling it
# and the discount lifts as soon as its samples recover.
TTFT_WEIGHT_FLOOR = 0.5


def _prune(samples: list[tuple[float, int]], now: float) -> None:
    oldest_kept = now - TTFT_SAMPLE_WINDOW_SECONDS
    stale = 0
    for recorded_at, _ in samples:
        if recorded_at >= oldest_kept:
            break
        stale += 1
    if stale:
        del samples[:stale]
    excess = len(samples) - TTFT_MAX_SAMPLES
    if excess > 0:
        del samples[:excess]


def _eligible(
    *,
    account_id: str | None,
    status: str,
    request_kind: str,
    latency_first_token_ms: int | None,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    reasoning_effort: str | None,
    latency_upstream_send_ms: int | None,
    queued_wait_ms: int,
    retried: bool,
) -> bool:
    return (
        bool(account_id)
        and status == "success"
        and request_kind == TTFT_SAMPLE_REQUEST_KIND
        and not retried
        and latency_first_token_ms is not None
        and latency_upstream_send_ms is not None
        and 0 <= latency_upstream_send_ms <= latency_first_token_ms
        and input_tokens is not None
        and input_tokens - (cached_input_tokens or 0) < TTFT_SAMPLE_MAX_INPUT_TOKENS
        and reasoning_effort in TTFT_SAMPLE_EFFORTS
        and queued_wait_ms <= 0
    )


def record_ttft_sample(
    balancer: Any,
    *,
    account_id: str | None,
    status: str,
    request_kind: str,
    latency_first_token_ms: int | None,
    input_tokens: int | None,
    cached_input_tokens: int | None = None,
    reasoning_effort: str | None,
    latency_upstream_send_ms: int | None,
    queued_wait_ms: int,
    retried: bool = False,
) -> None:
    """Record one eligible first-token latency for ``account_id`` at the balancer clock.

    The sample is ``latency_first_token_ms - latency_upstream_send_ms``: the
    row's first-token latency runs from the request start, and on the bridge
    path the start precedes session lookup, reconnect, prewarm, image inlining
    and payload slimming, so the account is only charged from its
    ``response.create`` send. ``latency_upstream_send_ms`` is the start-to-send
    offset (``0`` when the caller's clock already starts at the send, as the
    HTTP stream's attempt clock does); a row without one (``None``) or with a
    send after its first token has no usable anchor and is never sampled.
    ``retried`` marks a row whose first-token latency spans more than one
    upstream send or an account-capacity wait (bridge retries, transparent
    direct-WebSocket replays after a dropped upstream, retries after a
    capacity wait or an owner-pinned quota error); such a latency includes
    the failed attempt and the reconnect and, after an account switch, would
    be charged to the destination account, so it is never sampled.
    ``queued_wait_ms`` is the response-create gate wait (which includes the
    global response-create admission wait) plus the bridge-queue wait.

    Called from the request-log funnel on stream close. Synchronous and
    lock-free on purpose: there is no await between reading and writing the
    per-account list, so on the single-threaded loop it cannot interleave
    with selection, and the stream close never waits on the per-account lock
    that ``record_errors`` holds across a database write. No-op when
    ``balancer`` does not expose the runtime map or clock (test doubles).
    """
    runtime_map = getattr(balancer, "_runtime", None)
    clock = getattr(balancer, "_clock", None)
    if not isinstance(runtime_map, dict) or clock is None:
        return
    if not _eligible(
        account_id=account_id,
        status=status,
        request_kind=request_kind,
        latency_first_token_ms=latency_first_token_ms,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_effort=reasoning_effort,
        latency_upstream_send_ms=latency_upstream_send_ms,
        queued_wait_ms=queued_wait_ms,
        retried=retried,
    ):
        return
    if account_id is None or latency_first_token_ms is None or latency_upstream_send_ms is None:
        return  # narrowed by ``_eligible``
    now = float(clock.time())
    runtime = runtime_map.setdefault(account_id, RuntimeState())
    samples = runtime.ttft_samples
    if samples is None:
        samples = runtime.ttft_samples = []
    samples.append((now, int(latency_first_token_ms - latency_upstream_send_ms)))
    _prune(samples, now)


def account_ttft_estimate_ms(runtime: RuntimeState | None, now: float) -> float | None:
    """Upper-trimmed mean of the in-window samples; ``None`` below ``TTFT_MIN_SAMPLES``."""
    if runtime is None or not runtime.ttft_samples:
        return None
    oldest_kept = now - TTFT_SAMPLE_WINDOW_SECONDS
    values = sorted(ttft_ms for recorded_at, ttft_ms in runtime.ttft_samples if recorded_at >= oldest_kept)
    if len(values) < TTFT_MIN_SAMPLES:
        return None
    kept = values[: len(values) - math.ceil(len(values) * TTFT_TRIM_FRACTION)]
    return sum(kept) / len(kept)


def ttft_weight_multiplier(account_ms: float, fleet_ms: float) -> float:
    """Draw-weight multiplier in ``[floor, 1.0]`` for an account estimate relative to the fleet."""
    if fleet_ms <= 0.0 or account_ms <= fleet_ms * (1.0 + TTFT_WEIGHT_DEADBAND):
        return 1.0
    return max(TTFT_WEIGHT_FLOOR, fleet_ms / account_ms)


def fleet_ttft_estimates(
    runtime_by_account_id: Mapping[str, RuntimeState],
    now: float,
) -> tuple[dict[str, float], float | None]:
    """Per-account estimates and the fleet reference (their median).

    Computed over the whole runtime map so a selection narrowed to a subset is
    still weighed against the full pool; the reference is ``None`` -- every
    multiplier neutral -- while fewer than ``TTFT_MIN_ACCOUNTS`` accounts hold
    enough samples.
    """
    estimates = {
        account_id: estimate
        for account_id, runtime in runtime_by_account_id.items()
        if (estimate := account_ttft_estimate_ms(runtime, now)) is not None
    }
    fleet_ms = median(estimates.values()) if len(estimates) >= TTFT_MIN_ACCOUNTS else None
    return estimates, fleet_ms
