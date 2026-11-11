from datetime import datetime, timezone

import pytest

from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy._load_balancer import quota_recovery
from app.modules.proxy._load_balancer.types import RuntimeState

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "status,account_block,used,recorded_offset,error_offset,expected",
    [
        (AccountStatus.ACTIVE, None, 0.0, -1, -10, True),
        (AccountStatus.PAUSED, None, 0.0, -1, -10, False),
        (AccountStatus.REAUTH_REQUIRED, None, 0.0, -1, -10, False),
        (AccountStatus.DEACTIVATED, None, 0.0, -1, -10, False),
        (AccountStatus.ACTIVE, 1, 0.0, -1, -10, False),
        (AccountStatus.ACTIVE, None, 100.0, -1, -10, False),
        (AccountStatus.ACTIVE, None, 0.0, -15, -10, False),
        (AccountStatus.ACTIVE, None, 0.0, -5, -1, False),
    ],
)
def test_runtime_recovery_requires_available_post_failure_snapshot(
    monkeypatch, status, account_block, used, recorded_offset, error_offset, expected
):
    now = 1_800_000_000.0
    account = Account(id="fixture", status=status, blocked_at=account_block)
    runtime = RuntimeState(blocked_at=now - 20, last_error_at=now + error_offset)
    entry = UsageHistory(
        account_id=account.id,
        used_percent=used,
        reset_at=int(now + 3600),
        recorded_at=datetime.fromtimestamp(now + recorded_offset, timezone.utc).replace(tzinfo=None),
    )
    assert quota_recovery._runtime_quota_recovered(account, runtime, (entry,), now=now) is expected


def test_runtime_recovery_preserves_non_quota_backoff_history():
    now = 1_800_000_000.0
    account = Account(id="mixed", status=AccountStatus.ACTIVE, blocked_at=None, plan_type="plus")
    runtime = RuntimeState(
        blocked_at=now - 20,
        reset_at=now + 3600,
        cooldown_until=now + 120,
        last_error_at=now - 10,
        error_count=5,
        quota_error_count=1,
        last_non_quota_error_at=now - 15,
    )
    entry = UsageHistory(
        account_id=account.id,
        used_percent=0,
        reset_at=int(now + 3600),
        recorded_at=datetime.fromtimestamp(now - 1, timezone.utc).replace(tzinfo=None),
    )
    holds = quota_recovery.recovered_runtime_holds(account, runtime, entry, 300, None, now=now)
    assert holds == (None, None, None)
    assert runtime.error_count == 4
    assert runtime.last_error_at == now - 15
    assert runtime.quota_error_count == 0


@pytest.mark.parametrize(
    "secondary_age,secondary_used,expected_recovered",
    [(30, 100.0, True), (30, 0.0, True), (3, 100.0, False), (1, 100.0, False), (-1, 100.0, False), (1, 0.0, True)],
)
def test_runtime_recovery_uses_only_latest_fetch_siblings(secondary_age, secondary_used, expected_recovered):
    now = 1_800_000_000.0
    account = Account(id="siblings", status=AccountStatus.ACTIVE, blocked_at=None, plan_type="plus")
    runtime = RuntimeState(blocked_at=now - 20, reset_at=now + 3600, cooldown_until=now + 120)
    primary = UsageHistory(
        account_id=account.id,
        window="primary",
        window_minutes=300,
        used_percent=0.0,
        reset_at=int(now + 3600),
        recorded_at=datetime.fromtimestamp(now - 1, timezone.utc).replace(tzinfo=None),
    )
    secondary = UsageHistory(
        account_id=account.id,
        window="secondary",
        window_minutes=10080,
        used_percent=secondary_used,
        reset_at=int(now + 86400),
        recorded_at=datetime.fromtimestamp(now - secondary_age, timezone.utc).replace(tzinfo=None),
    )
    result = quota_recovery.recovered_runtime_holds(account, runtime, primary, 300, secondary, now=now)
    assert (result == (None, None, None)) is expected_recovered


@pytest.mark.parametrize("used,expected_recovered", [(0.0, True), (100.0, False)])
def test_runtime_recovery_accepts_primary_without_duration(used, expected_recovered):
    now = 1_800_000_000.0
    account = Account(id="no-duration", status=AccountStatus.ACTIVE, blocked_at=None, plan_type="plus")
    runtime = RuntimeState(blocked_at=now - 20, reset_at=now + 3600, cooldown_until=now + 120)
    primary = UsageHistory(
        account_id=account.id,
        window="primary",
        window_minutes=None,
        used_percent=used,
        reset_at=int(now + 3600),
        recorded_at=datetime.fromtimestamp(now - 1, timezone.utc).replace(tzinfo=None),
    )
    result = quota_recovery.recovered_runtime_holds(account, runtime, primary, None, None, now=now)
    assert (result == (None, None, None)) is expected_recovered


def test_runtime_recovery_retains_weekly_primary_remap():
    now = 1_800_000_000.0
    account = Account(id="weekly-remap", status=AccountStatus.ACTIVE, blocked_at=None, plan_type="pro")
    runtime = RuntimeState(blocked_at=now - 20, reset_at=now + 3600, cooldown_until=now + 120)
    weekly = UsageHistory(
        account_id=account.id,
        window="primary",
        window_minutes=10080,
        used_percent=0.0,
        reset_at=int(now + 86400),
        recorded_at=datetime.fromtimestamp(now - 1, timezone.utc).replace(tzinfo=None),
    )
    assert quota_recovery.recovered_runtime_holds(account, runtime, weekly, None, weekly, now=now) == (None, None, None)
