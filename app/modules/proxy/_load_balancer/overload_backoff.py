"""Soft backoff for accounts upstream keeps rejecting as overloaded.

``server_is_overloaded`` is an admission rejection: upstream refuses to start
a *new* response for the account while already-admitted streams on the same
account keep flowing. Two properties follow that the generic transient error
path cannot express:

- It is account-scoped and bursty. One account can be rejected on most fresh
  admissions for an hour while its siblings are clean, and the rejection can
  take 30-90 s to arrive, so every fresh admission routed there costs the
  client that wait before failover even starts.
- It says nothing bad about the account's live sessions. Bridge reuse and
  sticky continuity on the same account keep succeeding, and every success
  zeroes ``RuntimeState.error_count`` -- so the generic error backoff and the
  drain tier never latch, and fresh selection keeps feeding the rejected
  account.

This module keeps a replica-local sliding window of overload rejections per
account. When it trips, fresh (unbound) selection *deprioritizes* the account
for a bounded, exponentially growing interval: it is dropped from the candidate
pool only while at least one other candidate remains, so it can never empty
the pool, and sticky / continuity / hard-affinity selection is untouched. The
window is not reset by successes -- an account that succeeds on warm sessions
but rejects fresh admissions is exactly the case this exists for.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from app.core.balancer.logic import AccountState
from app.db.models import Account
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
# without tripping, so a recovered account is not punished for last hour.
OVERLOAD_LEVEL_DECAY_SECONDS = 1800.0
# Levels saturate at the first level whose interval hits the cap
# (60, 120, 240, 480, then 600), so the stored level and the exponent are
# both bounded and sustained overload can never overflow or inflate the log.
OVERLOAD_MAX_LEVEL = 5


class _OverloadBalancerLike(Protocol):
    _runtime: dict[str, RuntimeState]
    _clock: Any

    async def _get_account_lock(self, account_id: str) -> Any: ...


def overload_backoff_seconds(level: int) -> float:
    """Deprioritization interval for a trip at ``level`` (1-based, saturating)."""
    exponent = min(max(0, level - 1), OVERLOAD_MAX_LEVEL - 1)
    return min(OVERLOAD_BACKOFF_MAX_SECONDS, OVERLOAD_BACKOFF_BASE_SECONDS * (2**exponent))


def overload_backoff_active(runtime: RuntimeState | None, now: float) -> bool:
    return runtime is not None and runtime.overload_backoff_until is not None and now < runtime.overload_backoff_until


def record_overload_rejection_locked(runtime: RuntimeState, now: float) -> float | None:
    """Record one overload rejection observed at ``now``; return the new
    backoff deadline when it trips the window, else ``None``.

    Caller holds the balancer's per-account lock.
    """
    if (
        runtime.overload_last_trip_at is not None
        and now - runtime.overload_last_trip_at >= OVERLOAD_LEVEL_DECAY_SECONDS
    ):
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
    deadline = now + overload_backoff_seconds(runtime.overload_backoff_level)
    # A trip while already deprioritized (rejections keep arriving from
    # in-flight admissions) extends, never shortens, the deadline.
    if runtime.overload_backoff_until is not None and runtime.overload_backoff_until > deadline:
        deadline = runtime.overload_backoff_until
    runtime.overload_backoff_until = deadline
    return deadline


async def record_upstream_overload(balancer: Any, account: Account, *, redact_account_id: bool = False) -> None:
    """Record one upstream overload rejection for ``account`` at the balancer clock.

    Observations are taken where account health is written (the
    ``_handle_stream_error`` funnel), so they inherit its settlement ordering.
    No-op when ``balancer`` does not expose the runtime map (test doubles).
    """
    runtime_map = getattr(balancer, "_runtime", None)
    if not isinstance(runtime_map, dict):
        return
    lock = await balancer._get_account_lock(account.id)
    async with lock:
        now = float(balancer._clock.time())
        runtime = runtime_map.setdefault(account.id, RuntimeState())
        deadline = record_overload_rejection_locked(runtime, now)
    if deadline is not None:
        logger.warning(
            "Account overload backoff engaged account_id=%s level=%d backoff_seconds=%.0f "
            "(fresh selection deprioritizes the account while another candidate can be selected)",
            "<redacted>" if redact_account_id else account.id,
            runtime.overload_backoff_level,
            deadline - now,
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


def overload_backed_off_account_ids(
    states: Iterable[AccountState],
    runtime_by_account_id: Mapping[str, RuntimeState],
    *,
    now: float,
) -> list[str]:
    return [
        state.account_id
        for state in states
        if overload_backoff_active(runtime_by_account_id.get(state.account_id), now)
    ]
