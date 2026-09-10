from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsSnapshot, ResetCreditItem
from app.modules.desktop_resets.projection import ResetPoolUnavailable, pool_credits
from app.modules.rate_limit_reset_credits.store import RateLimitResetCreditsStore

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def snapshot(credit_id, expiry):
    return RateLimitResetCreditsSnapshot(
        available_count=1, credits=[ResetCreditItem(id=credit_id, status="available", expires_at=expiry)]
    )


def test_expiry_order_and_ties_are_deterministic():
    result = pool_credits(
        {
            "c": snapshot("no-expiry", None),
            "b": snapshot("later", NOW + timedelta(days=2)),
            "z": snapshot("tie-z", NOW + timedelta(days=1)),
            "a": snapshot("tie-a", NOW + timedelta(days=1)),
        },
        now=NOW,
    )
    assert [r.credit.id for r in result] == ["tie-a", "tie-z", "later", "no-expiry"]


def test_ambiguous_credit_owners_are_rejected():
    with pytest.raises(ResetPoolUnavailable, match="ambiguous"):
        pool_credits({"a": snapshot("shared", None), "b": snapshot("shared", None)}, now=NOW)


def test_inconsistent_count_is_rejected():
    item = snapshot("credit", None)
    item.available_count = 0
    with pytest.raises(ResetPoolUnavailable, match="count"):
        pool_credits({"a": item}, now=NOW)


@pytest.mark.asyncio
async def test_freshness_expires_without_changing_legacy_cache_reads(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.modules.rate_limit_reset_credits.store.time.monotonic", lambda: now[0])
    store = RateLimitResetCreditsStore()
    item = snapshot("credit", None)
    await store.set("a", item)
    assert store.get_fresh("a", max_age_seconds=180) is item
    now[0] = 280
    assert store.get_fresh("a", max_age_seconds=180) is None
    assert store.get("a") is item
    await store.invalidate("a")
    assert store.get_fresh("a", max_age_seconds=180) is None
