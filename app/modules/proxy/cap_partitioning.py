"""Cluster-wide account concurrency cap partitioning.

Configured per-account concurrency caps are cluster-wide targets. Each replica
derives its own deterministic share locally from the same sorted active
bridge-ring member list, so there is no cross-replica mutable shared state and
no per-request database I/O: every replica computes `floor(cap / R)` plus one
extra slot when its rank falls below `cap mod R`.

Membership changes are adopted with hysteresis keyed on whether this
replica's share could actually grow. A rank's share is monotone non-increasing
in both the replica count and the rank, but neither direction alone decides
growth: a count decrease can be outweighed by a rank increase (more members
sort ahead and consume the remainder), and a rank decrease can be outweighed by
a large enough count increase. Rather than reason about count/rank directions,
the decision compares the *prospective* share against the *current* share for
each configured cap directly — using the same effective caps (dashboard
overrides over startup defaults) and the same ``partition_cap`` share formula
the admission path enforces — and defers only when some configured cap's share
would strictly grow. Any change whose every configured-cap share holds or
shrinks is safe toward upstream and adopted on the next refresh; a
share-growing change is adopted only after that exact pending partition (count
and rank) has been observed continuously for a configured stability window — a
different pending partition restarts the window, and a failed membership read
also restarts it since an observation gap must not count as continuous — so
neither a missed heartbeat, a rolling replacement, nor a read outage can
transiently inflate a survivor's share.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.core.config.settings import get_settings
from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, cap_partition_replicas

logger = logging.getLogger(__name__)

DEFAULT_SCALE_DOWN_SECONDS = 60.0


def partition_cap(cap: int, replica_count: int, rank: int) -> int:
    """Return this replica's share of a cluster-wide account cap.

    ``cap <= 0`` stays unlimited on every replica, a single replica keeps the
    full cap, and every share is floored at one slot so an account never
    becomes unroutable on a replica (when ``cap < replica_count`` the
    aggregate may therefore reach ``replica_count``).
    """
    if cap <= 0:
        return 0
    if replica_count <= 1:
        return cap
    base, remainder = divmod(cap, replica_count)
    return max(1, base + (1 if rank < remainder else 0))


@dataclass(frozen=True, slots=True)
class CapPartition:
    """Adopted partitioning inputs: live replica count and this replica's rank."""

    replica_count: int = 1
    rank: int = 0


