from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.modules.proxy.cap_partitioning as cap_partitioning_module
from app.modules.proxy.cap_partitioning import (
    CapPartition,
    CapPartitionHolder,
    get_cap_partition,
    observe_ring_members,
    partition_cap,
    refresh_cap_partition,
    reset_cap_partition_for_tests,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_partition():
    reset_cap_partition_for_tests()
    yield
    reset_cap_partition_for_tests()


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_partition_cap_even_split() -> None:
    assert partition_cap(8, 2, 0) == 4
    assert partition_cap(8, 2, 1) == 4


def test_partition_cap_remainder_goes_to_lowest_ranks_and_sums_to_cap() -> None:
    shares = [partition_cap(8, 3, rank) for rank in range(3)]
    assert shares == [3, 3, 2]
    assert sum(shares) == 8


def test_partition_cap_floors_share_at_one_slot() -> None:
    shares = [partition_cap(2, 3, rank) for rank in range(3)]
    assert shares == [1, 1, 1]


def test_partition_cap_nonpositive_cap_stays_unlimited() -> None:
    assert partition_cap(0, 3, 1) == 0
    assert partition_cap(-4, 2, 0) == 0


def test_partition_cap_single_replica_keeps_full_cap() -> None:
    assert partition_cap(8, 1, 0) == 8
    assert partition_cap(8, 0, 0) == 8


def test_holder_defaults_to_single_replica() -> None:
    holder = CapPartitionHolder()
    assert holder.current == CapPartition(replica_count=1, rank=0)


def test_holder_adopts_scale_up_immediately() -> None:
    holder = CapPartitionHolder(clock=_Clock())
    changed = holder.observe_members(["replica-a", "replica-b"], "replica-a", scale_down_seconds=60.0)
    assert changed is True
    assert holder.current == CapPartition(replica_count=2, rank=0)


def test_holder_counts_self_when_missing_from_member_list() -> None:
    holder = CapPartitionHolder(clock=_Clock())
    holder.observe_members(["replica-b"], "replica-a", scale_down_seconds=60.0)
    assert holder.current == CapPartition(replica_count=2, rank=0)


def test_holder_defers_scale_down_until_stability_window_elapses() -> None:
    clock = _Clock()
    holder = CapPartitionHolder(clock=clock)
    holder.observe_members(["replica-a", "replica-b"], "replica-a", scale_down_seconds=60.0)

    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=2, rank=0)

    clock.advance(59.0)
    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=2, rank=0)

    clock.advance(1.0)
    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is True
    assert holder.current == CapPartition(replica_count=1, rank=0)


def test_holder_flap_recovery_clears_pending_scale_down() -> None:
    clock = _Clock()
    holder = CapPartitionHolder(clock=clock)
    holder.observe_members(["replica-a", "replica-b"], "replica-a", scale_down_seconds=60.0)

    holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0)
    clock.advance(30.0)
    # The missing replica's heartbeat recovers inside the window.
    assert holder.observe_members(["replica-a", "replica-b"], "replica-a", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=2, rank=0)

    # A later scale-down must run the full window again.
    clock.advance(10.0)
    holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0)
    clock.advance(59.0)
    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=2, rank=0)
    clock.advance(1.0)
    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is True
    assert holder.current == CapPartition(replica_count=1, rank=0)


def test_holder_restarts_window_when_lower_count_changes() -> None:
    clock = _Clock()
    holder = CapPartitionHolder(clock=clock)
    holder.observe_members(["replica-a", "replica-b", "replica-c"], "replica-a", scale_down_seconds=60.0)

    holder.observe_members(["replica-a", "replica-b"], "replica-a", scale_down_seconds=60.0)
    clock.advance(30.0)
    # A different, lower count restarts the stability window.
    holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0)
    clock.advance(50.0)
    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=3, rank=0)
    clock.advance(10.0)
    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is True
    assert holder.current == CapPartition(replica_count=1, rank=0)


