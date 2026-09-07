"""Virtual-time coverage for the owner-less waits in ``_service/support.py``.

These helpers have no service owner in scope, so they take explicit
``scheduler`` / ``clock`` collaborators. Under the real defaults they are
``asyncio.sleep`` / ``time.monotonic`` verbatim; here the virtual scheduler
proves the wait, the heartbeat cadence and the timestamps all follow the
injected time source.
"""

from __future__ import annotations

import time
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


def test_account_capacity_wait_payload_uses_caller_clock_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    """``waited_seconds`` compares the owner clock's wait start with the owner clock's ``now``."""

    monkeypatch.setattr(time, "monotonic", lambda: 1_000_000.0)
    request_state = SimpleNamespace(request_id="req-wait", account_capacity_wait_started_at=100.0)

    payload = support._account_capacity_wait_payload(
        cast(Any, request_state),
        request_id=None,
        reason="Account capacity exhausted",
        retry_after_seconds=30.0,
        now=125.4,
    )
    owner_less = support._account_capacity_wait_payload(
        None,
        request_id="req-owner-less",
        reason=None,
        retry_after_seconds=None,
        started_at=90.0,
        now=100.0,
    )

    assert payload["waited_seconds"] == 25
    assert payload["request_id"] == "req-wait"
    assert payload["retry_after_seconds"] == 30
    assert owner_less["waited_seconds"] == 10
    assert owner_less["request_id"] == "req-owner-less"


def test_account_capacity_wait_payload_never_reads_the_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1_000_000.0)

    payload = support._account_capacity_wait_payload(
        None,
        request_id="req-default",
        reason=None,
        retry_after_seconds=None,
        started_at=100.0,
        now=130.0,
    )
    unstarted = support._account_capacity_wait_payload(
        None, request_id=None, reason=None, retry_after_seconds=None, now=130.0
    )

    assert payload["waited_seconds"] == 30
    assert unstarted["waited_seconds"] == 0


def test_downstream_websocket_activity_stamps_the_injected_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1_000_000.0)
    clock = VirtualClock(monotonic_value=5.0)

    activity = support._DownstreamWebSocketActivity(clock=clock)
    assert activity.last_activity_at == 5.0
    assert activity.disconnected is False

    clock.advance(2.5)
    activity.mark()
    assert activity.last_activity_at == 7.5

    clock.advance(1.0)
    activity.mark_disconnected()
    assert activity.disconnected is True
    assert activity.last_activity_at == 8.5


def test_downstream_websocket_activity_defaults_to_the_real_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 42.0)

    activity = support._DownstreamWebSocketActivity()

    assert activity.last_activity_at == 42.0
    monkeypatch.setattr(time, "monotonic", lambda: 43.0)
    activity.mark()
    assert activity.last_activity_at == 43.0


def test_record_response_event_stamps_the_caller_clock_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1_000_000.0)
    request_state = SimpleNamespace(
        response_create_attempt=None, last_upstream_activity_at=None, response_event_count=0
    )

    support._record_response_event(cast(Any, request_state), "response.created", now=42.0)
    assert request_state.last_upstream_activity_at == 42.0
    assert request_state.response_event_count == 1

    support._record_response_event(cast(Any, request_state), "response.failed", now=43.0)
    assert request_state.last_upstream_activity_at == 43.0
    assert request_state.response_event_count == 1

    support._record_response_event(cast(Any, request_state), "codex.keepalive", now=44.0)
    assert request_state.last_upstream_activity_at == 43.0


def test_ttft_visibility_helpers_use_the_caller_clock_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1_000_000.0)

    assert support._ttft_event_visible_at("response.output_text.delta", {"delta": "hi"}, now=7.0) == 7.0
    assert support._is_ttft_event("response.output_text.delta", {"delta": "hi"}, now=7.0) is True
    assert support._ttft_event_latency_ms("response.output_text.delta", {"delta": "hi"}, {}, 5.0, now=7.0) == 2000

    pending: dict[tuple[str | None, int | None, int | None], support._TTFTReasoningDeltaState] = {
        ("item", 0, 0): support._TTFTReasoningDeltaState("visible reasoning")
    }
    assert support._finalize_ttft_reasoning_deltas(dict(pending), now=9.0) == 9.0
    assert support._finalize_ttft_latency_ms(dict(pending), 5.0, now=9.0) == 4000
    # A pending reasoning delta finalized by a later non-reasoning event is
    # stamped with the same caller sample.
    assert support._ttft_event_visible_at("response.output_text.delta", {"delta": "x"}, dict(pending), now=9.5) == 9.5
