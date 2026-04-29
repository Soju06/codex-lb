from __future__ import annotations

import random
import time
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.balancer import (
    AccountState,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    select_account,
)
from app.core.usage.quota import apply_usage_quota
from app.db.models import Account, AccountStatus, StickySessionKind, UsageHistory
from app.modules.proxy.load_balancer import LoadBalancer, RuntimeState, SelectionInputs, _state_from_account
from app.modules.proxy.sticky_repository import StickyRoutingTarget

pytestmark = pytest.mark.unit


def test_select_account_picks_lowest_used_percent():
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=50.0),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states, routing_strategy="usage_weighted")
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
    result = select_account(states, now=now, prefer_earlier_reset=True, routing_strategy="usage_weighted")
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
    result = select_account(states, now=now, prefer_earlier_reset=True, routing_strategy="usage_weighted")
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
    result = select_account(states, now=now, prefer_earlier_reset=True, routing_strategy="usage_weighted")
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
    result = select_account(states, now=now, prefer_earlier_reset=True, routing_strategy="usage_weighted")
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
    result = select_account(states, now=now, prefer_earlier_reset=False, routing_strategy="usage_weighted")
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


def test_handle_quota_exceeded_sets_used_percent_and_cooldown():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_quota_exceeded(state, {})
    assert state.status == AccountStatus.QUOTA_EXCEEDED
    assert state.used_percent == 100.0
    assert state.cooldown_until is not None


def test_handle_permanent_failure_sets_reason():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_permanent_failure(state, "refresh_token_expired")
    assert state.status == AccountStatus.DEACTIVATED
    assert state.deactivation_reason is not None


def test_handle_permanent_failure_sets_reason_for_account_deactivated():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_permanent_failure(state, "account_deactivated")
    assert state.status == AccountStatus.DEACTIVATED
    assert state.deactivation_reason == "Account has been deactivated"


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


def test_apply_usage_quota_clears_quota_exceeded_when_runtime_reset_is_none(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.QUOTA_EXCEEDED,
        primary_used=30.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=None,
        secondary_used=5.0,
        secondary_reset=int(now + 3600),
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 30.0
    assert reset_at is None


def test_apply_usage_quota_clears_rate_limited_when_runtime_reset_is_none(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=10.0,
        primary_reset=int(now + 3600),
        primary_window_minutes=60,
        runtime_reset=None,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 10.0
    assert reset_at is None


def test_quota_exceeded_cooldown_blocks_selection_despite_low_usage():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        cooldown_until=now + 120.0,
    )
    result = select_account([state], now=now)
    assert result.account is None


def test_quota_exceeded_cooldown_allows_selection_after_expiry():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        cooldown_until=now - 1.0,
    )
    result = select_account([state], now=now)
    assert result.account is not None
    assert result.account.account_id == "a"


def _make_test_account(
    account_id: str = "a",
    status: AccountStatus = AccountStatus.ACTIVE,
    reset_at: int | None = None,
    blocked_at: int | None = None,
) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id="chatgpt-" + account_id,
        email=f"{account_id}@test.com",
        plan_type="plus",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"r",
        id_token_encrypted=b"i",
        last_refresh=datetime(2025, 1, 1),
        status=status,
        reset_at=reset_at,
        blocked_at=blocked_at,
    )


def _make_test_usage(
    account_id: str = "a",
    window: str = "secondary",
    used_percent: float = 10.0,
    reset_at: int | None = None,
    recorded_at: datetime | None = None,
    window_minutes: int | None = None,
) -> UsageHistory:
    return UsageHistory(
        id=1,
        account_id=account_id,
        recorded_at=recorded_at or datetime(2025, 1, 1),
        window=window,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=window_minutes if window_minutes is not None else (300 if window == "primary" else 10080),
    )