def test_holder_defers_same_count_churn_that_grows_share_until_window_elapses() -> None:
    clock = _Clock()
    holder = CapPartitionHolder(clock=clock)
    holder.observe_members(["replica-a", "replica-b"], "replica-b", scale_down_seconds=60.0)
    assert holder.current == CapPartition(replica_count=2, rank=1)

    # Rolling replacement: replica-a drains while replica-c appears, so the
    # count stays 2 but this replica's rank (and thus its remainder share)
    # would grow — the stability window must apply.
    assert holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=2, rank=1)

    clock.advance(59.0)
    assert holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=2, rank=1)

    clock.advance(1.0)
    assert holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0) is True
    assert holder.current == CapPartition(replica_count=2, rank=0)


def test_holder_adopts_same_count_churn_toward_later_rank_immediately() -> None:
    holder = CapPartitionHolder(clock=_Clock())
    holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0)
    assert holder.current == CapPartition(replica_count=2, rank=0)

    # Same-size churn that moves this replica to a later rank only shrinks
    # its share — safe toward upstream, adopted on this refresh.
    changed = holder.observe_members(["replica-a", "replica-b"], "replica-b", scale_down_seconds=60.0)
    assert changed is True
    assert holder.current == CapPartition(replica_count=2, rank=1)


def test_holder_same_count_churn_flap_recovery_clears_pending_increase() -> None:
    clock = _Clock()
    holder = CapPartitionHolder(clock=clock)
    holder.observe_members(["replica-a", "replica-b"], "replica-b", scale_down_seconds=60.0)

    holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0)
    clock.advance(30.0)
    # replica-a's heartbeat recovers inside the window: keep the adopted rank.
    assert holder.observe_members(["replica-a", "replica-b"], "replica-b", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=2, rank=1)

    # A later share-growing churn must run the full window again.
    clock.advance(10.0)
    holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0)
    clock.advance(59.0)
    assert holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=2, rank=1)
    clock.advance(1.0)
    assert holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0) is True
    assert holder.current == CapPartition(replica_count=2, rank=0)


def test_holder_defers_mixed_churn_where_count_grows_but_rank_moves_earlier() -> None:
    clock = _Clock()
    holder = CapPartitionHolder(clock=clock)
    holder.observe_members(
        ["replica-a", "replica-b", "replica-c", "replica-d", "replica-m"], "replica-m", scale_down_seconds=60.0
    )
    assert holder.current == CapPartition(replica_count=5, rank=4)

    # Rolling replacement: four lower-ranked members drain while five new
    # later-sorting ids appear. The count grows (5 -> 6) but the rank drops
    # (4 -> 0), which grows the share for e.g. cap 8 (1 slot -> 2 slots), so
    # the stability window must apply despite the larger count.
    churned = ["replica-m", "replica-n", "replica-o", "replica-p", "replica-q", "replica-r"]
    assert holder.observe_members(churned, "replica-m", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=5, rank=4)

    clock.advance(59.0)
    assert holder.observe_members(churned, "replica-m", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=5, rank=4)

    clock.advance(1.0)
    assert holder.observe_members(churned, "replica-m", scale_down_seconds=60.0) is True
    assert holder.current == CapPartition(replica_count=6, rank=0)


def test_holder_adopts_count_increase_toward_later_rank_immediately() -> None:
    holder = CapPartitionHolder(clock=_Clock())
    holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0)
    assert holder.current == CapPartition(replica_count=2, rank=0)

    # More replicas at a same-or-later rank can only shrink the share.
    changed = holder.observe_members(["replica-a", "replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0)
    assert changed is True
    assert holder.current == CapPartition(replica_count=3, rank=1)


def test_holder_restarts_window_when_pending_rank_changes_at_same_count() -> None:
    clock = _Clock()
    holder = CapPartitionHolder(clock=clock)
    holder.observe_members(
        ["replica-a", "replica-b", "replica-c", "replica-d", "replica-m"], "replica-m", scale_down_seconds=60.0
    )
    assert holder.current == CapPartition(replica_count=5, rank=4)

    # First share-growing observation: same count, rank 4 -> 3.
    rank_three = ["replica-b", "replica-c", "replica-d", "replica-m", "replica-z"]
    assert holder.observe_members(rank_three, "replica-m", scale_down_seconds=60.0) is False
    clock.advance(50.0)
    assert holder.observe_members(rank_three, "replica-m", scale_down_seconds=60.0) is False

    # A different pending target (same count, rank 0) must not inherit the
    # 50 seconds already accrued by the rank-3 observation.
    rank_zero = ["replica-m", "replica-v", "replica-w", "replica-x", "replica-y"]
    clock.advance(10.0)
    assert holder.observe_members(rank_zero, "replica-m", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=5, rank=4)

    clock.advance(59.0)
    assert holder.observe_members(rank_zero, "replica-m", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=5, rank=4)

    clock.advance(1.0)
    assert holder.observe_members(rank_zero, "replica-m", scale_down_seconds=60.0) is True
    assert holder.current == CapPartition(replica_count=5, rank=0)


def test_observe_ring_members_updates_process_partition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cap_partitioning_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_account_cap_partition_scale_down_seconds=60),
    )

    observe_ring_members(["replica-a", "replica-b"], "replica-a")

    assert get_cap_partition() == CapPartition(replica_count=2, rank=0)


