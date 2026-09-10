"""Retry-circuit cleanup keeps its ownership and deadlines under virtual time."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service import support
from app.modules.proxy._service.http_bridge import request_submit
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler
from tests.unit.test_proxy_http_bridge import _make_bridge_session

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_remote_probe_lookup_expires_at_virtual_caller_deadline() -> None:
    clock = VirtualClock(monotonic_value=100.0)
    scheduler = VirtualScheduler(clock)
    service = proxy_service.ProxyService(cast(Any, nullcontext()), clock=clock, scheduler=scheduler)
    key = proxy_service._HTTPBridgeSessionKey("session_header", "virtual-probe", None)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def lookup(**_kwargs: Any) -> Any:
        started.set()
        try:
            await scheduler.sleep(60.0)
        finally:
            cancelled.set()

    service._durable_bridge = SimpleNamespace(lookup_retry_circuit=lookup)
    generation = support._HTTPBridgeRetryCircuitGeneration(
        admission_generation=3,
        persisted_updated_at_epoch=1000.0,
        persisted_consecutive_failures=2,
        durable_cooldown_until_epoch=0.0,
        local_consecutive_failures=0,
        last_failure_monotonic=0.0,
        local_cooldown_until=0.0,
    )
    task = scheduler.create_task(service._http_bridge_claim_miss_shows_remote_probe(key, generation, deadline=100.25))
    try:
        await scheduler.drain()
        assert started.is_set()
        await scheduler.advance(0.24)
        assert not task.done()
        await scheduler.advance(0.01)
        assert task.done()
        assert task.result() is False
        assert cancelled.is_set()
        assert all(owned.done() for owned in scheduler.owned_tasks)
        assert scheduler.pending_timers == 0
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_account_release", [False, True], ids=["error", "cancel"])
@pytest.mark.parametrize("release_confirmed", [True, False], ids=["confirmed", "unconfirmed"])
async def test_interrupted_submit_owns_virtual_claim_release(
    monkeypatch: pytest.MonkeyPatch,
    cancel_account_release: bool,
    release_confirmed: bool,
) -> None:
    clock = VirtualClock(monotonic_value=100.0)
    scheduler = VirtualScheduler(clock)
    service = proxy_service.ProxyService(cast(Any, nullcontext()), clock=clock, scheduler=scheduler)
    session = _make_bridge_session(key_value="virtual-cleanup")
    account_started = asyncio.Event()
    claim_started = asyncio.Event()
    original_error = RuntimeError("account release failed")

    async def release_account(_lease: Any) -> None:
        account_started.set()
        if cancel_account_release:
            await scheduler.sleep(60.0)
        raise original_error

    clear_calls: list[dict[str, Any]] = []

    async def clear_claim(**kwargs: Any) -> bool:
        clear_calls.append(kwargs)
        claim_started.set()
        await scheduler.sleep(0.25)
        return release_confirmed

    monkeypatch.setattr(service, "_clear_http_bridge_retry_circuit_admission_claim", clear_claim)
    retry = Mock()
    monkeypatch.setattr(request_submit, "_schedule_http_bridge_retry_circuit_admission_claim_release_retry", retry)
    state = proxy_service._WebSocketRequestState(
        request_id="virtual-cleanup",
        model="gpt-5.6-luna",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=clock.monotonic(),
        transport="http",
        response_create_gate=asyncio.Semaphore(1),
        account_response_create_lease=proxy_service.AccountLease(
            lease_id="virtual-lease", account_id="acc-bridge", kind="stream", acquired_at=1.0
        ),
        account_response_create_release=release_account,
        awaiting_response_created=True,
        verified_stale_anchor_retry_circuit_key=session.key,
        verified_stale_anchor_retry_circuit_claimed_generation=4,
        verified_stale_anchor_retry_circuit_claimed_at_epoch=1000.0,
        verified_stale_anchor_retry_circuit_claimed_until_epoch=1100.0,
        verified_stale_anchor_retry_circuit_claimed_attempt_count=0,
    )
    task = scheduler.create_task(
        service._cleanup_http_bridge_submit_interruption(
            session, request_state=state, gate_acquired=False, request_enqueued=False, counted_in_queue=False
        )
    )
    try:
        await scheduler.drain()
        assert account_started.is_set()
        if cancel_account_release:
            task.cancel()
            await scheduler.drain()
        assert claim_started.is_set()
        assert any(
            owned.get_name() == "http-bridge-retry-circuit-submit-cleanup-virtual-cleanup" and not owned.done()
            for owned in scheduler.owned_tasks
        )
        assert state.verified_stale_anchor_retry_circuit_claimed_generation == 4
        await scheduler.advance(0.24)
        assert not task.done()
        await scheduler.advance(0.01)
        assert task.done()
        if cancel_account_release:
            with pytest.raises(asyncio.CancelledError):
                task.result()
        else:
            assert task.exception() is original_error
        assert clear_calls == [
            dict(key=session.key, claimed_generation=4, claimed_at_epoch=1000.0, claimed_until_epoch=1100.0)
        ]
        assert state.verified_stale_anchor_retry_circuit_claimed_generation == (None if release_confirmed else 4)
        assert state.verified_stale_anchor_retry_circuit_claimed_at_epoch == (None if release_confirmed else 1000.0)
        assert state.verified_stale_anchor_retry_circuit_claimed_until_epoch == (None if release_confirmed else 1100.0)
        if release_confirmed:
            retry.assert_not_called()
        else:
            retry.assert_called_once_with(service, state)
        assert all(owned.done() for owned in scheduler.owned_tasks)
        assert scheduler.pending_timers == 0
    finally:
        await scheduler.cancel_owned_tasks()
