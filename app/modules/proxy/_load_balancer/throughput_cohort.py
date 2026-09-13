"""Replica-local, fleet-relative output-throughput weight per (account, model).

First-token latency (``ttft_cohort.py``) misses the dominant per-account speed
difference seen in production: on ``gpt-5.6-sol`` the fleet splits into two
output-throughput cohorts (nine accounts near 40 tok/s, ten near 66-75 tok/s)
whose first-token latency is the same, while on ``gpt-6-astra`` the same
accounts are uniform. Throughput is therefore a property of the (account,
model) pair, not of the account, and a slow-on-one-model account must not be
discounted on a model where it is not slow.

This module keeps, per account and per model, a bounded list of recent output
throughputs -- total ``output_tokens`` over the generation span
``latency_ms - latency_first_token_ms`` -- for successful ``normal`` turns
with at least 200 output tokens, a single upstream attempt and no local
queueing (the same funnel filters as the TTFT sampler), on any input size and
reasoning effort. ``output_tokens`` is the total the upstream reports,
reasoning tokens included. Where the reasoning lands depends on whether the
client asked for reasoning summaries: with summaries the first summary delta
is the first token, the reasoning runs inside the span and the total is what
the span produced; without summaries the whole reasoning prefix sits inside
the TTFT and the sample overstates the span's throughput by ``total /
visible`` -- several-fold on a turn that reasons for longer than it answers,
not a small bias. No numerator is exact for both kinds of turn (visible tokens
alone would under-read every summary-streaming turn by the same factor). The
total is kept because the comparison is fleet-relative: fresh draws spread
clients across accounts, so the summary/no-summary mix is shared and the
per-account medians move together; a per-account median only shifts when more
than half of that account's in-window samples are inflated, and the fleet
reference only when more than half of the accounts' medians are, so a spurious
discount needs an uneven mix that persists over most of an hour on most of the
fleet. Inflation reads *fast*, so it never discounts the account it lands on.

Estimator: the per-(account, model) estimate is the median of the sample
throughputs. Unlike first-token latency, throughput is not bimodal -- it is
the serving path's speed, largely independent of the prompt -- so a median
neither hides a mode share nor follows a single stalled stream (a network
pause or a slow client) or a short burst; the 200-token floor keeps the
quantization of ``tokens / duration`` small. The fleet reference is the median
of the per-account estimates for the requested model; an account below the
reference by more than the deadband is discounted to ``max(floor, account /
fleet)``. ``latency_cohort.apply_latency_cohort_weights`` combines this with the
TTFT multiplier as the minimum of the two (never their product) and only for
fresh weighted draws.
"""

from __future__ import annotations

from collections.abc import Mapping
from statistics import median
from typing import Any

from app.modules.proxy._load_balancer.ttft_cohort import (
    TTFT_MAX_SAMPLES,
    TTFT_MIN_ACCOUNTS,
    TTFT_SAMPLE_REQUEST_KIND,
    TTFT_SAMPLE_WINDOW_SECONDS,
)
from app.modules.proxy._load_balancer.types import RuntimeState

# Same window/cap pattern as the TTFT sampler, per (account, model).
TPS_SAMPLE_WINDOW_SECONDS = TTFT_SAMPLE_WINDOW_SECONDS
TPS_MAX_SAMPLES = TTFT_MAX_SAMPLES
# Below this many samples for a model an account's estimate is noise and stays neutral.
TPS_MIN_SAMPLES = 8
# Short answers quantize ``tokens / duration`` too coarsely to compare.
TPS_SAMPLE_MIN_OUTPUT_TOKENS = 200
# Rows written without a model (direct WebSocket rows carry ``""``) or with the
# placeholder cannot be attributed to a per-model cohort.
_UNKNOWN_MODEL = "unknown"
# An account within 15% below the fleet reference is not discounted at all.
TPS_WEIGHT_DEADBAND = 0.15
# A 10x slower account still keeps half of its weight: the window keeps
# sampling it and the discount lifts as soon as its throughput recovers.
TPS_WEIGHT_FLOOR = 0.5


def _sampleable_model(model: str | None) -> str | None:
    if not model or model == _UNKNOWN_MODEL:
        return None
    return model


def _prune(samples_by_model: dict[str, list[tuple[float, float]]], now: float) -> None:
    oldest_kept = now - TPS_SAMPLE_WINDOW_SECONDS
    for model in list(samples_by_model):
        samples = samples_by_model[model]
        stale = 0
        for recorded_at, _ in samples:
            if recorded_at >= oldest_kept:
                break
            stale += 1
        if stale:
            del samples[:stale]
        excess = len(samples) - TPS_MAX_SAMPLES
        if excess > 0:
            del samples[:excess]
        if not samples:
            del samples_by_model[model]


