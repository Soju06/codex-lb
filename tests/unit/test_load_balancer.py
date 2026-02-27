from __future__ import annotations

import time

import pytest

from app.core.balancer import (
    AccountState,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    select_account,
)
from app.core.usage.quota import apply_usage_quota
from app.db.models import AccountStatus

pytestmark = pytest.mark.unit


def test_select_account_picks_lowest_used_percent():
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=50.0),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_prefers_earlier_secondary_reset_bucket():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 3 * 24 * 3600),
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            secondary_used_percent=50.0,
            secondary_reset_at=int(now + 2 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=True)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_secondary_reset_is_bucketed_by_day():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=20.0,
            secondary_used_percent=20.0,
            secondary_reset_at=int(now + 23 * 3600),
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 1 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=True)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_prefers_lower_secondary_used_with_same_reset_bucket():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=5.0,
            secondary_used_percent=80.0,
            secondary_reset_at=int(now + 6 * 3600),
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 1 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=True)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_deprioritizes_missing_secondary_reset_at():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=0.0,
            secondary_used_percent=0.0,
            secondary_reset_at=None,
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=90.0,
            secondary_used_percent=90.0,
            secondary_reset_at=int(now + 1 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=True)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_ignores_reset_when_disabled():
    now = time.time()
    states = [
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=int(now + 5 * 24 * 3600),
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            secondary_used_percent=50.0,
            secondary_reset_at=int(now + 1 * 3600),
        ),
    ]
    result = select_account(states, now=now, prefer_earlier_reset=False)
    assert result.account is not None
    assert result.account.account_id == "a"


def test_select_account_skips_rate_limited_until_reset():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.RATE_LIMITED, used_percent=5.0, reset_at=int(now + 60)),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_round_robin_prefers_least_recently_selected():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=90.0, last_selected_at=now - 2),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0, last_selected_at=now - 30),
        AccountState("c", AccountStatus.ACTIVE, used_percent=5.0, last_selected_at=now - 5),
    ]
    result = select_account(states, now=now, routing_strategy="round_robin")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_round_robin_prefers_never_selected():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=1.0, last_selected_at=now - 1),
        AccountState("b", AccountStatus.ACTIVE, used_percent=99.0, last_selected_at=None),
    ]
    result = select_account(states, now=now, routing_strategy="round_robin")
    assert result.account is not None
    assert result.account.account_id == "b"


def test_handle_rate_limit_sets_reset_at_from_message(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_rate_limit(state, {"message": "Try again in 1.5s"})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 1.5)


def test_handle_rate_limit_uses_backoff_when_no_delay(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    monkeypatch.setattr("app.core.balancer.logic.backoff_seconds", lambda _: 0.2)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_rate_limit(state, {"message": "Rate limit exceeded."})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 0.2)


def test_select_account_skips_cooldown_until_expired():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=5.0, cooldown_until=now + 60),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_resets_error_count_when_cooldown_expires():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        cooldown_until=now - 1,
        last_error_at=now - 10,
        error_count=4,
    )
    result = select_account([state], now=now)
    assert result.account is not None
    assert state.cooldown_until is None
    assert state.last_error_at is None
    assert state.error_count == 0


def test_select_account_reports_cooldown_wait_time():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=5.0, cooldown_until=now + 30),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0, cooldown_until=now + 60),
    ]
    result = select_account(states, now=now)
    assert result.account is None
    assert result.error_message is not None
    assert "Try again in" in result.error_message


def test_apply_usage_quota_sets_fallback_reset_for_primary_window(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=100.0,
        primary_reset=None,
        primary_window_minutes=1,
        runtime_reset=None,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 100.0
    assert reset_at is not None
    assert reset_at == pytest.approx(now + 60.0)


def test_handle_quota_exceeded_sets_used_percent():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_quota_exceeded(state, {})
    assert state.status == AccountStatus.QUOTA_EXCEEDED
    assert state.used_percent == 100.0


def test_handle_permanent_failure_sets_reason():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_permanent_failure(state, "refresh_token_expired")
    assert state.status == AccountStatus.DEACTIVATED
    assert state.deactivation_reason is not None


def test_apply_usage_quota_respects_runtime_reset_for_quota_exceeded(monkeypatch):
    now = 1_700_000_000.0
    future = now + 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    # Normally 50% used would reset it to ACTIVE, but runtime_reset is in future
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.QUOTA_EXCEEDED,
        primary_used=50.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=future,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.QUOTA_EXCEEDED
    assert used_percent == 50.0
    assert reset_at == future


def test_apply_usage_quota_respects_runtime_reset_for_rate_limited(monkeypatch):
    now = 1_700_000_000.0
    future = now + 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    # Normally 50% used would reset it to ACTIVE, but runtime_reset is in future
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=50.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=future,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 50.0
    assert reset_at == future


def test_apply_usage_quota_resets_to_active_if_runtime_reset_expired(monkeypatch):
    now = 1_700_000_000.0
    past = now - 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=50.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=past,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 50.0
    assert reset_at is None
