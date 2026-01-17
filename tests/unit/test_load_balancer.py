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
    result = select_account(states, now=now)
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
    result = select_account(states, now=now)
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
    result = select_account(states, now=now)
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
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_skips_rate_limited_until_reset():
    now = time.time()
    states = [
        AccountState("a", AccountStatus.RATE_LIMITED, used_percent=5.0, reset_at=int(now + 60)),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_handle_rate_limit_sets_reset_at_from_message():
    now = time.time()
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_rate_limit(state, {"message": "Try again in 1.5s"})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at is not None
    delay = state.reset_at - now
    assert 1.2 <= delay <= 2.0


def test_handle_rate_limit_uses_backoff_when_no_delay():
    now = time.time()
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_rate_limit(state, {"message": "Rate limit exceeded."})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at is not None
    delay = state.reset_at - now
    assert 0.15 <= delay <= 0.3


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
