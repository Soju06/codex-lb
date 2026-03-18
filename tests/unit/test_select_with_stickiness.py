"""Unit tests for LoadBalancer._select_with_stickiness cache-affinity fixes.

Covers:
- Fix 1+3: sticky session preservation when pinned account is temporarily down
- Fix 2: grace period for rate-limited accounts with imminent reset
"""

from __future__ import annotations

import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.core.balancer import AccountState, select_account
from app.db.models import AccountStatus, StickySessionKind
from app.modules.proxy.load_balancer import _RECOVERABLE_STATUSES, _STICKY_GRACE_PERIOD_SECONDS

pytestmark = pytest.mark.unit


def _active(account_id: str, used_percent: float = 10.0) -> AccountState:
    return AccountState(account_id, AccountStatus.ACTIVE, used_percent=used_percent)


def _rate_limited(
    account_id: str,
    reset_at: float | None = None,
    cooldown_until: float | None = None,
) -> AccountState:
    return AccountState(
        account_id,
        AccountStatus.RATE_LIMITED,
        reset_at=reset_at,
        cooldown_until=cooldown_until,
        error_count=1,
        last_error_at=time.time(),
    )


def _make_sticky_repo(existing_account_id: str | None = None) -> AsyncMock:
    repo = AsyncMock()
    repo.get_account_id = AsyncMock(return_value=existing_account_id)
    repo.upsert = AsyncMock()
    repo.delete = AsyncMock()
    return repo


async def _select_with_stickiness(
    states: list[AccountState],
    sticky_key: str,
    sticky_repo: AsyncMock,
    *,
    sticky_kind: StickySessionKind = StickySessionKind.PROMPT_CACHE,
    reallocate_sticky: bool = False,
    sticky_max_age_seconds: int | None = 600,
):
    """Inline replica of LoadBalancer._select_with_stickiness for unit testing.

    WARNING: This is a copy of the logic in LoadBalancer._select_with_stickiness.
    If the original method changes, this replica must be updated to match.
    Integration tests in test_proxy_sticky_sessions.py cover the real code path.
    """

    if not sticky_key or not sticky_repo:
        return select_account(states)

    account_map = {s.account_id: True for s in states}

    existing = await sticky_repo.get_account_id(
        sticky_key,
        kind=sticky_kind,
        max_age_seconds=sticky_max_age_seconds,
    )

    persist_fallback = True

    if existing:
        pinned = next((s for s in states if s.account_id == existing), None)
        if pinned is not None:
            pinned_result = select_account(
                [pinned],
                routing_strategy="usage_weighted",
                allow_backoff_fallback=False,
            )
            if pinned_result.account is not None:
                if not reallocate_sticky and sticky_max_age_seconds is not None:
                    await sticky_repo.upsert(sticky_key, pinned.account_id, kind=sticky_kind)
                return pinned_result
            if not reallocate_sticky and pinned.status == AccountStatus.RATE_LIMITED:
                grace_copy = replace(pinned)
                grace_result = select_account(
                    [grace_copy],
                    now=time.time() + _STICKY_GRACE_PERIOD_SECONDS,
                    routing_strategy="usage_weighted",
                    allow_backoff_fallback=False,
                )
                if grace_result.account is not None:
                    if sticky_max_age_seconds is not None:
                        await sticky_repo.upsert(sticky_key, pinned.account_id, kind=sticky_kind)
                    return grace_result
            if reallocate_sticky:
                await sticky_repo.delete(sticky_key, kind=sticky_kind)
            elif pinned.status not in _RECOVERABLE_STATUSES:
                pass
            else:
                persist_fallback = False
        else:
            await sticky_repo.delete(sticky_key, kind=sticky_kind)

    chosen = select_account(states, routing_strategy="usage_weighted")
    if persist_fallback and chosen.account is not None and chosen.account.account_id in account_map:
        await sticky_repo.upsert(sticky_key, chosen.account.account_id, kind=sticky_kind)
    return chosen


