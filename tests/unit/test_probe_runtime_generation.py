import pytest

from app.db.models import Account, AccountStatus
from app.modules.proxy.load_balancer import RuntimeState, _state_from_account

pytestmark = pytest.mark.unit


def test_stale_recovered_row_cannot_clear_newer_runtime_rejection():
    account = Account(id="held", status=AccountStatus.ACTIVE, plan_type="pro", block_generation=2)
    runtime = RuntimeState(block_generation=3, blocked_at=1000, reset_at=2000, cooldown_until=2000)
    _state_from_account(account=account, primary_entry=None, secondary_entry=None, runtime=runtime, now=1100)
    assert runtime.block_generation == 3
    assert runtime.blocked_at == 1000
    assert runtime.reset_at == 2000
    assert runtime.cooldown_until == 2000