class _DummyStickyRepository:
    def __init__(self, target: StickyRoutingTarget | None = None) -> None:
        self._target = target

    async def get_target(
        self,
        key: str,
        *,
        kind,
        provider_kind: str,
        max_age_seconds: int | None = None,
    ) -> StickyRoutingTarget | None:
        del key, kind, provider_kind, max_age_seconds
        return self._target


@asynccontextmanager
async def _sticky_repo_factory(target: StickyRoutingTarget | None):
    yield SimpleNamespace(sticky_sessions=_DummyStickyRepository(target))


@pytest.mark.asyncio
async def test_should_fallback_to_platform_for_usage_drain_returns_false_when_one_chatgpt_account_is_healthy():
    balancer = LoadBalancer(lambda: None)
    accounts = [_make_test_account("a"), _make_test_account("b")]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={
            "a": _make_test_usage("a", window="primary", used_percent=90.0),
            "b": _make_test_usage("b", window="primary", used_percent=30.0),
        },
        latest_secondary={
            "a": _make_test_usage("a", window="secondary", used_percent=20.0),
            "b": _make_test_usage("b", window="secondary", used_percent=20.0),
        },
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]

    should_fallback = await balancer.should_fallback_to_platform_for_usage_drain(model="gpt-5.1")

    assert should_fallback is False


@pytest.mark.asyncio
async def test_should_fallback_to_platform_for_usage_drain_returns_true_when_all_candidates_are_drained():
    balancer = LoadBalancer(lambda: None)
    accounts = [_make_test_account("a"), _make_test_account("b")]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={
            "a": _make_test_usage("a", window="primary", used_percent=90.0),
            "b": _make_test_usage("b", window="primary", used_percent=30.0),
        },
        latest_secondary={
            "a": _make_test_usage("a", window="secondary", used_percent=20.0),
            "b": _make_test_usage("b", window="secondary", used_percent=95.0),
        },
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]

    should_fallback = await balancer.should_fallback_to_platform_for_usage_drain(model="gpt-5.1")

    assert should_fallback is True


@pytest.mark.asyncio
async def test_should_fallback_to_platform_for_usage_drain_returns_true_when_remaining_candidate_has_no_usage():
    balancer = LoadBalancer(lambda: None)
    accounts = [_make_test_account("a"), _make_test_account("b")]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={
            "a": _make_test_usage("a", window="primary", used_percent=95.0),
        },
        latest_secondary={
            "a": _make_test_usage("a", window="secondary", used_percent=95.0),
        },
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]

    should_fallback = await balancer.should_fallback_to_platform_for_usage_drain(model="gpt-5.1")

    assert should_fallback is True


@pytest.mark.asyncio
async def test_should_fallback_to_platform_for_usage_drain_returns_true_when_remaining_candidate_has_partial_usage():
    balancer = LoadBalancer(lambda: None)
    accounts = [_make_test_account("a"), _make_test_account("b")]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={
            "a": _make_test_usage("a", window="primary", used_percent=95.0),
            "b": _make_test_usage("b", window="primary", used_percent=10.0),
        },
        latest_secondary={
            "a": _make_test_usage("a", window="secondary", used_percent=95.0),
        },
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]

    should_fallback = await balancer.should_fallback_to_platform_for_usage_drain(model="gpt-5.1")

    assert should_fallback is True


@pytest.mark.asyncio
async def test_should_fallback_to_platform_for_usage_drain_keeps_weekly_only_candidate_healthy():
    balancer = LoadBalancer(lambda: None)
    accounts = [_make_test_account("a")]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={
            "a": _make_test_usage("a", window="primary", used_percent=20.0, window_minutes=10080),
        },
        latest_secondary={},
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]

    should_fallback = await balancer.should_fallback_to_platform_for_usage_drain(model="gpt-5.1")

    assert should_fallback is False