@pytest.mark.asyncio
async def test_refresh_cap_partition_retains_partition_on_failed_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cap_partitioning_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_account_cap_partition_scale_down_seconds=60),
    )
    observe_ring_members(["replica-a", "replica-b"], "replica-a")

    async def _failing_list_active() -> list[str]:
        raise RuntimeError("db unavailable")

    await refresh_cap_partition(_failing_list_active, "replica-a")

    assert get_cap_partition() == CapPartition(replica_count=2, rank=0)


def test_holder_failed_read_restarts_pending_scale_down_window() -> None:
    clock = _Clock()
    holder = CapPartitionHolder(clock=clock)
    holder.observe_members(["replica-a", "replica-b"], "replica-a", scale_down_seconds=60.0)

    # Begin scaling down to one replica: share-growing, so pending.
    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is False

    # A read outage spans the whole window; the gap must restart the window.
    clock.advance(70.0)
    holder.note_failed_read()

    # The next successful lower-count read must not adopt immediately even
    # though the pre-outage observation is now older than the window.
    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is False
    assert holder.current == CapPartition(replica_count=2, rank=0)

    # Only a full fresh window of continuous observation adopts the scale-down.
    clock.advance(60.0)
    assert holder.observe_members(["replica-a"], "replica-a", scale_down_seconds=60.0) is True
    assert holder.current == CapPartition(replica_count=1, rank=0)


@pytest.mark.asyncio
async def test_refresh_cap_partition_failed_read_restarts_pending_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cap_partitioning_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_account_cap_partition_scale_down_seconds=60),
    )
    clock = _Clock()
    monkeypatch.setattr(cap_partitioning_module, "_holder", CapPartitionHolder(clock=clock))

    async def _both_active() -> list[str]:
        return ["replica-a", "replica-b"]

    async def _one_active() -> list[str]:
        return ["replica-a"]

    async def _failing_list_active() -> list[str]:
        raise RuntimeError("db unavailable")

    await refresh_cap_partition(_both_active, "replica-a")
    assert get_cap_partition() == CapPartition(replica_count=2, rank=0)

    # Start the share-growing scale-down window, then lose reads past the window.
    await refresh_cap_partition(_one_active, "replica-a")
    assert get_cap_partition() == CapPartition(replica_count=2, rank=0)
    clock.advance(70.0)
    await refresh_cap_partition(_failing_list_active, "replica-a")
    assert get_cap_partition() == CapPartition(replica_count=2, rank=0)

    # First post-outage read must not adopt: the observation was not continuous.
    await refresh_cap_partition(_one_active, "replica-a")
    assert get_cap_partition() == CapPartition(replica_count=2, rank=0)

    # A full fresh continuous window is required before adopting the smaller share.
    clock.advance(60.0)
    await refresh_cap_partition(_one_active, "replica-a")
    assert get_cap_partition() == CapPartition(replica_count=1, rank=0)


@pytest.mark.asyncio
async def test_refresh_cap_partition_reads_active_members(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cap_partitioning_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_account_cap_partition_scale_down_seconds=60),
    )

    async def _list_active() -> list[str]:
        return ["replica-a", "replica-b", "replica-c"]

    await refresh_cap_partition(_list_active, "replica-b")

    assert get_cap_partition() == CapPartition(replica_count=3, rank=1)
