"""Congestion-aware per-API-key fair-share stream admission.

Stream slots are a pool-wide scarce resource: the per-account stream cap
protects individual accounts from upstream rate limits, but slot allocation
across API keys is otherwise first-come-first-served, so one bursty key can
hold every slot and starve every other key. This module provides the pure
max-min fair-share decision used by the load balancer's stream admission:

- Work-conserving: while pool utilization is below the configured congestion
  threshold, every key admits unconditionally — idle capacity is never wasted
  on a static per-key limit.
- Under congestion, a key may take a new slot only while it holds fewer than
  ``max(MIN_GUARANTEE_STREAMS, pool_capacity // active_keys)`` streams. Keys
  under their fair share always admit (a key holding fewer than the minimum
  guarantee can never be denied, making light interactive keys
  starvation-proof), while over-share keys cannot reacquire freed slots until
  they drop back under — freed capacity flows to the keys the congestion was
  starving.

All quantities are computed over the selection's candidate account set, so
account-scoped keys are measured against the pool they can actually use. All
arithmetic is integer-only so decisions are deterministic and exactly
testable. The module performs no I/O and holds no state; the impure edge
(runtime counters, locking, metrics) lives in the load balancer.
"""

from __future__ import annotations

from dataclasses import dataclass

API_KEY_STREAM_FAIR_SHARE_ERROR_CODE = "api_key_stream_fair_share"

MIN_GUARANTEE_STREAMS = 2


@dataclass(frozen=True, slots=True)
class FairShareDecision:
    admitted: bool
    congested: bool
    fair_share: int
    pool_capacity: int
    pool_inflight: int
    active_key_count: int
    requester_inflight: int


def effective_stream_pool_capacity(
    *,
    candidate_account_count: int,
    stream_limit: int,
    stream_reserve_slots: int,
) -> int:
    """Return the candidate pool's stream capacity, or 0 when ungateable.

    Mirrors the per-account admission arithmetic: each account contributes
    ``max(1, stream_limit - stream_reserve_slots)`` slots. A nonpositive
    stream limit means unlimited caps and a zero return disables the gate.
    """
    if stream_limit <= 0 or candidate_account_count <= 0:
        return 0
    per_account = max(1, stream_limit - max(0, stream_reserve_slots))
    return candidate_account_count * per_account


def evaluate_stream_fair_share(
    *,
    pool_capacity: int,
    pool_inflight: int,
    requester_inflight: int,
    other_active_key_count: int,
    threshold_pct: int,
    min_guarantee: int = MIN_GUARANTEE_STREAMS,
) -> FairShareDecision:
    """Return the fair-share admission decision for one stream request.

    ``other_active_key_count`` counts keys other than the requester holding at
    least one in-flight stream on the candidate accounts; the requester is
    always counted exactly once on top of it, whether or not it is already
    active. Congestion is ``pool_inflight * 100 >= pool_capacity *
    threshold_pct`` (integer math); a ``threshold_pct`` or ``pool_capacity``
    of zero disables the gate and admits.
    """
    active_key_count = other_active_key_count + 1
    if threshold_pct <= 0 or pool_capacity <= 0:
        return FairShareDecision(
            admitted=True,
            congested=False,
            fair_share=0,
            pool_capacity=pool_capacity,
            pool_inflight=pool_inflight,
            active_key_count=active_key_count,
            requester_inflight=requester_inflight,
        )
    congested = pool_inflight * 100 >= pool_capacity * threshold_pct
    fair_share = max(min_guarantee, pool_capacity // active_key_count)
    admitted = not congested or requester_inflight + 1 <= fair_share
    return FairShareDecision(
        admitted=admitted,
        congested=congested,
        fair_share=fair_share,
        pool_capacity=pool_capacity,
        pool_inflight=pool_inflight,
        active_key_count=active_key_count,
        requester_inflight=requester_inflight,
    )


class ApiKeyFairShareDenialError(Exception):
    """Fair-share gate denial from a direct (selection-less) lease acquire.

    ``LoadBalancer.select_account`` reports denials through the selection
    error surface; ``LoadBalancer.acquire_account_lease`` returns ``None``
    only for plain per-account cap denials, so the fair-share denial raises
    this instead of overloading that return value. Carries the full
    ``FairShareDecision`` for callers that surface the denial numbers.
    """

    def __init__(self, decision: FairShareDecision) -> None:
        super().__init__(fair_share_denial_message(decision))
        self.decision = decision


def fair_share_denial_message(decision: FairShareDecision) -> str:
    """Human-readable denial message carrying the decision's numbers."""
    return (
        "API key stream fair share exceeded under pool congestion; "
        f"key holds {decision.requester_inflight} of a fair share of {decision.fair_share} streams "
        f"({decision.pool_inflight}/{decision.pool_capacity} pool streams in flight across "
        f"{decision.active_key_count} active keys). "
        "Waiting for the key's streams to finish or the pool to decongest."
    )