@pytest.mark.asyncio
async def test_should_fallback_to_platform_for_usage_drain_returns_true_when_force_enabled(monkeypatch):
    balancer = LoadBalancer(lambda: None)
    accounts = [_make_test_account("a")]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={"a": _make_test_usage("a", window="primary", used_percent=10.0)},
        latest_secondary={"a": _make_test_usage("a", window="secondary", used_percent=10.0)},
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings",
        lambda: SimpleNamespace(
            platform_fallback_force_enabled=True,
            platform_fallback_primary_remaining_threshold_pct=10.0,
            platform_fallback_secondary_remaining_threshold_pct=5.0,
        ),
    )

    should_fallback = await balancer.should_fallback_to_platform_for_usage_drain(model="gpt-5.1")

    assert should_fallback is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED])
async def test_should_fallback_to_platform_for_usage_drain_returns_true_when_only_non_active_candidates_remain(
    status: AccountStatus,
):
    balancer = LoadBalancer(lambda: None)
    accounts = [_make_test_account("a", status=status, reset_at=int(time.time()) + 3600)]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={"a": _make_test_usage("a", window="primary", used_percent=10.0)},
        latest_secondary={"a": _make_test_usage("a", window="secondary", used_percent=10.0)},
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]

    should_fallback = await balancer.should_fallback_to_platform_for_usage_drain(model="gpt-5.1")

    assert should_fallback is True


@pytest.mark.asyncio
async def test_should_fallback_to_platform_for_usage_drain_returns_true_when_only_candidate_is_in_cooldown():
    balancer = LoadBalancer(lambda: None)
    accounts = [_make_test_account("a")]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={"a": _make_test_usage("a", window="primary", used_percent=10.0)},
        latest_secondary={"a": _make_test_usage("a", window="secondary", used_percent=10.0)},
        runtime_accounts=accounts,
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]
    balancer._runtime["a"] = RuntimeState(cooldown_until=time.time() + 60.0)

    should_fallback = await balancer.should_fallback_to_platform_for_usage_drain(model="gpt-5.1")

    assert should_fallback is True


@pytest.mark.asyncio
async def test_should_fallback_to_platform_for_usage_drain_returns_true_when_only_candidate_is_in_error_backoff():
    balancer = LoadBalancer(lambda: None)
    accounts = [_make_test_account("a")]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={"a": _make_test_usage("a", window="primary", used_percent=10.0)},
        latest_secondary={"a": _make_test_usage("a", window="secondary", used_percent=10.0)},
        runtime_accounts=accounts,
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]
    balancer._runtime["a"] = RuntimeState(error_count=4, last_error_at=time.time() - 1.0)

    should_fallback = await balancer.should_fallback_to_platform_for_usage_drain(model="gpt-5.1")

    assert should_fallback is True


@pytest.mark.asyncio
async def test_sticky_chatgpt_target_is_healthy_for_platform_fallback_respects_rate_limit_grace(monkeypatch):
    now = int(time.time())
    balancer = LoadBalancer(
        lambda: _sticky_repo_factory(
            StickyRoutingTarget(
                provider_kind="chatgpt_web",
                routing_subject_id="a",
                account_id="a",
            )
        )
    )
    accounts = [_make_test_account("a", status=AccountStatus.RATE_LIMITED, reset_at=now + 5)]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={"a": _make_test_usage("a", window="primary", used_percent=10.0, reset_at=now + 5)},
        latest_secondary={"a": _make_test_usage("a", window="secondary", used_percent=10.0, reset_at=now + 86400)},
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings",
        lambda: SimpleNamespace(
            platform_fallback_force_enabled=False,
            platform_fallback_primary_remaining_threshold_pct=10.0,
            platform_fallback_secondary_remaining_threshold_pct=5.0,
            prefer_earlier_reset_accounts=False,
        ),
    )

    sticky_healthy = await balancer.sticky_chatgpt_target_is_healthy_for_platform_fallback(
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        model="gpt-5.1",
    )

    assert sticky_healthy is True


