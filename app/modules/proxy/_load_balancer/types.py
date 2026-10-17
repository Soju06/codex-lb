from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AccountLeaseKind = Literal["response_create", "stream"]

MAX_SELECTION_ATTEMPTS = 4


@dataclass
class RuntimeState:
    reset_at: float | None = None
    cooldown_until: float | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    version: int = 0
    health_version: int = 0
    blocked_at: float | None = None
    health_tier: int = 0
    drain_entered_at: float | None = None
    probe_success_streak: int = 0
    inflight_response_creates: int = 0
    inflight_streams: int = 0
    leased_tokens: float = 0.0
    leases: dict[str, AccountLease] | None = None
    stream_key_inflight: dict[str, int] | None = None
    # Upstream-overload soft backoff (see ``_load_balancer/overload_backoff.py``).
    # Recent ``server_is_overloaded`` rejection times inside the trip window,
    # the deprioritization deadline once tripped, the exponential level, and
    # when the level last tripped (for decay). Replica-local, never persisted.
    overload_rejections: list[float] | None = None
    # Recent *soft* overload observations: bare ``server_error`` terminals that
    # upstream returns for the same admission-rejection condition but without
    # the explicit overload code. Counted at a fractional weight so a genuine
    # one-off fault never trips the window on its own, while a sustained
    # ``server_error`` refusal still deprioritizes the account.
    soft_overload_rejections: list[float] | None = None
    overload_backoff_until: float | None = None
    overload_backoff_level: int = 0
    overload_last_trip_at: float | None = None
    # Isolation stage deadline (subset of the backoff deadline): while set and
    # in the future, soft sticky owners are rerouted too.
    overload_isolated_until: float | None = None
    # Short burst cooldown set by a code-less upstream HTTP 429 (per-account
    # burst/concurrency rejection, no ``rate_limit_exceeded`` / usage code):
    # fresh (unbound) selection and fresh sticky bindings avoid the account
    # until this deadline while another candidate exists. Established sticky
    # owners and hard continuity owners are untouched. Replica-local, never
    # persisted, independent of ``cooldown_until`` / persisted RATE_LIMITED.
    burst_backoff_until: float | None = None
    # Recent upstream outcome window (see ``_load_balancer/error_rate.py``):
    # minute bucket -> [successes, failures], pruned to the configured window.
    # Feeds the selection weight multiplier; replica-local, never persisted.
    outcome_buckets: dict[int, list[int]] | None = None
    # Recent eligible first-token latencies (see ``_load_balancer/ttft_cohort.py``):
    # ``(recorded_at, ttft_ms)`` pruned to the window/cap. Replica-local, never persisted.
    ttft_samples: list[tuple[float, int]] | None = None
    # Recent eligible output throughputs per model (see
    # ``_load_balancer/throughput_cohort.py``): model -> ``(recorded_at, tokens_per_second)``
    # pruned to the window/cap; models without in-window samples are dropped.
    tps_samples: dict[str, list[tuple[float, float]]] | None = None
    # Transition-log gates of ``_load_balancer/latency_cohort.py``: the last
    # applied first-token multiplier (model-agnostic) and the last applied
    # non-neutral throughput multiplier per model (neutral models are dropped,
    # so the map only grows with models the account is discounted on).
    ttft_weight: float = 1.0
    tps_weights: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class ProbeReservation:
    account_id: str
    previous_last_selected_at: float | None
    reserved_at: float
    expected_runtime_version: int


@dataclass(frozen=True, slots=True)
class AccountLease:
    lease_id: str
    account_id: str
    kind: AccountLeaseKind
    acquired_at: float
    estimated_tokens: float = 0.0
    api_key_id: str | None = None


@dataclass(frozen=True, slots=True)
class AccountConcurrencyCaps:
    response_create_limit: int
    stream_limit: int
    configured_response_create_limit: int | None = None
    configured_stream_limit: int | None = None
    replica_count: int = 1
