"""Cluster-wide account concurrency cap partitioning.

Configured per-account concurrency caps are cluster-wide targets. Each replica
derives its own deterministic share locally from the same sorted active
bridge-ring member list, so there is no cross-replica mutable shared state and
no per-request database I/O: every replica computes `floor(cap / R)` plus one
extra slot when its rank falls below `cap mod R`.

Membership changes are adopted with direction-aware hysteresis keyed on the
share direction, not just the member count: changes that shrink or keep this
replica's share (count increases, or same-count churn that moves this replica
to a later rank) are adopted on the next refresh, while changes that would
grow the share (count decreases, or same-count churn that moves this replica
to an earlier rank, e.g. a draining replica being replaced by a new instance
id) are adopted only after the growing observation has been held continuously
for a configured stability window, so neither a missed heartbeat nor a rolling
replacement can transiently inflate a survivor's share.
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
    """Tracks the adopted partition with direction-aware hysteresis."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._adopted = CapPartition()
        self._pending_increase: CapPartition | None = None
        self._pending_increase_since: float | None = None

    @property
    def current(self) -> CapPartition:
        return self._adopted

    def observe_members(
        self,
        active_instance_ids: Sequence[str],
        self_instance_id: str,
        *,
        scale_down_seconds: float,
    ) -> bool:
        """Feed a fresh active-member list; return True when the adopted partition changed.

        The observing replica is always counted even when its own ring row is
        missing or stale, so startup and self-heartbeat gaps degrade to fewer
        shared slots rather than a crash or an empty ring.
        """
        members = sorted(set(active_instance_ids) | {self_instance_id})
        observed = CapPartition(replica_count=len(members), rank=members.index(self_instance_id))
        if observed == self._adopted:
            self._clear_pending_increase()
            return False
        if not self._would_increase_share(observed):
            # Scale-up or same-size churn toward a later rank: every share
            # shrinks or stays put, which is safe toward upstream — adopt now.
            self._adopted = observed
            self._clear_pending_increase()
            return True
        now = self._clock()
        if self._pending_increase is None or self._pending_increase.replica_count != observed.replica_count:
            self._pending_increase = observed
            self._pending_increase_since = now
            return False
        self._pending_increase = observed
        if self._pending_increase_since is not None and now - self._pending_increase_since >= scale_down_seconds:
            self._adopted = observed
            self._clear_pending_increase()
            return True
        return False

    def _would_increase_share(self, observed: CapPartition) -> bool:
        """Whether adopting ``observed`` could grow this replica's share of any cap.

        Fewer replicas always grow shares, and at an unchanged count an earlier
        rank can move this replica under the ``cap mod R`` remainder threshold
        and grant it an extra slot (same-size churn during rolling replacement).
        Larger counts and later ranks never grow a share.
        """
        if observed.replica_count < self._adopted.replica_count:
            return True
        return observed.replica_count == self._adopted.replica_count and observed.rank < self._adopted.rank

    def _clear_pending_increase(self) -> None:
        self._pending_increase = None
        self._pending_increase_since = None


_holder = CapPartitionHolder()


def get_cap_partition() -> CapPartition:
    """Return the partition currently used for account cap enforcement."""
    return _holder.current


def observe_ring_members(active_instance_ids: Sequence[str], self_instance_id: str) -> None:
    """Refresh the process-wide partition from an active bridge-ring member list."""
    settings = get_settings()
    scale_down_seconds = float(
        getattr(settings, "proxy_account_cap_partition_scale_down_seconds", DEFAULT_SCALE_DOWN_SECONDS)
    )
    previous = _holder.current
    changed = _holder.observe_members(
        active_instance_ids,
        self_instance_id,
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

    A failed membership read retains the last-known partition instead of
    falling open to the full configured caps.
    """
    try:
        members = await list_active_members()
    except Exception:
        logger.warning("Cap partition refresh failed; retaining last-known partition", exc_info=True)
        return
    observe_ring_members(members, self_instance_id)


def reset_cap_partition_for_tests() -> None:
    global _holder
    _holder = CapPartitionHolder()


def _record_cap_partition_replicas(count: int) -> None:
    if PROMETHEUS_AVAILABLE and cap_partition_replicas is not None:
        cap_partition_replicas.set(count)
