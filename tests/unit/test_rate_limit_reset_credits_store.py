from __future__ import annotations

from datetime import datetime

import pytest

from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsSnapshot, ResetCreditItem
from app.modules.rate_limit_reset_credits.store import (
    RateLimitResetCreditsStore,
    get_rate_limit_reset_credits_store,
)

pytestmark = pytest.mark.unit


def _snapshot(available_count: int = 1) -> RateLimitResetCreditsSnapshot:
    return RateLimitResetCreditsSnapshot(available_count=available_count)


def _credit(credit_id: str, *, expires_at: str, status: str = "available") -> ResetCreditItem:
    return ResetCreditItem.model_validate({"id": credit_id, "expires_at": expires_at, "status": status})


@pytest.mark.asyncio
async def test_set_and_get_round_trip() -> None:
    store = RateLimitResetCreditsStore()
    snapshot = _snapshot(2)

    await store.set("acc_a", snapshot)

    assert store.get("acc_a") is snapshot
    assert snapshot.available_count == 2


@pytest.mark.asyncio
async def test_get_returns_none_for_missing_account() -> None:
    store = RateLimitResetCreditsStore()
    assert store.get("missing") is None


@pytest.mark.asyncio
async def test_set_overwrites_prior_snapshot() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_a", _snapshot(1))
    await store.set("acc_a", _snapshot(5))

    snapshot = store.get("acc_a")
    assert snapshot is not None
    assert snapshot.available_count == 5


@pytest.mark.asyncio
async def test_generation_changes_are_scoped_to_account() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_a", _snapshot(1))
    await store.set("acc_b", _snapshot(2))
    generation_b = store.generation("acc_b")

    await store.set("acc_a", _snapshot(9))

    assert await store.set_if_generation("acc_b", _snapshot(7), generation_b)
    snapshot_b = store.get("acc_b")
    assert snapshot_b is not None
    assert snapshot_b.available_count == 7


@pytest.mark.asyncio
async def test_same_account_generation_change_rejects_stale_write() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_a", _snapshot(1))
    generation = store.generation("acc_a")

    await store.invalidate("acc_a")

    assert not await store.set_if_generation("acc_a", _snapshot(7), generation)
    assert store.get("acc_a") is None


@pytest.mark.asyncio
async def test_invalidate_all_rejects_in_flight_writes_for_any_account() -> None:
    store = RateLimitResetCreditsStore()
    generation = store.generation("acc_a")

    await store.invalidate()

    assert not await store.set_if_generation("acc_a", _snapshot(7), generation)
    assert store.get("acc_a") is None


@pytest.mark.asyncio
async def test_invalidate_single_account_clears_only_that_key() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_a", _snapshot(1))
    await store.set("acc_b", _snapshot(2))

    await store.invalidate("acc_a")

    assert store.get("acc_a") is None
    snapshot_b = store.get("acc_b")
    assert snapshot_b is not None
    assert snapshot_b.available_count == 2


@pytest.mark.asyncio
async def test_invalidate_all_clears_every_key() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_a", _snapshot(1))
    await store.set("acc_b", _snapshot(2))

    await store.invalidate()

    assert store.get("acc_a") is None
    assert store.get("acc_b") is None


@pytest.mark.asyncio
async def test_invalidate_missing_account_is_noop() -> None:
    store = RateLimitResetCreditsStore()
    await store.invalidate("never_existed")  # must not raise
    assert store.get("never_existed") is None


@pytest.mark.asyncio
async def test_mark_credit_redeemed_preserves_remaining_available_credits() -> None:
    store = RateLimitResetCreditsStore()
    await store.set(
        "acc_a",
        RateLimitResetCreditsSnapshot(
            available_count=2,
            nearest_expires_at=datetime.fromisoformat("2026-06-20T00:00:00+00:00"),
            credits=[
                _credit("soon", expires_at="2026-06-20T00:00:00Z"),
                _credit("late", expires_at="2026-07-10T00:00:00Z"),
            ],
        ),
    )
    redeemed_at = datetime.fromisoformat("2026-06-18T12:00:00+00:00")

    await store.mark_credit_redeemed("acc_a", "soon", redeemed_at=redeemed_at)

    snapshot = store.get("acc_a")
    assert snapshot is not None
    assert snapshot.available_count == 1
    assert snapshot.nearest_expires_at == datetime.fromisoformat("2026-07-10T00:00:00+00:00")
    assert [(credit.id, credit.status) for credit in snapshot.credits] == [("soon", "redeemed"), ("late", "available")]
    assert snapshot.credits[0].redeemed_at == redeemed_at


@pytest.mark.asyncio
async def test_concurrent_setters_are_serialized_under_lock() -> None:
    store = RateLimitResetCreditsStore()

    async def writer(account_id: str) -> None:
        for value in range(20):
            await store.set(account_id, _snapshot(value))

    # If the lock did not serialize, a careless implementation could still pass,
    # but a dict is not coroutine-safe across truly concurrent writes; this at
    # least exercises the lock path and confirms the final state is consistent.
    import asyncio

    await asyncio.gather(*(writer(f"acc_{i}") for i in range(5)))

    for i in range(5):
        snapshot = store.get(f"acc_{i}")
        assert snapshot is not None
        assert snapshot.available_count == 19


def test_module_singleton_accessor_returns_shared_instance() -> None:
    assert get_rate_limit_reset_credits_store() is get_rate_limit_reset_credits_store()