def _throughput_sample(
    *,
    account_id: str | None,
    status: str,
    request_kind: str,
    model: str | None,
    latency_ms: int | None,
    latency_first_token_ms: int | None,
    output_tokens: int | None,
    queued_wait_ms: int,
    retried: bool,
) -> tuple[str, float] | None:
    """``(model, tokens_per_second)`` for an eligible row, else ``None``."""
    sample_model = _sampleable_model(model)
    if (
        not account_id
        or status != "success"
        or request_kind != TTFT_SAMPLE_REQUEST_KIND
        or retried
        or sample_model is None
        or latency_first_token_ms is None
        or latency_first_token_ms < 0
        or latency_ms is None
        or latency_ms <= latency_first_token_ms
        or output_tokens is None
        or output_tokens < TPS_SAMPLE_MIN_OUTPUT_TOKENS
        or queued_wait_ms > 0
    ):
        return None
    return sample_model, output_tokens / ((latency_ms - latency_first_token_ms) / 1000.0)


def record_tps_sample(
    balancer: Any,
    *,
    account_id: str | None,
    status: str,
    request_kind: str,
    model: str | None,
    latency_ms: int | None,
    latency_first_token_ms: int | None,
    output_tokens: int | None,
    queued_wait_ms: int,
    retried: bool = False,
) -> None:
    """Record one eligible output throughput for ``(account_id, model)`` at the balancer clock.

    ``retried`` and ``queued_wait_ms`` have the meaning documented on
    ``ttft_cohort.record_ttft_sample``: a retried send or a capacity wait
    leaves a failed attempt inside ``latency_ms``, and a queued row's span is
    not the upstream's. ``latency_ms`` MUST end at the upstream terminal event,
    not at the end of downstream delivery or local settlement/cleanup: the
    request-log funnel passes the ``latency_upstream_terminal_ms`` each path
    stamps when it parses the terminal frame (HTTP stream settlement, bridge
    reader, WebSocket reader) in place of the row's latency. The throughput is
    ``output_tokens`` over the generation span
    ``latency_ms - latency_first_token_ms``; rows without a
    model, with the ``unknown`` placeholder, without a first token, with a
    non-positive span or with fewer than ``TPS_SAMPLE_MIN_OUTPUT_TOKENS``
    output tokens add nothing.

    Same concurrency contract as the TTFT sampler: synchronous, lock-free, no
    await between reading and writing the per-account map; no-op when
    ``balancer`` does not expose the runtime map or clock (test doubles).
    """
    runtime_map = getattr(balancer, "_runtime", None)
    clock = getattr(balancer, "_clock", None)
    if not isinstance(runtime_map, dict) or clock is None or not account_id:
        return
    sample = _throughput_sample(
        account_id=account_id,
        status=status,
        request_kind=request_kind,
        model=model,
        latency_ms=latency_ms,
        latency_first_token_ms=latency_first_token_ms,
        output_tokens=output_tokens,
        queued_wait_ms=queued_wait_ms,
        retried=retried,
    )
    if sample is None:
        return
    sample_model, tokens_per_second = sample
    now = float(clock.time())
    runtime = runtime_map.setdefault(account_id, RuntimeState())
    samples_by_model = runtime.tps_samples
    if samples_by_model is None:
        samples_by_model = runtime.tps_samples = {}
    samples_by_model.setdefault(sample_model, []).append((now, tokens_per_second))
    _prune(samples_by_model, now)


def account_tps_estimate(runtime: RuntimeState | None, model: str | None, now: float) -> float | None:
    """Median in-window throughput (tok/s) of ``runtime`` on ``model``; ``None`` below ``TPS_MIN_SAMPLES``."""
    if runtime is None or not runtime.tps_samples or not model:
        return None
    samples = runtime.tps_samples.get(model)
    if not samples:
        return None
    oldest_kept = now - TPS_SAMPLE_WINDOW_SECONDS
    values = [tps for recorded_at, tps in samples if recorded_at >= oldest_kept]
    if len(values) < TPS_MIN_SAMPLES:
        return None
    return float(median(values))


def tps_weight_multiplier(account_tps: float, fleet_tps: float) -> float:
    """Draw-weight multiplier in ``[floor, 1.0]`` for an account throughput relative to the fleet."""
    if fleet_tps <= 0.0 or account_tps <= 0.0 or account_tps >= fleet_tps * (1.0 - TPS_WEIGHT_DEADBAND):
        return 1.0
    return max(TPS_WEIGHT_FLOOR, account_tps / fleet_tps)


def fleet_tps_estimates(
    runtime_by_account_id: Mapping[str, RuntimeState],
    model: str | None,
    now: float,
) -> tuple[dict[str, float], float | None]:
    """Per-account estimates on ``model`` and the fleet reference (their median).

    The reference is ``None`` -- every multiplier neutral -- when no model is
    requested or fewer than ``TTFT_MIN_ACCOUNTS`` accounts hold enough samples
    for that model; evidence on other models never contributes.
    """
    if not model:
        return {}, None
    estimates = {
        account_id: estimate
        for account_id, runtime in runtime_by_account_id.items()
        if (estimate := account_tps_estimate(runtime, model, now)) is not None
    }
    fleet_tps = median(estimates.values()) if len(estimates) >= TTFT_MIN_ACCOUNTS else None
    return estimates, fleet_tps
