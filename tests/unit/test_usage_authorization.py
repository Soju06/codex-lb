from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.account_limits import AccountUsageLimitState
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.modules.usage.authorization import (
    OwnerAuthorizationKind,
    authorize_usage_snapshot,
    load_owner_authorization,
)
from app.modules.usage.repository import AccountUsageLimitSnapshot, UsageRepository


@pytest.mark.parametrize("require_active", [False, True])
@pytest.mark.parametrize("status", list(AccountStatus))
def test_disabled_policy_does_not_override_owner_availability(status, require_active):
    snapshot = AccountUsageLimitSnapshot(status, False, None, "plus", None, None, None)
    decision = authorize_usage_snapshot(snapshot, refresh_interval_seconds=60, require_active=require_active)
    unavailable = status in {AccountStatus.PAUSED, AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED}
    unavailable = unavailable or (require_active and status != AccountStatus.ACTIVE)
    assert decision.kind is (
        OwnerAuthorizationKind.OWNER_UNAVAILABLE if unavailable else OwnerAuthorizationKind.ALLOWED
    )
    assert decision.allowed is not unavailable
    assert decision.owner_status is status
    assert decision.snapshot is snapshot


@pytest.mark.parametrize(
    ("used", "age_seconds", "policy_state"),
    [
        (0, 0, AccountUsageLimitState.AVAILABLE),
        (5, 0, AccountUsageLimitState.AVAILABLE),
        (10, 0, AccountUsageLimitState.REACHED),
        (11, 0, AccountUsageLimitState.REACHED),
        (None, 0, AccountUsageLimitState.DATA_UNAVAILABLE),
        (5, 3600, AccountUsageLimitState.DATA_UNAVAILABLE),
    ],
)
def test_owner_decision_preserves_policy_reason(used, age_seconds, policy_state):
    row = UsageWindowRow("owner", used, window_minutes=300, recorded_at=utcnow() - timedelta(seconds=age_seconds))
    snapshot = AccountUsageLimitSnapshot(AccountStatus.ACTIVE, True, 10, "plus", row, None, None)
    decision = authorize_usage_snapshot(snapshot, refresh_interval_seconds=60)
    assert decision.usage_limit_state is policy_state
    assert decision.allowed is (not policy_state.blocks_account_use)
    assert decision.kind is (
        OwnerAuthorizationKind.USAGE_POLICY_BLOCKED
        if policy_state.blocks_account_use
        else OwnerAuthorizationKind.ALLOWED
    )


def test_missing_owner_is_not_a_disabled_policy():
    decision = authorize_usage_snapshot(None, refresh_interval_seconds=60)
    assert decision.kind is OwnerAuthorizationKind.OWNER_UNAVAILABLE
    assert not decision.allowed
    assert decision.usage_limit_state is None


@pytest.mark.asyncio
async def test_authorization_read_failure_is_an_explicit_local_outcome(monkeypatch):
    usage = UsageRepository(AsyncMock(spec=AsyncSession))
    read = AsyncMock(side_effect=RuntimeError("test database unavailable"))
    monkeypatch.setattr(usage, "account_usage_limit_snapshot", read)
    decision = await load_owner_authorization(usage, "owner", refresh_interval_seconds=60)
    assert decision.kind is OwnerAuthorizationKind.AUTHORIZATION_FAILED
    assert not decision.allowed
    assert decision.snapshot is None
    read.assert_awaited_once_with("owner")


@pytest.mark.asyncio
async def test_authorization_read_cancellation_is_not_converted_into_a_policy_decision(monkeypatch):
    usage = UsageRepository(AsyncMock(spec=AsyncSession))
    monkeypatch.setattr(usage, "account_usage_limit_snapshot", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await load_owner_authorization(usage, "owner", refresh_interval_seconds=60)