class CapPartitionHolder:
    """Tracks the adopted partition with share-growth-aware hysteresis."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._adopted = CapPartition()
        self._pending: CapPartition | None = None
        self._pending_since: float | None = None

    @property
    def current(self) -> CapPartition:
        return self._adopted

    def observe_members(
        self,
        active_instance_ids: Sequence[str],
        self_instance_id: str,
        *,
        configured_caps: Sequence[int],
        scale_down_seconds: float,
    ) -> bool:
        """Feed a fresh active-member list; return True when the adopted partition changed.

        ``configured_caps`` are the cluster-wide per-account caps currently in
        effect (response-create and stream limits); the share-growth decision is
        made against those caps only, never against an arbitrary cap.

        The observing replica is always counted even when its own ring row is
        missing or stale, so startup and self-heartbeat gaps degrade to fewer
        shared slots rather than a crash or an empty ring.
        """
        members = sorted(set(active_instance_ids) | {self_instance_id})
        observed = CapPartition(replica_count=len(members), rank=members.index(self_instance_id))
        if observed == self._adopted:
            self._clear_pending()
            return False
        if not self._could_grow_share(observed, configured_caps):
            # Every cap share shrinks or stays put, which is safe toward
            # upstream — adopt now.
            self._adopted = observed
            self._clear_pending()
            return True
        now = self._clock()
        if observed != self._pending:
            # A new or changed share-growing target restarts the stability
            # window: only a partition held continuously may be adopted.
            self._pending = observed
            self._pending_since = now
            return False
        if self._pending_since is not None and now - self._pending_since >= scale_down_seconds:
            self._adopted = observed
            self._clear_pending()
            return True
        return False

    def _could_grow_share(self, observed: CapPartition, configured_caps: Sequence[int]) -> bool:
        """Whether adopting ``observed`` grows this replica's share of any configured cap.

        The share is monotone non-increasing in both the replica count and the
        rank, but neither direction alone decides growth: a count decrease can
        be outweighed by a rank increase, and a rank decrease by a large enough
        count increase. Reasoning about count/rank directions therefore either
        over-defers genuine shrinks (a count decrease that a rank increase turns
        into a smaller configured share) or under-defers genuine growth, so the
        decision compares the prospective share against the current share for
        each configured cap using the same ``partition_cap`` formula the
        admission path enforces. Adopting can only be unsafe toward the
        cluster-wide cap when some configured cap's share strictly grows; every
        other change (each configured cap's share holds or shrinks) is safe and
        adopted immediately.
        """
        adopted = self._adopted
        return any(
            partition_cap(cap, observed.replica_count, observed.rank)
            > partition_cap(cap, adopted.replica_count, adopted.rank)
            for cap in configured_caps
        )

    def note_failed_read(self) -> None:
        """Restart the stability window after a failed membership read.

        A share-growing partition is adopted only after being observed
        *continuously* for the stability window. A failed read is a gap in
        observations, so any pending share-increase is dropped and its window
        must restart from the next successful read; otherwise a read outage
        spanning the window would let the first post-outage observation adopt a
        larger survivor share without continuous confirmation, weakening the
        missed-heartbeat guard. The already-adopted partition is left intact.
        """
        self._clear_pending()

    def _clear_pending(self) -> None:
        self._pending = None
        self._pending_since = None


_holder = CapPartitionHolder()


def get_cap_partition() -> CapPartition:
    """Return the partition currently used for account cap enforcement."""
    return _holder.current


def configured_account_concurrency_caps(
    dashboard_settings: object | None,
    *,
    startup_settings: object | None = None,
) -> tuple[int, int]:
    """Return the cluster-wide per-account caps currently in effect.

    Dashboard-configured overrides (when present on ``dashboard_settings``) win
    over the startup defaults, exactly as ``effective_account_concurrency_caps``
    derives them before partitioning. The share-growth hysteresis must gate on
    these *same* effective caps rather than the startup defaults: when a
    dashboard cap is higher (or lower) than the startup value, a membership
    change that does not grow the startup-default share can still grow the
    effective share (or vice versa), so gating on the wrong caps would adopt or
    defer incorrectly. ``startup_settings`` supplies the startup-default source
    when a caller already holds it (so both the admission and hysteresis paths
    read the same settings object); it defaults to ``get_settings()``. Returns
    ``(response_create_limit, stream_limit)``.
    """
    if startup_settings is None:
        startup_settings = get_settings()
    response_override = getattr(dashboard_settings, "proxy_account_response_create_limit", None)
    stream_override = getattr(dashboard_settings, "proxy_account_stream_limit", None)
    startup_response = getattr(startup_settings, "proxy_account_response_create_limit", 4)
    startup_stream = getattr(startup_settings, "proxy_account_stream_limit", 8)
    response_cap = max(0, int(startup_response if response_override is None else response_override))
    stream_cap = max(0, int(startup_stream if stream_override is None else stream_override))
    return (response_cap, stream_cap)


async def _current_dashboard_settings() -> object | None:
    """Best-effort fetch of the dashboard-configured caps for the hysteresis gate.

    Reuses the process settings cache so the common case adds no per-refresh
    database round trip, and falls back to the startup defaults (``None``) when
    the read fails so a settings outage never blocks a membership refresh.
    """
    try:
        from app.core.config.settings_cache import get_settings_cache

        return await get_settings_cache().get()
    except Exception:
        logger.debug("Cap partition dashboard-settings lookup failed; using startup caps", exc_info=True)
        return None


def observe_ring_members(
    active_instance_ids: Sequence[str],
    self_instance_id: str,
    *,
    dashboard_settings: object | None = None,
) -> None:
    """Refresh the process-wide partition from an active bridge-ring member list.

    ``dashboard_settings`` carries the dashboard-configured per-account cap
    overrides so the hysteresis gates on the same effective caps the admission
    path partitions; when ``None`` the startup defaults apply.
    """
    settings = get_settings()
    scale_down_seconds = float(
        getattr(settings, "proxy_account_cap_partition_scale_down_seconds", DEFAULT_SCALE_DOWN_SECONDS)
    )
    previous = _holder.current
    changed = _holder.observe_members(
        active_instance_ids,
        self_instance_id,
        configured_caps=configured_account_concurrency_caps(dashboard_settings, startup_settings=settings),
        scale_down_seconds=scale_down_seconds,
    )
    current = _holder.current
    _record_cap_partition_replicas(current.replica_count)
    if changed:
        logger.info(
            "Account cap partition rebalanced old_count=%s new_count=%s rank=%s",
            previous.replica_count,
            current.replica_count,
            current.rank,
        )


async def refresh_cap_partition(
    list_active_members: Callable[[], Awaitable[Sequence[str]]],
    self_instance_id: str,
) -> None:
    """Refresh the partition from active bridge-ring membership.

    A failed membership read retains the last-known adopted partition instead
    of falling open to the full configured caps, but restarts any pending
    share-increase window so the observation gap does not count toward the
    continuous stability window.
    """
    try:
        members = await list_active_members()
    except Exception:
        logger.warning("Cap partition refresh failed; retaining last-known partition", exc_info=True)
        _holder.note_failed_read()
        return
    dashboard_settings = await _current_dashboard_settings()
    observe_ring_members(members, self_instance_id, dashboard_settings=dashboard_settings)


def reset_cap_partition_for_tests() -> None:
    global _holder
    _holder = CapPartitionHolder()


def _record_cap_partition_replicas(count: int) -> None:
    if PROMETHEUS_AVAILABLE and cap_partition_replicas is not None:
        cap_partition_replicas.set(count)
