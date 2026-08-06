"""Counter-evidence for hazard_02: the *gate* half of the claim does not stand.

After a CancelledError at app/modules/proxy/_service/websocket/helpers.py:1586
the request_state still carries ``response_create_gate_acquired is True`` (it is
only cleared at helpers.py:1591, after the await).  Every later cleanup path
keys the semaphore release on exactly that flag, so the semaphore is recovered:

  * app/modules/proxy/_service/websocket/mixin.py:814
    ``_release_websocket_response_create_ownership_for_cleanup`` releases the
    gate in a ``finally`` (mixin.py:851-853) -- reached from
    ``_finalize_claimed_websocket_requests`` (mixin.py:5962) i.e. from
    ``_fail_pending_websocket_requests``, i.e. from
    ``_close_http_bridge_session`` (http_bridge/helpers.py:806-821).
  * app/modules/proxy/_service/http_bridge/request_submit.py:1522
    ``gate_acquired or request_state.response_create_gate_acquired``.

Both claimed cancellation sites leave the request_state INSIDE
``session.pending_requests`` (``_detach_http_bridge_request`` only marks
``draining_until_terminal``; the reader's response.created branch does not pop),
so those paths really do reach it.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._load_balancer.types import AccountConcurrencyCaps
from app.modules.proxy._service.websocket import helpers as ws_helpers
from app.modules.proxy._service.websocket import mixin as ws_mixin
from app.modules.proxy.load_balancer import LoadBalancer


async def _interrupted_release(load_balancer: LoadBalancer, gate: asyncio.Semaphore):
    """Reproduce the hazard: cancel the real helper at helpers.py:1586."""
    caps = AccountConcurrencyCaps(response_create_limit=4, stream_limit=64)
    lease = await load_balancer.acquire_account_lease(
        "acc_hz02_refute", kind="response_create", concurrency_caps=caps
    )
    assert lease is not None
    state = proxy_service._WebSocketRequestState(
        request_id="req_hz02_refute",
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
    state.account_response_create_release = load_balancer.release_account_lease

    await load_balancer._runtime_lock.acquire()
    try:
        releaser = asyncio.create_task(ws_helpers._release_websocket_response_create_gate(state, gate))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not releaser.done()
        releaser.cancel()
        with pytest.raises(asyncio.CancelledError):
            await releaser
    finally:
        load_balancer._runtime_lock.release()
    return state


@pytest.mark.asyncio
async def test_cleanup_path_recovers_the_leaked_semaphore():
    load_balancer = LoadBalancer(repo_factory=None)  # type: ignore[arg-type]
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    state = await _interrupted_release(load_balancer, gate)

    # Hazard reproduced: gate still held, ownership flag survived.
    assert gate.locked() is True
    assert state.response_create_gate_acquired is True

    # The REAL terminal-cleanup helper used by _fail_pending_websocket_requests
    # (websocket/mixin.py:5962) recovers it.
    await ws_mixin._release_websocket_response_create_ownership_for_cleanup(state, gate)
    assert gate.locked() is False, "cleanup did not recover the gate"
    assert state.response_create_gate_acquired is False


@pytest.mark.asyncio
async def test_cleanup_path_recovers_the_gate_even_if_the_lease_release_raises():
    """The finally at mixin.py:851-853 is what makes the recovery unconditional."""
    load_balancer = LoadBalancer(repo_factory=None)  # type: ignore[arg-type]
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    state = await _interrupted_release(load_balancer, gate)

    async def exploding_release(_lease):
        raise RuntimeError("db down")

    # Simulate a still-owned lease on the interrupted state.
    state.account_response_create_lease = object()
    state.account_response_create_release = exploding_release

    await ws_mixin._release_websocket_response_create_ownership_for_cleanup(state, gate)
    assert gate.locked() is False


@pytest.mark.asyncio
async def test_genuinely_stale_lease_is_reclaimed_by_the_stale_ttl():
    """The existing reclaim path still returns deliberately stale leases."""
    load_balancer = LoadBalancer(repo_factory=None)  # type: ignore[arg-type]
    caps = AccountConcurrencyCaps(response_create_limit=4, stream_limit=64)
    lease = await load_balancer.acquire_account_lease(
        "acc_hz02_refute", kind="response_create", concurrency_caps=caps
    )
    assert lease is not None
    runtime = load_balancer._runtime["acc_hz02_refute"]
    assert runtime.leases is not None and len(runtime.leases) == 1

    # Age it past proxy_account_lease_ttl_seconds (default 900.0,
    # app/core/config/settings.py:425) and let any acquire/select/snapshot
    # trigger _reclaim_stale_account_leases_locked (load_balancer.py:308).
    import dataclasses

    runtime.leases = {
        lease_id: dataclasses.replace(lease, acquired_at=lease.acquired_at - 10_000.0)
        for lease_id, lease in runtime.leases.items()
    }
    fresh = await load_balancer.acquire_account_lease(
        "acc_hz02_refute", kind="response_create", concurrency_caps=caps
    )
    assert fresh is not None
    inflight, _, _ = await load_balancer.account_pressure_snapshot("acc_hz02_refute")
    assert inflight == 1, "stale reclaim did not return the stale slot before fresh acquire"