@pytest.mark.asyncio
async def test_sticky_chatgpt_target_is_selectable_for_platform_fallback_ignores_usage_drain_thresholds(
    monkeypatch,
):
    balancer = LoadBalancer(
        lambda: _sticky_repo_factory(
            StickyRoutingTarget(
                provider_kind="chatgpt_web",
                routing_subject_id="a",
                account_id="a",
            )
        )
    )
    accounts = [_make_test_account("a", status=AccountStatus.ACTIVE)]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={"a": _make_test_usage("a", window="primary", used_percent=95.0)},
        latest_secondary={"a": _make_test_usage("a", window="secondary", used_percent=95.0)},
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings",
        lambda: SimpleNamespace(
            platform_fallback_force_enabled=False,
            platform_fallback_primary_remaining_threshold_pct=10.0,
            platform_fallback_secondary_remaining_threshold_pct=5.0,
            prefer_earlier_reset_accounts=False,
        ),
    )

    sticky_healthy = await balancer.sticky_chatgpt_target_is_healthy_for_platform_fallback(
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        model="gpt-5.1",
    )
    sticky_selectable = await balancer.sticky_chatgpt_target_is_selectable_for_platform_fallback(
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        model="gpt-5.1",
    )

    assert sticky_healthy is False
    assert sticky_selectable is True


@pytest.mark.asyncio
async def test_sticky_chatgpt_target_is_healthy_for_platform_fallback_returns_false_when_active_target_is_in_cooldown(
    monkeypatch,
):
    balancer = LoadBalancer(
        lambda: _sticky_repo_factory(
            StickyRoutingTarget(
                provider_kind="chatgpt_web",
                routing_subject_id="a",
                account_id="a",
            )
        )
    )
    accounts = [_make_test_account("a", status=AccountStatus.ACTIVE)]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={"a": _make_test_usage("a", window="primary", used_percent=10.0)},
        latest_secondary={"a": _make_test_usage("a", window="secondary", used_percent=10.0)},
        runtime_accounts=accounts,
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]
    balancer._runtime["a"] = RuntimeState(cooldown_until=time.time() + 60.0)
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings",
        lambda: SimpleNamespace(
            platform_fallback_force_enabled=False,
            platform_fallback_primary_remaining_threshold_pct=10.0,
            platform_fallback_secondary_remaining_threshold_pct=5.0,
            prefer_earlier_reset_accounts=False,
        ),
    )

    sticky_healthy = await balancer.sticky_chatgpt_target_is_healthy_for_platform_fallback(
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        model="gpt-5.1",
    )

    assert sticky_healthy is False


@pytest.mark.asyncio
async def test_sticky_chatgpt_target_is_unhealthy_during_error_backoff(
    monkeypatch,
):
    balancer = LoadBalancer(
        lambda: _sticky_repo_factory(
            StickyRoutingTarget(
                provider_kind="chatgpt_web",
                routing_subject_id="a",
                account_id="a",
            )
        )
    )
    accounts = [_make_test_account("a", status=AccountStatus.ACTIVE)]
    selection_inputs = SelectionInputs(
        accounts=accounts,
        latest_primary={"a": _make_test_usage("a", window="primary", used_percent=10.0)},
        latest_secondary={"a": _make_test_usage("a", window="secondary", used_percent=10.0)},
        runtime_accounts=accounts,
    )

    async def fake_load_selection_inputs(*, model=None, additional_limit_name=None, account_ids=None):
        del model, additional_limit_name, account_ids
        return selection_inputs

    balancer._load_selection_inputs = fake_load_selection_inputs  # type: ignore[method-assign]
    balancer._runtime["a"] = RuntimeState(error_count=4, last_error_at=time.time() - 1.0)
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings",
        lambda: SimpleNamespace(
            platform_fallback_force_enabled=False,
            platform_fallback_primary_remaining_threshold_pct=10.0,
            platform_fallback_secondary_remaining_threshold_pct=5.0,
            prefer_earlier_reset_accounts=False,
        ),
    )

    sticky_healthy = await balancer.sticky_chatgpt_target_is_healthy_for_platform_fallback(
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        model="gpt-5.1",
    )

    assert sticky_healthy is False


