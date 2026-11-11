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
