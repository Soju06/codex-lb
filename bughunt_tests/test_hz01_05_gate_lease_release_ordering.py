"""Regression coverage for cancellation-safe WebSocket create cleanup.

``_release_websocket_response_create_gate`` (app/modules/proxy/_service/websocket/
helpers.py:1574) clears the account-lease fields, then awaits
``release_account_lease`` (which takes ``LoadBalancer._runtime_lock``,
app/modules/proxy/load_balancer.py:283) and only then releases the per-session
``response_create_gate`` semaphore (helpers.py:1592).

The account lease release is shielded so cancellation while the load-balancer
runtime lock is contended cannot orphan the account slot. The per-session gate
still follows the helper's existing release semantics.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._load_balancer.types import AccountLease
from app.modules.proxy._service.websocket import helpers as ws_helpers
from app.modules.proxy.load_balancer import LoadBalancer


def _make_request_state(gate: asyncio.Semaphore, lease: AccountLease, release):
    state = proxy_service._WebSocketRequestState(
        request_id="req_hz02_gate_leak",
        model="gpt-5.6-sol",
        service_tier="priority",
        reasoning_effort="high",
        api_key_reservation=None,
        started_at=time.monotonic(),
    )
    state.response_create_gate = gate
    state.response_create_gate_acquired = True
    state.awaiting_response_created = True
    state.account_response_create_lease = lease
    state.account_response_create_release = release
    return state


@pytest.mark.asyncio
async def test_hz02_account_lease_is_returned_when_release_is_cancelled():
    """Cancellation at load_balancer.py:283 does not orphan the account slot."""
    load_balancer = LoadBalancer(repo_factory=None)  # type: ignore[arg-type]
    lease = AccountLease(
        lease_id="lease_hz02",
        account_id="acc_hz02",
        kind="response_create",
        acquired_at=time.monotonic(),
    )

    gate = asyncio.Semaphore(1)
    await gate.acquire()
    assert gate.locked() is True

    state = _make_request_state(gate, lease, load_balancer.release_account_lease)

    # Contend the REAL LoadBalancer._runtime_lock, exactly as a concurrent
    # account selection / lease acquire / pressure snapshot would.
    await load_balancer._runtime_lock.acquire()
    try:
        releaser = asyncio.create_task(
            ws_helpers._release_websocket_response_create_gate(state, gate)
        )
        # Let it reach `async with self._runtime_lock` in release_account_lease.
        for _ in range(5):
            await asyncio.sleep(0)
        assert not releaser.done()

        # The reader task is routinely cancelled here: _reconnect_http_bridge_session
        # calls _await_cancelled_task(old_reader) while reusing the SAME session.
        releaser.cancel()
        with pytest.raises(asyncio.CancelledError):
            await releaser
    finally:
        load_balancer._runtime_lock.release()

    # The request state is cleared before cleanup, but the shielded release
    # continues and returns the captured lease to the load balancer.
    assert state.account_response_create_lease is None
    assert state.account_response_create_release is None
    await asyncio.sleep(0)
    inflight_create, _, _ = await load_balancer.account_pressure_snapshot("acc_hz02")
    assert inflight_create == 0
    assert gate.locked() is True


@pytest.mark.asyncio
async def test_hz02_cancelled_cleanup_does_not_hold_the_account_cap():
    """A cancelled cleanup returns the slot before stale-TTL reclamation."""
    from app.modules.proxy._load_balancer.types import AccountConcurrencyCaps

    load_balancer = LoadBalancer(repo_factory=None)  # type: ignore[arg-type]
    caps = AccountConcurrencyCaps(response_create_limit=4, stream_limit=64)

    gate = asyncio.Semaphore(1)
    await gate.acquire()

    lease = await load_balancer.acquire_account_lease(
        "acc_hz02_orphan", kind="response_create", concurrency_caps=caps
    )
    assert lease is not None
    state = _make_request_state(gate, lease, load_balancer.release_account_lease)

    await load_balancer._runtime_lock.acquire()
    try:
        releaser = asyncio.create_task(
            ws_helpers._release_websocket_response_create_gate(state, gate)
        )
        for _ in range(5):
            await asyncio.sleep(0)
        releaser.cancel()
        with pytest.raises(asyncio.CancelledError):
            await releaser
    finally:
        load_balancer._runtime_lock.release()

    assert state.account_response_create_lease is None
    inflight_create, _, _ = await load_balancer.account_pressure_snapshot("acc_hz02_orphan")
    assert inflight_create == 0


@pytest.mark.asyncio
async def test_hz04_hz05_stuck_gate_recovery_is_blind_to_a_leaked_gate():
    """The gate-timeout recovery keys on stale *pending* requests.

    A leaked gate has no pending request at all, so
    ``_classify_http_bridge_stale_gate_holders``
    (app/modules/proxy/_service/http_bridge/request_submit.py:1764) returns
    "nothing to fail, do not retire", and service.py:1370-1384 does nothing.
    The `http_bridge_stuck_watchdog_skipped` warning at service.py:1334 is
    likewise gated on a non-empty pending list, so the wedge is silent.
    """
    service = proxy_service.ProxyService.__new__(proxy_service.ProxyService)

    stale, retire = proxy_service.ProxyService._classify_http_bridge_stale_gate_holders(
        service,
        [],  # a leaked gate leaves ZERO pending requests behind
        now=time.monotonic(),
        threshold_seconds=300.0,
        session_closed=False,
    )
    assert stale == []
    assert retire is False


@pytest.mark.asyncio
async def test_hz02_queued_gate_waiters_exhaust_the_account_response_create_cap():
    """Leases are taken BEFORE the gate (service.py:1289 vs 1297).

    Defaults: proxy_account_response_create_limit=4 (settings.py:417) but
    http_responses_session_bridge_queue_limit=8 (settings.py:301).  Four
    requests merely QUEUED behind one session's Semaphore(1) consume the whole
    account cap, so waiter #5 gets a hard 429 instead of queueing.
    """
    from app.modules.proxy._load_balancer.types import AccountConcurrencyCaps

    load_balancer = LoadBalancer(repo_factory=None)  # type: ignore[arg-type]
    caps = AccountConcurrencyCaps(response_create_limit=4, stream_limit=64)

    leases = []
    for _ in range(4):
        lease = await load_balancer.acquire_account_lease(
            "acc_hz02_cap", kind="response_create", concurrency_caps=caps
        )
        assert lease is not None
        leases.append(lease)

    # Waiter #5 -- still only queued behind the same Semaphore(1) -- is denied.
    denied = await load_balancer.acquire_account_lease(
        "acc_hz02_cap", kind="response_create", concurrency_caps=caps
    )
    assert denied is None, "5th queued gate waiter was admitted -- no cap inversion"
