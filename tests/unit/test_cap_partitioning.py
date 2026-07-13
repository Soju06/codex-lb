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


def test_holder_adopts_same_count_membership_churn_immediately() -> None:
    holder = CapPartitionHolder(clock=_Clock())
    holder.observe_members(["replica-a", "replica-b"], "replica-b", scale_down_seconds=60.0)
    assert holder.current == CapPartition(replica_count=2, rank=1)

    changed = holder.observe_members(["replica-b", "replica-c"], "replica-b", scale_down_seconds=60.0)
    assert changed is True
    assert holder.current == CapPartition(replica_count=2, rank=0)


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
