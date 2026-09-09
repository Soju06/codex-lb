"""Backoff and isolation for accounts upstream keeps rejecting as overloaded.

``server_is_overloaded`` is an admission rejection: upstream refuses to start
a *new* response for the account while already-admitted streams on the same
account keep flowing. Two properties follow that the generic transient error
path cannot express:

- It is account-scoped and bursty. One account can be rejected on most fresh
  admissions for an hour while its siblings are clean, and the rejection can
  take 30-90 s to arrive, so every fresh admission routed there costs the
  client that wait before failover even starts.
- It says nothing bad about the account's live sessions. Bridge reuse on the
  same account keeps succeeding, and every success zeroes
  ``RuntimeState.error_count`` -- so the generic error backoff and the drain
  tier never latch, and selection keeps feeding the rejected account.

This module keeps a replica-local sliding window of overload rejections per
account with two escalation stages:

1. **Soft backoff** (every trip): fresh (unbound) selection and fresh sticky
   bindings *deprioritize* the account for a bounded, exponentially growing
   interval. Established sticky owners are left alone, so a short burst never
   churns warm sessions.
2. **Isolation** (the configured trip level, sustained overload): the account
   is held out for a longer, operator-configured interval and established
   *soft* sticky owners are rerouted as well. Only a soft sticky mapping is a
   locality hint; every request that re-enters the pinned account is a fresh
   upstream admission, so keeping the owner pinned just replays the rejection
   wait per request. Hard continuity owners (``previous_response_id``, bridge
   ownership, file pins) are resolved before soft selection and are never
   moved by this module.

In both stages the account is dropped from a candidate pool only while at
least one other candidate remains, so the window can never empty the pool.
The window is not reset by successes -- an account that succeeds on warm
sessions but rejects fresh admissions is exactly the case this exists for.

**Burst cooldown** (``record_upstream_burst_rejection``): a code-less upstream
HTTP 429 (a per-account burst/concurrency rejection whose body carries only a
message, no ``rate_limit_exceeded`` / usage code) is not a quota event -- the
same account typically succeeds again within seconds -- so it must neither
flip the persisted status nor touch ``cooldown_until``. It does mean the
account is momentarily saturated, so a short replica-local deadline
(``RuntimeState.burst_backoff_until``, honoring upstream ``Retry-After``
inside ``[BURST_BACKOFF_DEFAULT_SECONDS, BURST_BACKOFF_MAX_SECONDS]``) makes
``overload_backoff_active`` true and steers fresh selection and fresh sticky
bindings to a sibling. It is deliberately *not* an isolation trigger:
established soft sticky owners keep their session through a burst. The
overload rejection window and ``overload_backoff_until`` are never written by
the burst path.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.balancer.logic import AccountState
from app.db.models import Account
from app.modules.proxy._load_balancer.tunables import RoutingTunables
from app.modules.proxy._load_balancer.types import RuntimeState

logger = logging.getLogger(__name__)

# Upstream error codes that mean "admission refused: overloaded". Both spellings
# reach ``_handle_stream_error`` normalized to ``retryable_transient``.
UPSTREAM_OVERLOAD_CODES: frozenset[str] = frozenset({"server_is_overloaded", "overloaded_error"})

# Trip when this many rejections land inside the window. Three keeps a lone
# rejection (upstream hiccup) from deprioritizing an account, while a rejected
# account under real traffic trips within a minute or two.
OVERLOAD_TRIP_COUNT = 3
OVERLOAD_WINDOW_SECONDS = 120.0
# Bounded exponential deprioritization: 60 s, 120 s, 240 s, ... capped at 10 min.
OVERLOAD_BACKOFF_BASE_SECONDS = 60.0
OVERLOAD_BACKOFF_MAX_SECONDS = 600.0
# The level decays back to the base once the account has gone this long
# without tripping *and* without being held out, so a recovered account is not
# punished for last hour while an account leaving isolation keeps its level.
OVERLOAD_LEVEL_DECAY_SECONDS = 1800.0
# Levels saturate at the first level whose interval hits the cap
# (60, 120, 240, 480, then 600), so the stored level and the exponent are
# both bounded and sustained overload can never overflow or inflate the log.
OVERLOAD_MAX_LEVEL = 5


# The trip level at which sustained overload escalates from soft backoff to
# isolation: the third trip means at least nine rejections inside a few
# minutes despite the account already being deprioritized twice.
OVERLOAD_ISOLATION_TRIP_LEVEL = 3

# Burst cooldown bounds for a code-less upstream HTTP 429. The default covers
# the observed recovery window (the same account succeeds within a few seconds
# of the rejection); an upstream ``Retry-After`` raises the deadline up to the
# cap so a stale or hostile header cannot bench an account for minutes.
BURST_BACKOFF_DEFAULT_SECONDS = 5.0
BURST_BACKOFF_MAX_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class OverloadIsolationPolicy:
    """Operator knob for the isolation stage: the dashboard setting
    ``proxy_overload_isolation_seconds`` (environment fallback
    ``CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS``), resolved through
    ``RoutingTunables`` (C2-2 routing/overload)."""

    seconds: float = 1800.0

    @classmethod
    def from_tunables(cls, tunables: RoutingTunables) -> OverloadIsolationPolicy:
        return cls(seconds=float(tunables.overload_isolation_seconds))

    @property
    def enabled(self) -> bool:
        return self.seconds > 0.0

    def isolates(self, level: int) -> bool:
        return self.enabled and level >= OVERLOAD_ISOLATION_TRIP_LEVEL


def overload_backoff_seconds(level: int) -> float:
    """Deprioritization interval for a trip at ``level`` (1-based, saturating)."""
    exponent = min(max(0, level - 1), OVERLOAD_MAX_LEVEL - 1)
    return min(OVERLOAD_BACKOFF_MAX_SECONDS, OVERLOAD_BACKOFF_BASE_SECONDS * (2**exponent))


def overload_backoff_active(runtime: RuntimeState | None, now: float) -> bool:
    """Whether fresh admissions should avoid the account (soft backoff, isolation or burst cooldown)."""
    if runtime is None:
        return False
    if runtime.overload_backoff_until is not None and now < runtime.overload_backoff_until:
        return True
    return runtime.burst_backoff_until is not None and now < runtime.burst_backoff_until


def overload_isolation_active(runtime: RuntimeState | None, now: float) -> bool:
    """Whether the account is in the isolation stage (soft sticky owners reroute too)."""
    return runtime is not None and runtime.overload_isolated_until is not None and now < runtime.overload_isolated_until


def record_overload_rejection_locked(
    runtime: RuntimeState,
    now: float,
    *,
    isolation: OverloadIsolationPolicy | None = None,
) -> float | None:
    """Record one overload rejection observed at ``now``; return the new
    backoff deadline when it trips the window, else ``None``.

    When ``isolation`` says the new level isolates, the deadline is the
    isolation interval and ``runtime.overload_isolated_until`` is set to it.
    Caller holds the balancer's per-account lock.
    """
    quiet_since = runtime.overload_last_trip_at
    if quiet_since is not None and runtime.overload_backoff_until is not None:
        quiet_since = max(quiet_since, runtime.overload_backoff_until)
    if quiet_since is not None and now - quiet_since >= OVERLOAD_LEVEL_DECAY_SECONDS:
        runtime.overload_backoff_level = 0
    window_start = now - OVERLOAD_WINDOW_SECONDS
    recent = [at for at in (runtime.overload_rejections or ()) if at > window_start]
    recent.append(now)
    if len(recent) < OVERLOAD_TRIP_COUNT:
        runtime.overload_rejections = recent
        return None
    runtime.overload_rejections = []
    runtime.overload_backoff_level = min(runtime.overload_backoff_level + 1, OVERLOAD_MAX_LEVEL)
    runtime.overload_last_trip_at = now
    isolated = isolation is not None and isolation.isolates(runtime.overload_backoff_level)
    interval = (
        isolation.seconds
        if isolated and isolation is not None
        else overload_backoff_seconds(runtime.overload_backoff_level)
    )
    deadline = now + interval
    # A trip while already deprioritized (rejections keep arriving from
    # in-flight admissions) extends, never shortens, the deadline.
    if runtime.overload_backoff_until is not None and runtime.overload_backoff_until > deadline:
        deadline = runtime.overload_backoff_until
    runtime.overload_backoff_until = deadline
    if isolated:
        runtime.overload_isolated_until = deadline
    return deadline


async def record_upstream_overload(
    balancer: Any,
    account: Account,
    *,
    redact_account_id: bool = False,
    isolation: OverloadIsolationPolicy | None = None,
) -> None:
    """Record one upstream overload rejection for ``account`` at the balancer clock.

    Observations are taken where account health is written (the
    ``_handle_stream_error`` funnel), so they inherit its settlement ordering.
    No-op when ``balancer`` does not expose the runtime map (test doubles).
    """
    runtime_map = getattr(balancer, "_runtime", None)
    if not isinstance(runtime_map, dict):
        return
    if isolation is None:
        # C2-2 routing/overload: the error funnel carries no request snapshot;
        # use the balancer's most recent one (no settings read under a lock).
        isolation = OverloadIsolationPolicy.from_tunables(balancer.current_routing_tunables())
    lock = await balancer._get_account_lock(account.id)
    async with lock:
        now = float(balancer._clock.time())
        runtime = runtime_map.setdefault(account.id, RuntimeState())
        deadline = record_overload_rejection_locked(runtime, now, isolation=isolation)
        isolated = deadline is not None and overload_isolation_active(runtime, now)
    if deadline is None:
        return
    account_label = "<redacted>" if redact_account_id else account.id
    if isolated:
        logger.warning(
            "Account overload isolation engaged account_id=%s level=%d isolation_seconds=%.0f "
            "(fresh selection avoids the account and soft sticky owners are rerouted while another "
            "candidate can be selected; hard continuity owners are untouched)",
            account_label,
            runtime.overload_backoff_level,
            deadline - now,
        )
        return
    logger.warning(
        "Account overload backoff engaged account_id=%s level=%d backoff_seconds=%.0f "
        "(fresh selection deprioritizes the account while another candidate can be selected)",
        account_label,
        runtime.overload_backoff_level,
        deadline - now,
    )


def record_burst_rejection_locked(runtime: RuntimeState, now: float, *, retry_after_seconds: float | None) -> float:
    """Engage (or extend) the burst cooldown observed at ``now``; return the applied seconds.

    ``retry_after_seconds`` (upstream ``Retry-After``) is clamped to
    ``[BURST_BACKOFF_DEFAULT_SECONDS, BURST_BACKOFF_MAX_SECONDS]``; a missing
    header applies the default. A rejection while already cooling down
    extends, never shortens, the deadline. Caller holds the balancer's
    per-account lock.
    """
    requested = BURST_BACKOFF_DEFAULT_SECONDS if retry_after_seconds is None else float(retry_after_seconds)
    seconds = min(BURST_BACKOFF_MAX_SECONDS, max(BURST_BACKOFF_DEFAULT_SECONDS, requested))
    runtime.burst_backoff_until = max(runtime.burst_backoff_until or 0.0, now + seconds)
    return seconds


async def record_upstream_burst_rejection(
    balancer: Any,
    account: Account,
    *,
    retry_after_seconds: float | None = None,
    redact_account_id: bool = False,
) -> None:
    """Record one code-less upstream HTTP 429 burst rejection for ``account``.

    Taken at the ``_handle_stream_error`` funnel like ``record_upstream_overload``.
    Writes only ``RuntimeState.burst_backoff_until`` (never the overload window,
    ``cooldown_until`` or the persisted status). No-op when ``balancer`` does not
    expose the runtime map (test doubles).
    """
    runtime_map = getattr(balancer, "_runtime", None)
    if not isinstance(runtime_map, dict):
        return
    lock = await balancer._get_account_lock(account.id)
    async with lock:
        now = float(balancer._clock.time())
        runtime = runtime_map.setdefault(account.id, RuntimeState())
        applied = record_burst_rejection_locked(runtime, now, retry_after_seconds=retry_after_seconds)
    logger.warning(
        "Account burst backoff engaged account_id=%s backoff_seconds=%.1f retry_after_seconds=%s http_status=429",
        "<redacted>" if redact_account_id else account.id,
        applied,
        retry_after_seconds,
    )


def filter_overload_backoff_candidates(
    states: list[AccountState],
    runtime_by_account_id: Mapping[str, RuntimeState],
    *,
    now: float,
) -> list[AccountState]:
    """Return the candidates not in overload backoff, or ``states`` itself
    when that would leave nothing (or change nothing).

    Callers select from the returned list first and, when the configured
    strategy rejects every remaining candidate, select again from the
    original ``states`` -- the identity check (``is``) tells them whether a
    fallback is possible. Eligibility is therefore judged by the real
    selector under the real strategy and budget gates, never approximated
    here, so the backoff can only ever *reorder* preference: it cannot turn
    usable capacity into ``No available accounts`` or an account-cap error.
    """
    kept = [state for state in states if not overload_backoff_active(runtime_by_account_id.get(state.account_id), now)]
    if not kept or len(kept) == len(states):
        return states
    return kept


def sticky_owner_isolation_reroute_pool(
    states: list[AccountState],
    runtime_by_account_id: Mapping[str, RuntimeState] | None,
    *,
    owner_account_id: str,
    now: float,
) -> list[AccountState] | None:
    """Return the overload-free pool a *soft* sticky owner should be rerouted
    into, or ``None`` when the owner keeps its session.

    The owner is released only while it is in the isolation stage (not on a
    soft backoff) and the pool still holds another candidate outside the
    overload window; the caller must verify the strategy actually selects
    from the returned pool before abandoning the owner.
    """
    if runtime_by_account_id is None:
        return None
    if not overload_isolation_active(runtime_by_account_id.get(owner_account_id), now):
        return None
    pool = filter_overload_backoff_candidates(states, runtime_by_account_id, now=now)
    if pool is states:
        return None
    return pool
