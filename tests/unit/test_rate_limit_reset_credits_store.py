from __future__ import annotations

import pytest

from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsSnapshot
from app.modules.rate_limit_reset_credits.store import (
    RateLimitResetCreditsStore,
    get_rate_limit_reset_credits_store,
)

pytestmark = pytest.mark.unit


def _snapshot(available_count: int = 1) -> RateLimitResetCreditsSnapshot:
    return RateLimitResetCreditsSnapshot(available_count=available_count)


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
