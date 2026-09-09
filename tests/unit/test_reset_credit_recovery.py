from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.reset_credit_recovery import refresh_standard_capacity

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "plan,primary,secondary,expected",
    [
        ("pro", 0, 99, True),
        ("pro", 100, 0, False),
        ("pro", 0, 100, False),
        ("free", None, 0, True),
        ("free", None, 100, False),
        ("pro", None, 0, True),
        ("pro", None, 100, False),
    ],
)
async def test_fresh_standard_windows_determine_exhaustion(plan, primary, secondary, expected):
    account = Account(id="account", plan_type=plan, status=AccountStatus.QUOTA_EXCEEDED)
    repo, updater = AsyncMock(), AsyncMock()
    updater.force_refresh.return_value = True

    async def latest(account_id, *, window):
        pct = primary if window == "primary" else secondary
        if window == "monthly" or pct is None:
            return None
        return UsageHistory(
            account_id=account_id,
            window=window,
            used_percent=pct,
            recorded_at=utcnow(),
            reset_at=int((utcnow() + timedelta(days=1)).timestamp()),
            window_minutes=300 if window == "primary" else 10080,
        )

    repo.latest_entry_for_account.side_effect = latest
    assert await refresh_standard_capacity(account, updater, repo) is expected


async def test_failed_refresh_and_additional_only_usage_cannot_authorize_reset():
    account = Account(id="account", plan_type="free", status=AccountStatus.QUOTA_EXCEEDED)
    repo, updater = AsyncMock(), AsyncMock()
    updater.force_refresh.return_value = False
    assert await refresh_standard_capacity(account, updater, repo) is None
    repo.latest_entry_for_account.assert_not_awaited()
    updater.force_refresh.return_value = True
    repo.latest_entry_for_account.return_value = None
    assert await refresh_standard_capacity(account, updater, repo) is None


async def test_stale_standard_rows_cannot_authorize_reset():
    account = Account(id="account", plan_type="free", status=AccountStatus.QUOTA_EXCEEDED)
    repo, updater = AsyncMock(), AsyncMock()
    updater.force_refresh.return_value = True
    row = UsageHistory(
        account_id="account", window="secondary", used_percent=100, recorded_at=utcnow() - timedelta(hours=1)
    )
    repo.latest_entry_for_account.side_effect = [None, row, None]
    assert await refresh_standard_capacity(account, updater, repo) is None


@pytest.mark.parametrize("used,expected", [(0, True), (100, False)])
async def test_weekly_primary_only_pro_matches_canonical_routing(used, expected):
    account = Account(id="account", plan_type="pro", status=AccountStatus.QUOTA_EXCEEDED)
    repo, updater = AsyncMock(), AsyncMock()
    updater.force_refresh.return_value = True

    async def latest(account_id, *, window):
        if window != "primary":
            return None
        return UsageHistory(
            account_id=account_id,
            window="primary",
            used_percent=used,
            recorded_at=utcnow(),
            reset_at=int((utcnow() + timedelta(days=7)).timestamp()),
            window_minutes=10080,
        )

    repo.latest_entry_for_account.side_effect = latest
    assert await refresh_standard_capacity(account, updater, repo) is expected


async def test_credit_capacity_on_primary_row_suppresses_exhaustion_reset():
    account = Account(id="account", plan_type="pro", status=AccountStatus.QUOTA_EXCEEDED)
    repo, updater = AsyncMock(), AsyncMock()
    updater.force_refresh.return_value = True

    async def latest(account_id, *, window):
        if window == "monthly":
            return None
        return UsageHistory(
            account_id=account_id,
            window=window,
            used_percent=0 if window == "primary" else 100,
            recorded_at=utcnow(),
            window_minutes=300 if window == "primary" else 10080,
            credits_has=True if window == "primary" else None,
        )

    repo.latest_entry_for_account.side_effect = latest
    assert await refresh_standard_capacity(account, updater, repo) is True
