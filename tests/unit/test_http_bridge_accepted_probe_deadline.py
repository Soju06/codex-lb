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
from app.modules.proxy._service.http_bridge import retry_circuit
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler
from tests.unit.test_proxy_http_bridge import (
    _OVERLOADED_MESSAGE,
    _accepted_bridge_request_state,
    _capacity_error_text,
    _make_bridge_session,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_accepted_replay_probe_covers_original_budget_and_cannot_send_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=proxy_service.get_settings())),
    )
    clock = VirtualClock(monotonic_value=1000.0)
    scheduler = VirtualScheduler(clock)
    service = proxy_service.ProxyService(cast(Any, nullcontext()), clock=clock, scheduler=scheduler)
    request = _accepted_bridge_request_state(
        started_at=clock.monotonic(),
        bridge_request_deadline=clock.monotonic() + 7200.0,
        response_create_attempt_count=1,
        skip_request_log=True,
    )
    session = _make_bridge_session(
        key_value="accepted-probe-budget",
        pending_requests=deque([request]),
        queued_request_count=1,
    )
    sibling = _make_bridge_session(key_value="accepted-probe-budget")
    circuit = retry_circuit._HTTPBridgeRetryCircuitState(
        consecutive_failures=2,
        cooldown_until=clock.monotonic() - 1.0,
        last_detail="stream_incomplete",
        last_touched_monotonic=clock.monotonic(),
    )
    cast(Any, service)._http_bridge_retry_circuits[session.key] = circuit
    service._durable_bridge = SimpleNamespace(lookup_retry_circuit=AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())
    lease = proxy_service.AccountLease(
        lease_id="accepted-probe-account-create",
        account_id=session.account.id,
        kind="response_create",
        acquired_at=clock.monotonic(),
    )
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", AsyncMock(return_value=lease))
    release_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_lease)
    send = AsyncMock()
    monkeypatch.setattr(session.upstream, "send_text", send, raising=False)
    reconnect_started = asyncio.Event()
    resume_reconnect = asyncio.Event()

    async def reconnect(*args: Any, **kwargs: Any) -> None:
        reconnect_started.set()
        await resume_reconnect.wait()

    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)
    terminal_task = scheduler.create_task(
        service._process_http_bridge_upstream_text(
            session,
            _capacity_error_text(code="server_is_overloaded", message=_OVERLOADED_MESSAGE),
        ),
        name="accepted-probe-budget-terminal",
    )
    try:
        await asyncio.wait_for(reconnect_started.wait(), timeout=1.0)
        assert request.terminal_settlement_phase == "claimed"
        assert circuit.half_open_owner_token is request
        assert circuit.half_open_until == request.bridge_request_deadline
        generation = circuit.half_open_lease_generation
        await scheduler.advance(retry_circuit._HTTP_BRIDGE_RETRY_CIRCUIT_HALF_OPEN_LEASE_SECONDS + 1.0)
        assert await service._http_bridge_precreated_retry_allowed(sibling) is False
        assert circuit.half_open_owner_token is request
        assert circuit.half_open_lease_generation == generation
        assert not terminal_task.done()

        # The settlement claim is retained during the replay. The original
        # request deadline still prevents a late reconnect from sending again.
        assert request.bridge_request_deadline is not None
        await scheduler.advance(request.bridge_request_deadline - clock.monotonic() - 1.0)
        assert await service._http_bridge_precreated_retry_allowed(sibling) is False
        await scheduler.advance(2.0)
        replacement_owner = object()
        assert await service._http_bridge_precreated_retry_allowed(sibling, probe_owner=replacement_owner) is True
        replacement_generation = circuit.half_open_lease_generation
        assert replacement_generation > generation
        resume_reconnect.set()
        await _await_task_deferring_cancellation(terminal_task)
        send.assert_not_awaited()
        assert [call.args[0] for call in release_lease.await_args_list if call.args[0] is not None] == [lease]
        assert request.response_create_attempt_count == 1
        assert circuit.half_open_owner_token is replacement_owner
        assert circuit.half_open_owner_session is sibling
        assert circuit.half_open_lease_generation == replacement_generation
        assert circuit.half_open_until > clock.monotonic()
        assert session.response_create_gate.locked() is False
        assert request.response_create_admission is None
        assert request.terminal_settlement_phase is None
        assert request.event_queue is not None
        terminal_block = request.event_queue.get_nowait()
        assert terminal_block is not None
        terminal = proxy_service.parse_sse_data_json(terminal_block)
        assert terminal is not None
        assert terminal["type"] == "response.failed"
        assert cast(dict[str, Any], terminal["response"])["id"] == "resp-accepted-visible"
        assert request.event_queue.get_nowait() is None
        assert request.event_queue.empty()
    finally:
        resume_reconnect.set()
        if not terminal_task.done():
            terminal_task.cancel()
        await asyncio.gather(terminal_task, return_exceptions=True)