def _epoch_to_naive_utc(epoch: float) -> datetime:
    from datetime import timezone

    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


def test_state_from_account_recovers_quota_exceeded_on_restart_without_blocked_at_when_usage_shows_new_reset_window(
    monkeypatch,
):
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    next_reset = int(now + 7200)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=next_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.ACTIVE


def test_state_from_account_keeps_quota_exceeded_on_restart_when_fresh_usage_is_missing_and_no_blocked_at(
    monkeypatch,
):
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 600),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_keeps_quota_exceeded_without_blocked_at_when_usage_stays_on_same_reset_window(
    monkeypatch,
):
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_clears_quota_exceeded_after_restart_with_persisted_blocked_at(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=future_reset,
        blocked_at=int(blocked),
    )
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.ACTIVE
    assert state.blocked_at is None


def test_state_from_account_keeps_quota_exceeded_after_restart_when_persisted_blocked_at_is_recent(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 60.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=future_reset,
        blocked_at=int(blocked),
    )
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_keeps_quota_exceeded_after_restart_when_secondary_usage_is_older_than_block(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=future_reset,
        blocked_at=int(blocked),
    )
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(blocked - 30),
    )

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=RuntimeState(),
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_clears_quota_exceeded_after_cooldown_expiry(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 30),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=runtime,
    )
    assert state.status == AccountStatus.ACTIVE


def test_state_from_account_keeps_quota_exceeded_during_active_cooldown(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 10.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 5),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now + 60.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=runtime,
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_keeps_quota_exceeded_when_usage_is_stale(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 60.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)
    secondary = _make_test_usage(
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(blocked - 30),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=secondary,
        runtime=runtime,
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_keeps_quota_exceeded_when_no_usage_data(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.QUOTA_EXCEEDED, reset_at=future_reset)

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=None,
        secondary_entry=None,
        runtime=runtime,
    )
    assert state.status == AccountStatus.QUOTA_EXCEEDED


def test_state_from_account_rate_limited_checks_primary_freshness(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.RATE_LIMITED, reset_at=future_reset)
    stale_primary = _make_test_usage(
        window="primary",
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(blocked - 30),
    )
    fresh_secondary = _make_test_usage(
        window="secondary",
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=stale_primary,
        secondary_entry=fresh_secondary,
        runtime=runtime,
    )
    assert state.status == AccountStatus.RATE_LIMITED