# ---------------------------------------------------------------------------
# Fix 1+3: sticky session is preserved when pinned account is temporarily down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_does_not_overwrite_sticky_when_pinned_rate_limited():
    """When the pinned account is rate-limited, a fallback is returned but the
    sticky mapping must NOT be overwritten — so the next request can return to
    the original account once it recovers."""
    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_not_called()
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_all_accounts_unavailable_does_not_overwrite_sticky():
    """When the pinned account is down AND no fallback is available,
    the sticky mapping must still be preserved (not deleted or overwritten)."""
    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is None
    repo.upsert.assert_not_called()
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_overwrites_sticky_when_reallocate_sticky_true():
    """With reallocate_sticky=True (STICKY_THREAD), the sticky session IS
    deleted and the fallback IS persisted."""
    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        sticky_kind=StickySessionKind.STICKY_THREAD,
        reallocate_sticky=True,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once()
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.STICKY_THREAD)


@pytest.mark.asyncio
async def test_sticky_preserved_then_returns_to_original_on_recovery():
    """After a temporary fallback (without overwrite), the NEXT request
    returns to the original account once it recovers."""
    now = time.time()

    # Request 1: pinned account is rate-limited → fallback to b
    acc_a_down = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    r1 = await _select_with_stickiness(
        [acc_a_down, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )
    assert r1.account is not None
    assert r1.account.account_id == "b"
    repo.upsert.assert_not_called()

    # Request 2: original account is back to active
    acc_a_up = _active("a", used_percent=5.0)
    acc_b2 = _active("b", used_percent=20.0)
    repo2 = _make_sticky_repo(existing_account_id="a")

    r2 = await _select_with_stickiness(
        [acc_a_up, acc_b2],
        "key1",
        repo2,
        reallocate_sticky=False,
    )
    assert r2.account is not None
    assert r2.account.account_id == "a"


@pytest.mark.asyncio
async def test_sticky_deleted_when_pinned_account_removed_from_pool():
    """When the pinned account is no longer in the account pool (deleted),
    the sticky session IS deleted and a new mapping IS persisted."""
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once()
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_first_request_creates_sticky_mapping():
    """When no existing sticky mapping exists, the chosen account is persisted."""
    acc_a = _active("a", used_percent=10.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id=None)

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


# ---------------------------------------------------------------------------
# Fix 2: grace period for rate-limited pinned accounts with known reset_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grace_period_returns_pinned_when_reset_imminent():
    """When the pinned account is RATE_LIMITED but resets within the grace
    window, it should be returned optimistically (not the fallback)."""
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 5)  # resets in 5s < grace 10s
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_grace_period_skipped_when_reset_far_away():
    """When the reset_at is well beyond the grace window, the grace period
    should NOT trigger — a fallback should be used instead."""
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 300)  # 5 minutes away
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_grace_period_skipped_for_active_with_cooldown():
    """Accounts that apply_usage_quota resets to ACTIVE (with cooldown still
    active) must NOT be returned via the grace period — this avoids bypassing
    error-handling cooldowns."""
    now = time.time()
    # Simulates the apply_usage_quota path: status=ACTIVE, cooldown set
    acc_a = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        cooldown_until=now + 2,
        error_count=1,
        last_error_at=now,
    )
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"


@pytest.mark.asyncio
async def test_grace_period_not_applied_for_reallocate_sticky():
    """Grace period only applies when reallocate_sticky=False."""
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 5)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        sticky_kind=StickySessionKind.STICKY_THREAD,
        reallocate_sticky=True,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once()


@pytest.mark.asyncio
async def test_grace_period_skipped_when_no_reset_at():
    """When the pinned account is RATE_LIMITED but has no reset_at (unknown
    recovery time), the grace period should not fire."""
    now = time.time()
    acc_a = _rate_limited("a", reset_at=None, cooldown_until=now + 2)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# PR review issue 1: permanently down accounts must rebind sticky session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paused_pinned_account_persists_fallback():
    """PAUSED is permanent — the fallback MUST be persisted so the sticky
    session is rebound instead of pointing at a dead account forever."""
    acc_a = AccountState("a", AccountStatus.PAUSED)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_deactivated_pinned_account_persists_fallback():
    """DEACTIVATED is permanent — same rebind behaviour as PAUSED."""
    acc_a = AccountState("a", AccountStatus.DEACTIVATED, deactivation_reason="token expired")
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


# ---------------------------------------------------------------------------
# PR review issue 2: grace period must not mutate original state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grace_period_does_not_mutate_original_state():
    """The grace-period select_account call must operate on a copy so the
    original AccountState (synced to DB later) is not prematurely flipped
    to ACTIVE."""
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 5)  # within grace window
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    original_status = acc_a.status
    original_reset_at = acc_a.reset_at

    await _select_with_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert acc_a.status == original_status
    assert acc_a.reset_at == original_reset_at
