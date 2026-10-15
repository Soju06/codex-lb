"""Accepted replay must return only its undispatched retry probe."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.utils.shared_future import _await_task_deferring_cancellation
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge.retry_circuit import _HTTPBridgeRetryCircuitState
from tests.simulation.virtual_time import VirtualClock
from tests.unit.test_proxy_http_bridge import (
    _accepted_bridge_request_state,
    _make_app_settings,
    _make_bridge_session,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def accepted_probe(monkeypatch: pytest.MonkeyPatch) -> Any:
    clock = VirtualClock(monotonic_value=100.0)
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(service, "_clock", clock)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    owner = _accepted_bridge_request_state(
        request_id="accepted-probe-owner",
        started_at=90.0,
        bridge_request_deadline=700.0,
        response_create_sent_at=90.0,
        response_create_attempt_count=1,
    )
    session = _make_bridge_session(
        key_value="accepted-probe-handback",
        pending_requests=deque([owner]),
        queued_request_count=1,
    )
    session.last_upstream_close_code = 1011
    state = _HTTPBridgeRetryCircuitState(
        consecutive_failures=2,
        cooldown_until=99.0,
        last_detail="stream_incomplete",
        last_touched_monotonic=100.0,
    )
    cast(Any, service)._http_bridge_retry_circuits[session.key] = state
    service._durable_bridge = SimpleNamespace(lookup_retry_circuit=AsyncMock(return_value=None))
    claims: list[tuple[float, object, int]] = []
    original_admission = service._http_bridge_precreated_retry_allowed

    async def observe_admission(*args: Any, **kwargs: Any) -> bool:
        admitted = await original_admission(*args, **kwargs)
        claims.extend(kwargs["claimed_lease_out"])
        return admitted

    monkeypatch.setattr(service, "_http_bridge_precreated_retry_allowed", observe_admission)
    release = AsyncMock(wraps=service._release_http_bridge_retry_circuit_half_open)
    monkeypatch.setattr(service, "_release_http_bridge_retry_circuit_half_open", release)
    reconnect = AsyncMock(side_effect=RuntimeError("reconnect failed before retry send"))
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)
    cast(Any, session.upstream).send_text = AsyncMock()
    return SimpleNamespace(
        service=service,
        session=session,
        owner=owner,
        state=state,
        claims=claims,
        release=release,
        reconnect=reconnect,
    )


def _assert_exact_probe_returned(case: Any) -> None:
    assert len(case.claims) == 1
    deadline, owner, generation = case.claims[0]
    assert owner is case.owner
    assert deadline > 100.0
    assert generation > 0
    case.release.assert_awaited_once_with(
        case.session,
        detail="probe_not_dispatched",
        probe_owner=case.owner,
        expected_half_open_until=deadline,
        expected_half_open_generation=generation,
    )
    assert case.state.half_open_until == 0.0
    assert case.state.half_open_owner_token is None
    assert case.state.half_open_owner_session is None
    assert case.owner.claimed_half_open_generation == 0
    assert case.owner.response_create_attempt_count == 1
    case.session.upstream.send_text.assert_not_awaited()


async def test_accepted_transport_close_busy_gate_returns_new_probe(accepted_probe: Any) -> None:
    case = accepted_probe
    await case.session.response_create_gate.acquire()
    try:
        assert await case.service._retry_http_bridge_precreated_request(case.session) is False
        _assert_exact_probe_returned(case)
        case.reconnect.assert_not_awaited()
        assert case.session.response_create_gate.locked()
        assert not case.owner.response_create_gate_acquired
        assert case.owner.response_id == "resp-accepted-visible"
        assert case.owner.replay_count == 0
    finally:
        case.session.response_create_gate.release()


async def test_accepted_candidate_replaced_during_admission_does_not_claim_gate(
    accepted_probe: Any,
) -> None:
    case = accepted_probe
    replacement = _accepted_bridge_request_state(request_id="replacement-accepted-owner")
    lookup_started = asyncio.Event()
    finish_lookup = asyncio.Event()

    async def lookup(**kwargs: Any) -> None:
        lookup_started.set()
        await finish_lookup.wait()

    case.service._durable_bridge.lookup_retry_circuit = AsyncMock(side_effect=lookup)
    retry_task = asyncio.create_task(case.service._retry_http_bridge_precreated_request(case.session))
    try:
        await asyncio.wait_for(lookup_started.wait(), timeout=1.0)
        async with case.session.pending_lock:
            case.session.pending_requests.clear()
            case.session.pending_requests.append(replacement)
        finish_lookup.set()
        result, cancellation = await _await_task_deferring_cancellation(retry_task)
        assert cancellation is None
        assert result is False
        _assert_exact_probe_returned(case)
        case.reconnect.assert_not_awaited()
        assert list(case.session.pending_requests) == [replacement]
        assert not case.session.response_create_gate.locked()
        assert not replacement.response_create_gate_acquired
        assert not case.owner.response_create_gate_acquired
        assert replacement.response_id == "resp-accepted-visible"
        assert replacement.replay_count == 0
    finally:
        finish_lookup.set()
        if not retry_task.done():
            retry_task.cancel()
            await asyncio.gather(retry_task, return_exceptions=True)


@pytest.mark.parametrize("cancel_reconnect", [False, True], ids=["reconnect-failure", "reconnect-cancel"])
async def test_accepted_replay_returns_probe_before_second_send(
    accepted_probe: Any,
    cancel_reconnect: bool,
) -> None:
    case = accepted_probe
    reconnect_started = asyncio.Event()

    async def reconnect(*args: Any, **kwargs: Any) -> None:
        reconnect_started.set()
        await asyncio.Event().wait()

    if cancel_reconnect:
        case.reconnect.side_effect = reconnect
    retry_task = asyncio.create_task(case.service._retry_http_bridge_precreated_request(case.session))
    try:
        if cancel_reconnect:
            await asyncio.wait_for(reconnect_started.wait(), timeout=1.0)
            retry_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await _await_task_deferring_cancellation(retry_task)
        else:
            result, cancellation = await _await_task_deferring_cancellation(retry_task)
            assert cancellation is None
            assert result is False
        _assert_exact_probe_returned(case)
        case.reconnect.assert_awaited_once()
        assert case.owner.replay_downstream_response_id == "resp-accepted-visible"
        assert case.owner.suppress_next_created_downstream
        assert case.owner.replay_count == 1
    finally:
        if not retry_task.done():
            retry_task.cancel()
            await asyncio.gather(retry_task, return_exceptions=True)
        # The caller's terminal settlement owns the re-claimed create gate.
        if case.owner.response_create_gate_acquired:
            case.owner.response_create_gate_acquired = False
            case.session.response_create_gate.release()