def test_state_from_account_rate_limited_clears_with_fresh_primary(monkeypatch):
    now = 1_700_000_000.0
    blocked = now - 130.0
    future_reset = int(now + 3600)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    account = _make_test_account(status=AccountStatus.RATE_LIMITED, reset_at=future_reset)
    fresh_primary = _make_test_usage(
        window="primary",
        used_percent=10.0,
        reset_at=future_reset,
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    runtime = RuntimeState()
    runtime.cooldown_until = now - 1.0
    runtime.blocked_at = blocked

    state = _state_from_account(
        account=account,
        primary_entry=fresh_primary,
        secondary_entry=None,
        runtime=runtime,
    )
    assert state.status == AccountStatus.ACTIVE


def test_state_from_account_uses_configured_drain_primary_threshold(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings",
        lambda: SimpleNamespace(
            soft_drain_enabled=True,
            drain_primary_threshold_pct=75.0,
            drain_secondary_threshold_pct=90.0,
            drain_error_window_seconds=60.0,
            drain_error_count_threshold=2,
            probe_quiet_seconds=60.0,
            probe_success_streak_required=3,
        ),
    )

    account = _make_test_account(status=AccountStatus.ACTIVE)
    primary = _make_test_usage(
        window="primary",
        used_percent=80.0,
        reset_at=int(now + 3600),
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    state = _state_from_account(
        account=account,
        primary_entry=primary,
        secondary_entry=None,
        runtime=RuntimeState(),
    )

    assert state.health_tier == 1


def test_state_from_account_uses_configured_probe_quiet_seconds(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings",
        lambda: SimpleNamespace(
            soft_drain_enabled=True,
            drain_primary_threshold_pct=85.0,
            drain_secondary_threshold_pct=90.0,
            drain_error_window_seconds=60.0,
            drain_error_count_threshold=2,
            probe_quiet_seconds=10.0,
            probe_success_streak_required=3,
        ),
    )

    account = _make_test_account(status=AccountStatus.ACTIVE)
    runtime = RuntimeState(
        health_tier=1,
        drain_entered_at=now - 11.0,
        probe_success_streak=0,
    )
    primary = _make_test_usage(
        window="primary",
        used_percent=50.0,
        reset_at=int(now + 3600),
        recorded_at=_epoch_to_naive_utc(now - 10),
    )

    state = _state_from_account(
        account=account,
        primary_entry=primary,
        secondary_entry=None,
        runtime=runtime,
    )

    assert state.health_tier == 2


def test_error_backoff_resets_error_count_when_expired():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        error_count=7,
        last_error_at=now - 400,
    )
    result = select_account([state], now=now)
    assert result.account is not None
    assert result.account.account_id == "a"
    assert state.error_count == 0
    assert state.last_error_at is None


def test_error_backoff_does_not_reset_when_still_active():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        error_count=5,
        last_error_at=now - 60,
    )
    result = select_account([state], now=now)
    assert result.account is None
    assert state.error_count == 5


def test_error_backoff_expired_account_does_not_immediately_relock():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        error_count=7,
        last_error_at=now - 400,
    )
    result = select_account([state], now=now)
    assert result.account is not None
    assert state.error_count == 0

    state.error_count = 2
    state.last_error_at = now + 1

    result2 = select_account([state], now=now + 2)
    assert result2.account is not None
    assert result2.account.account_id == "a"


