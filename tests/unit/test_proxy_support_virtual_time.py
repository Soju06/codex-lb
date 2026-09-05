"""Virtual-time coverage for the owner-less waits in ``_service/support.py``.

These helpers have no service owner in scope, so they take explicit
``scheduler`` / ``clock`` collaborators. Under the real defaults they are
``asyncio.sleep`` / ``time.monotonic`` verbatim; here the virtual scheduler
proves the wait, the heartbeat cadence and the timestamps all follow the
injected time source.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest

from app.modules.proxy._service import support
from app.modules.proxy.load_balancer import AccountSelection
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_account_selection_recovery_sleep_follows_virtual_time(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = VirtualClock(monotonic_value=100.0)
    scheduler = VirtualScheduler(clock)
    monkeypatch.setattr(support, "_account_selection_recovery_sleep_seconds", lambda _selection: 25.0)
    heartbeats: list[float] = []

    async def heartbeat(remaining: float) -> None:
        heartbeats.append(remaining)

    request_state = SimpleNamespace(
        account_capacity_waiting=False,
        account_capacity_wait_reason=None,
        account_capacity_wait_started_at=None,
        account_capacity_wait_retry_after_seconds=None,
    )
    sleeper = scheduler.create_task(
        support._sleep_for_account_selection_recovery(
            AccountSelection(None, "Account capacity exhausted"),
            request_id="req-virtual-recovery",
            kind="websocket",
            request_stage="selection",
            model="gpt-5.5",
            max_sleep_seconds=30.0,
            request_state=cast(Any, request_state),
            heartbeat=heartbeat,
            scheduler=scheduler,
            clock=clock,
        )
    )

    await scheduler.drain()
    assert not sleeper.done()
    assert request_state.account_capacity_waiting is True
    assert request_state.account_capacity_wait_started_at == 100.0
    assert request_state.account_capacity_wait_retry_after_seconds == 25.0
    await scheduler.advance(support._ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS)
    assert not sleeper.done()
    await scheduler.advance(25.0 - support._ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS)

    assert await sleeper is True
    assert heartbeats == [25.0, 15.0, 5.0]
    assert clock.monotonic() == pytest.approx(125.0)
    assert request_state.account_capacity_waiting is False
    assert request_state.account_capacity_wait_retry_after_seconds is None


@pytest.mark.asyncio
async def test_websocket_continuity_gap_wait_follows_virtual_time() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    pending: deque[Any] = deque([object()])
    waiter = scheduler.create_task(
        support._wait_for_websocket_continuity_gap(
            pending,
            pending_lock=anyio.Lock(),
            timeout_seconds=1.0,
            scheduler=scheduler,
            clock=clock,
        )
    )

    await scheduler.advance(0.5)
    assert not waiter.done()
    pending.clear()
    await scheduler.advance(support._WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS)

    assert await waiter is True

    pending.append(object())
    timed_out = scheduler.create_task(
        support._wait_for_websocket_continuity_gap(
            pending,
            pending_lock=anyio.Lock(),
            timeout_seconds=1.0,
            scheduler=scheduler,
            clock=clock,
        )
    )
    await scheduler.advance(0.999)
    assert not timed_out.done()
    await scheduler.advance(0.001)

    assert await timed_out is False
