"""Unit tests for LoadBalancer._select_with_stickiness cache-affinity fixes.

Covers:
- Fix 1+3: sticky session preservation when pinned account is temporarily down
- Fix 2: grace period for rate-limited accounts with imminent reset
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.core.balancer import AccountState, RoutingStrategy
from app.db.models import Account, AccountStatus, StickySessionKind
from app.modules.proxy.load_balancer import LoadBalancer

pytestmark = pytest.mark.unit


def _active(
    account_id: str,
    used_percent: float = 10.0,
    secondary_used_percent: float | None = None,
) -> AccountState:
    return AccountState(
        account_id,
        AccountStatus.ACTIVE,
        used_percent=used_percent,
        secondary_used_percent=secondary_used_percent,
    )


def _rate_limited(
    account_id: str,
    reset_at: float | None = None,
    cooldown_until: float | None = None,
    used_percent: float | None = None,
) -> AccountState:
    return AccountState(
        account_id,
        AccountStatus.RATE_LIMITED,
        used_percent=used_percent,
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


async def _invoke_stickiness(
    states: list[AccountState],
    sticky_key: str,
    sticky_repo: AsyncMock,
    *,
    sticky_kind: StickySessionKind = StickySessionKind.PROMPT_CACHE,
    reallocate_sticky: bool = False,
    sticky_max_age_seconds: int | None = 600,
    sticky_budget_reallocation_enabled: bool = True,
    budget_threshold_pct: float = 95.0,
    routing_strategy: RoutingStrategy = "usage_weighted",
):
    """Wrapper that calls production LoadBalancer._select_with_stickiness.

    This delegates to the real implementation in LoadBalancer to avoid
    maintaining a replica that can drift from production.
    """

    @asynccontextmanager
    async def mock_repo_factory():
        yield AsyncMock()

    lb = LoadBalancer(mock_repo_factory)
    account_map = {s.account_id: cast(Account, AsyncMock()) for s in states}

    return await lb._select_with_stickiness(
        states=states,
        account_map=account_map,
        sticky_key=sticky_key,
        sticky_kind=sticky_kind,
        reallocate_sticky=reallocate_sticky,
        sticky_max_age_seconds=sticky_max_age_seconds,
        sticky_budget_reallocation_enabled=sticky_budget_reallocation_enabled,
        budget_threshold_pct=budget_threshold_pct,
        prefer_earlier_reset_accounts=False,
        routing_strategy=routing_strategy,
        sticky_repo=sticky_repo,
    )


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

    result = await _invoke_stickiness(
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

    result = await _invoke_stickiness(
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

    result = await _invoke_stickiness(
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

    r1 = await _invoke_stickiness(
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

    r2 = await _invoke_stickiness(
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

    result = await _invoke_stickiness(
        [acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


# ---------------------------------------------------------------------------
# Pool exhaustion guard — prevent thrashing when all accounts are depleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_accounts_exhausted_keeps_pinned_no_thrashing():
    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=97.0)
    acc_c = _active("c", used_percent=98.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b, acc_c],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_pool_exhausted_but_better_candidate_exists_reallocates():
    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=50.0)
    acc_c = _active("c", used_percent=97.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b, acc_c],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_round_robin_pool_health_check_prefers_budget_safe_candidate():
    now = time.time()
    acc_a = AccountState("a", AccountStatus.ACTIVE, used_percent=96.0, last_selected_at=now - 10)
    acc_b = AccountState("b", AccountStatus.ACTIVE, used_percent=50.0, last_selected_at=now - 1)
    acc_c = AccountState("c", AccountStatus.ACTIVE, used_percent=97.0, last_selected_at=now - 100)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b, acc_c],
        "key-round-robin",
        repo,
        reallocate_sticky=False,
        routing_strategy="round_robin",
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key-round-robin", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key-round-robin", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_capacity_weighted_pool_health_check_prefers_budget_safe_candidate():
    acc_a = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=96.0,
        secondary_used_percent=96.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    acc_b = AccountState(
        "b",
        AccountStatus.ACTIVE,
        used_percent=50.0,
        secondary_used_percent=50.0,
        plan_type="free",
        capacity_credits=1134.0,
    )
    acc_c = AccountState(
        "c",
        AccountStatus.ACTIVE,
        used_percent=97.0,
        secondary_used_percent=97.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b, acc_c],
        "key-capacity-weighted",
        repo,
        reallocate_sticky=False,
        routing_strategy="capacity_weighted",
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key-capacity-weighted", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key-capacity-weighted", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_pool_exhausted_single_account_keeps_pinned():
    acc_a = _active("a", used_percent=96.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_pool_exhausted_with_custom_threshold():
    acc_a = _active("a", used_percent=85.0)
    acc_b = _active("b", used_percent=88.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
        budget_threshold_pct=80.0,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_pool_exhausted_candidate_with_none_usage_triggers_reallocation():
    acc_a = _active("a", used_percent=96.0)
    acc_b = AccountState("b", AccountStatus.ACTIVE, used_percent=None)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_first_request_creates_sticky_mapping():
    """When no existing sticky mapping exists, the chosen account is persisted."""
    acc_a = _active("a", used_percent=10.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id=None)

    result = await _invoke_stickiness(
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

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_grace_period_keeps_rate_limited_pinned_account_even_when_usage_is_100_pct():
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 5, used_percent=100.0)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_grace_period_skipped_when_reset_far_away():
    """When the reset_at is well beyond the grace window, the grace period
    should NOT trigger — a fallback should be used instead."""
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 300)  # 5 minutes away
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
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

    result = await _invoke_stickiness(
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

    result = await _invoke_stickiness(
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

    result = await _invoke_stickiness(
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

    result = await _invoke_stickiness(
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

    result = await _invoke_stickiness(
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

    await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert acc_a.status == original_status
    assert acc_a.reset_at == original_reset_at


@pytest.mark.asyncio
async def test_codex_session_persists_fallback_during_outage():
    """CODEX_SESSION is durable (no TTL). When the pinned account is
    temporarily down, the fallback MUST be persisted so the session
    sticks to one account instead of bouncing across random fallbacks."""
    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_called_once_with("session_123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_rate_limit_far_away_does_not_reallocate_codex_session_affinity():
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 1200)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("session_123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_rate_limit_far_away_without_fallback_preserves_codex_session_affinity():
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 1200)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    )

    assert result.account is None
    repo.delete.assert_not_called()
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_budget_exhaustion_triggers_reallocation():
    """When the pinned account has < 5% budget remaining (used_percent >= 95%),
    it should be reallocated to a different account."""
    acc_a = _active("a", used_percent=96.0)  # 96% used = < 4% remaining
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_budget_threshold_80_triggers_at_85_percent():
    acc_a = _active("a", used_percent=85.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
        budget_threshold_pct=80.0,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_budget_threshold_95_no_reallocation_at_85_percent():
    acc_a = _active("a", used_percent=85.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_budget_threshold_reallocates_codex_session_affinity():
    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "codex-session-123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("codex-session-123", kind=StickySessionKind.CODEX_SESSION)
    repo.upsert.assert_called_once_with("codex-session-123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_budget_threshold_can_preserve_codex_session_affinity():
    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "codex-session-123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        sticky_budget_reallocation_enabled=False,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_budget_threshold_reallocates_to_primary_safe_secondary_pressured_candidate():
    acc_a = _active("a", used_percent=99.0, secondary_used_percent=1.0)
    acc_b = _active("b", used_percent=10.0, secondary_used_percent=99.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "codex-session-123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("codex-session-123", kind=StickySessionKind.CODEX_SESSION)
    repo.upsert.assert_called_once_with("codex-session-123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_budget_threshold_preserves_sticky_when_every_candidate_secondary_pressured():
    acc_a = _active("a", used_percent=10.0, secondary_used_percent=99.0)
    acc_b = _active("b", used_percent=20.0, secondary_used_percent=99.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "codex-session-123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_far_away_triggers_reallocation():
    """When the pinned account's rate limit reset is more than 10 minutes away,
    it should be reallocated to avoid waiting."""
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 900)  # resets in 15 minutes > 10 min threshold
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)