@pytest.mark.asyncio
async def test_load_selection_inputs_parallelizes_usage_queries():
    """Verify that independent usage queries are parallelized with asyncio.gather()."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.modules.proxy.load_balancer import LoadBalancer

    # Create mock repositories
    mock_accounts_repo = AsyncMock()
    mock_accounts_repo.list_accounts = AsyncMock(return_value=[])

    mock_usage_repo = AsyncMock()

    async def slow_query():
        await asyncio.sleep(0.2)
        return {}

    mock_usage_repo.latest_by_account = AsyncMock(side_effect=slow_query)

    mock_repos = MagicMock()
    mock_repos.accounts = mock_accounts_repo
    mock_repos.usage = mock_usage_repo
    mock_repos.__aenter__ = AsyncMock(return_value=mock_repos)
    mock_repos.__aexit__ = AsyncMock(return_value=None)

    # Create LoadBalancer with mocked repo factory
    balancer = LoadBalancer(repo_factory=lambda: mock_repos)

    # Measure execution time
    start = time.time()
    result = await balancer._load_selection_inputs(model=None)
    elapsed = time.time() - start

    # If queries were sequential, elapsed would be ~0.4s (0.2 + 0.2)
    # If queries are parallel, elapsed should be ~0.2s
    # We use a generous threshold of 0.35s to account for test environment overhead
    assert elapsed < 0.35, f"Queries appear to be sequential (took {elapsed:.3f}s, expected <0.35s)"
    assert result.latest_primary == {}
    assert result.latest_secondary == {}


def test_select_account_capacity_weighted_pro_plus_same_usage_prefers_pro_by_capacity():
    random.seed(11)
    n = 2000
    pro = AccountState(
        "pro",
        AccountStatus.ACTIVE,
        used_percent=50.0,
        secondary_used_percent=10.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=50.0,
        secondary_used_percent=10.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts = {"pro": 0, "plus": 0}
    for _ in range(n):
        result = select_account([pro, plus], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    pro_ratio = counts["pro"] / n
    expected_pro_ratio = 50400.0 / (50400.0 + 7560.0)
    assert abs(pro_ratio - expected_pro_ratio) <= 0.05


def test_select_account_capacity_weighted_same_tier_lower_usage_selected_more():
    random.seed(22)
    n = 2000
    low_usage = AccountState(
        "plus-low",
        AccountStatus.ACTIVE,
        used_percent=20.0,
        secondary_used_percent=20.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )
    high_usage = AccountState(
        "plus-high",
        AccountStatus.ACTIVE,
        used_percent=80.0,
        secondary_used_percent=80.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts = {"plus-low": 0, "plus-high": 0}
    for _ in range(n):
        result = select_account([low_usage, high_usage], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    low_ratio = counts["plus-low"] / n
    expected_low_ratio = 0.8
    assert abs(low_ratio - expected_low_ratio) <= 0.05


def test_select_account_capacity_weighted_all_exhausted_falls_back_deterministically():
    a = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=60.0,
        secondary_used_percent=100.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )
    b = AccountState(
        "b",
        AccountStatus.ACTIVE,
        used_percent=40.0,
        secondary_used_percent=100.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )

    for _ in range(50):
        result = select_account([a, b], routing_strategy="capacity_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"


def test_select_account_capacity_weighted_single_account_always_selected():
    only = AccountState(
        "only",
        AccountStatus.ACTIVE,
        used_percent=77.0,
        secondary_used_percent=55.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    for _ in range(100):
        result = select_account([only], routing_strategy="capacity_weighted")
        assert result.account is not None
        assert result.account.account_id == "only"


def test_select_account_capacity_weighted_zero_capacity_treated_as_zero_weight():
    random.seed(33)
    zero_capacity = AccountState(
        "zero-capacity",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        plan_type="plus",
        capacity_credits=0.0,
    )
    weighted = AccountState(
        "weighted",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    for _ in range(200):
        result = select_account([zero_capacity, weighted], routing_strategy="capacity_weighted")
        assert result.account is not None
        assert result.account.account_id == "weighted"


def test_select_account_capacity_weighted_unknown_plan_uses_conservative_fallback_weight():
    random.seed(34)
    n = 2000
    unknown_plan = AccountState(
        "unknown-plan",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        plan_type="unknown",
        capacity_credits=None,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts = {"unknown-plan": 0, "plus": 0}
    for _ in range(n):
        result = select_account([unknown_plan, plus], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    unknown_ratio = counts["unknown-plan"] / n
    assert 0.05 <= unknown_ratio <= 0.25
    assert counts["plus"] > counts["unknown-plan"]


def test_select_account_capacity_weighted_education_alias_uses_edu_capacity():
    random.seed(35)
    n = 2000
    education = AccountState(
        "education",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        plan_type="education",
        capacity_credits=None,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )

    counts = {"education": 0, "plus": 0}
    for _ in range(n):
        result = select_account([education, plus], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    education_ratio = counts["education"] / n
    assert 0.45 <= education_ratio <= 0.55


def test_select_account_capacity_weighted_three_tiers_distribution_matches_capacity():
    random.seed(44)
    n = 2000
    pro = AccountState(
        "pro",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=0.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=0.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )
    free = AccountState(
        "free",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=0.0,
        plan_type="free",
        capacity_credits=1134.0,
    )

    counts = {"pro": 0, "plus": 0, "free": 0}
    for _ in range(n):
        result = select_account([pro, plus, free], routing_strategy="capacity_weighted")
        assert result.account is not None
        counts[result.account.account_id] += 1

    pro_ratio = counts["pro"] / n
    plus_ratio = counts["plus"] / n
    free_ratio = counts["free"] / n
    total_capacity = 50400.0 + 7560.0 + 1134.0

    assert abs(pro_ratio - (50400.0 / total_capacity)) <= 0.05
    assert abs(plus_ratio - (7560.0 / total_capacity)) <= 0.05
    assert abs(free_ratio - (1134.0 / total_capacity)) <= 0.05
    assert pro_ratio > plus_ratio > free_ratio


def test_select_account_capacity_weighted_prefers_earlier_reset_bucket():
    random.seed(55)
    now = time.time()
    early = AccountState(
        "early",
        AccountStatus.ACTIVE,
        used_percent=80.0,
        secondary_used_percent=80.0,
        secondary_reset_at=int(now + 2 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    late = AccountState(
        "late",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        secondary_reset_at=int(now + 4 * 24 * 3600),
        plan_type="pro",
        capacity_credits=50400.0,
    )

    for _ in range(100):
        result = select_account(
            [early, late],
            now=now,
            prefer_earlier_reset=True,
            routing_strategy="capacity_weighted",
        )
        assert result.account is not None
        assert result.account.account_id == "early"


def test_select_account_capacity_weighted_prefers_capacity_within_same_reset_bucket():
    random.seed(66)
    n = 2000
    now = time.time()
    pro = AccountState(
        "pro",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        secondary_reset_at=int(now + 3 * 3600),
        plan_type="pro",
        capacity_credits=50400.0,
    )
    plus = AccountState(
        "plus",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        secondary_used_percent=10.0,
        secondary_reset_at=int(now + 2 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    late = AccountState(
        "late",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 5 * 24 * 3600),
        plan_type="enterprise",
        capacity_credits=50400.0,
    )

    counts = {"pro": 0, "plus": 0, "late": 0}
    for _ in range(n):
        result = select_account(
            [pro, plus, late],
            now=now,
            prefer_earlier_reset=True,
            routing_strategy="capacity_weighted",
        )
        assert result.account is not None
        counts[result.account.account_id] += 1

    assert counts["late"] == 0
    pro_ratio = counts["pro"] / n
    expected_pro_ratio = 50400.0 / (50400.0 + 7560.0)
    assert abs(pro_ratio - expected_pro_ratio) <= 0.05


def test_select_account_capacity_weighted_with_prefer_deprioritizes_missing_reset():
    random.seed(77)
    now = time.time()
    missing_reset = AccountState(
        "missing-reset",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=None,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    known_reset = AccountState(
        "known-reset",
        AccountStatus.ACTIVE,
        used_percent=95.0,
        secondary_used_percent=95.0,
        secondary_reset_at=int(now + 2 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )

    for _ in range(100):
        result = select_account(
            [missing_reset, known_reset],
            now=now,
            prefer_earlier_reset=True,
            routing_strategy="capacity_weighted",
        )
        assert result.account is not None
        assert result.account.account_id == "known-reset"


def test_select_account_capacity_weighted_with_prefer_falls_back_when_earliest_bucket_zero_weight():
    random.seed(88)
    now = time.time()
    earliest_high_usage = AccountState(
        "earliest-high-usage",
        AccountStatus.ACTIVE,
        used_percent=30.0,
        secondary_used_percent=100.0,
        secondary_reset_at=int(now + 2 * 3600),
        plan_type="plus",
        capacity_credits=7560.0,
    )
    earliest_lower_usage = AccountState(
        "earliest-lower-usage",
        AccountStatus.ACTIVE,
        used_percent=20.0,
        secondary_used_percent=100.0,
        secondary_reset_at=int(now + 3 * 3600),
        plan_type="pro",
        capacity_credits=50400.0,
    )
    later_healthy = AccountState(
        "later-healthy",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 3 * 24 * 3600),
        plan_type="enterprise",
        capacity_credits=50400.0,
    )

    for _ in range(100):
        result = select_account(
            [earliest_high_usage, earliest_lower_usage, later_healthy],
            now=now,
            prefer_earlier_reset=True,
            routing_strategy="capacity_weighted",
        )
        assert result.account is not None
        assert result.account.account_id == "earliest-lower-usage"
