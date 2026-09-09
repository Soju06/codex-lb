from unittest.mock import AsyncMock

import pytest

from app.modules.proxy._service.support import (
    _account_selection_recovery_sleep_seconds,
    _sleep_for_account_selection_recovery,
)
from app.modules.proxy.load_balancer import AccountSelection
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining_budget", [0.01, 2.0, 75.0])
@pytest.mark.parametrize("scope_mismatch", [True, False])
async def test_hard_owner_recovery_distinguishes_scope_from_temporary_unavailability(
    remaining_budget: float, scope_mismatch: bool
) -> None:
    clock = VirtualClock(monotonic_value=100.0)
    scheduler = VirtualScheduler(clock)
    heartbeat = AsyncMock()
    selection = AccountSelection(
        account=None,
        error_message="Hard affinity owner account is unavailable",
        error_code="hard_affinity_saturated",
        hard_affinity_scope_mismatch=scope_mismatch,
    )
    assert _account_selection_recovery_sleep_seconds(selection) == (None if scope_mismatch else 2.0)
    task = scheduler.create_task(
        _sleep_for_account_selection_recovery(
            selection,
            request_id="scope-recovery",
            kind="websocket",
            request_stage="first_turn",
            model="gpt-5.1",
            max_sleep_seconds=remaining_budget,
            heartbeat=heartbeat,
            scheduler=scheduler,
            clock=clock,
        )
    )
    try:
        await scheduler.drain()
        if scope_mismatch:
            assert task.done()
            assert await task is False
            assert clock.monotonic() == 100.0
            heartbeat.assert_not_awaited()
        else:
            assert not task.done()
            await scheduler.advance(min(2.0, remaining_budget))
            assert task.done()
            assert await task is True
            heartbeat.assert_awaited()
        assert scheduler.pending_timers == 0
    finally:
        await scheduler.cancel_owned_tasks()
