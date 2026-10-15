from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterator
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service import support
from app.modules.proxy._service.http_bridge import retry_circuit
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler
from tests.unit.test_proxy_http_bridge import _accepted_bridge_request_state, _make_bridge_session

pytestmark = pytest.mark.unit


class _FailAfterFirstDisarm(deque[proxy_service._WebSocketRequestState]):
    error: RuntimeError | None = None

    def __iter__(self) -> Iterator[proxy_service._WebSocketRequestState]:
        for request in super().__iter__():
            yield request
            if self.error is not None:
                error, self.error = self.error, None
                raise error


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_step", [None, "detach", "disarm"])
@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_local_reset_returns_probe_only_after_complete_transition(
    monkeypatch: pytest.MonkeyPatch, failed_step: str | None, cancel_caller: bool
) -> None:
    clock = VirtualClock(monotonic_value=1000.0)
    scheduler = VirtualScheduler(clock)
    service = proxy_service.ProxyService(cast(Any, nullcontext()), clock=clock, scheduler=scheduler)
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=proxy_service.get_settings())),
    )
    requests = [
        _accepted_bridge_request_state(
            request_id=f"reset-request-{index}",
            response_id=f"resp-reset-{index}",
            started_at=clock.monotonic(),
            bridge_request_deadline=clock.monotonic() + 600.0,
            skip_request_log=True,
            response_create_attempt=support._HTTPBridgeResponseCreateAttempt(ordinal=1),
        )
        for index in range(2)
    ]
    session = _make_bridge_session(
        key_value="local-reset-probe-order", pending_requests=deque(requests), queued_request_count=2
    )
    sibling = _make_bridge_session(key_value="local-reset-probe-order")
    circuit = retry_circuit._HTTPBridgeRetryCircuitState(
        consecutive_failures=2,
        cooldown_until=clock.monotonic() - 1.0,
        last_detail="stream_incomplete",
        last_touched_monotonic=clock.monotonic(),
    )
    cast(Any, service)._http_bridge_retry_circuits[session.key] = circuit
    service._durable_bridge = SimpleNamespace(lookup_retry_circuit=AsyncMock(return_value=None))
    service._http_bridge_sessions[session.key] = session
    assert await service._http_bridge_precreated_retry_allowed(session, probe_owner=requests[0]) is True
    deadline = circuit.half_open_until
    generation = circuit.half_open_lease_generation
    requests[0].claimed_half_open_generation = generation
    lease = proxy_service.AccountLease(
        lease_id="local-reset-stream", account_id=session.account.id, kind="stream", acquired_at=clock.monotonic()
    )
    session.account_lease = lease
    release_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_lease)
    transition_error = RuntimeError(f"{failed_step} failed")
    if failed_step == "detach":
        monkeypatch.setattr(service, "_detach_http_bridge_session_locked", Mock(side_effect=transition_error))
    elif failed_step == "disarm":
        pending = _FailAfterFirstDisarm(requests)
        pending.error = transition_error
        session.pending_requests = pending

    release_probe = AsyncMock(wraps=service._release_http_bridge_retry_circuit_half_open)
    monkeypatch.setattr(service, "_release_http_bridge_retry_circuit_half_open", release_probe)
    original_settle = service._fail_pending_websocket_requests
    settle_started = asyncio.Event()
    resume_settle = asyncio.Event()

    async def settle(**kwargs: Any) -> bool:
        settle_started.set()
        await resume_settle.wait()
        return await original_settle(**kwargs)

    settle_pending = AsyncMock(side_effect=settle)
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", settle_pending)
    close_session = AsyncMock(wraps=service._close_http_bridge_session)
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)
    reset_task = scheduler.create_task(
        service._reset_http_bridge_session_after_local_terminal_error(
            session,
            error_code="stream_incomplete",
            error_message="local continuity lost",
            probe_owner=requests[0],
            proxy_continuity_loss_detail="continuity_owner_unavailable",
        ),
        name="test-local-reset-probe-order",
    )
    try:
        await asyncio.wait_for(settle_started.wait(), timeout=1.0)
        if cancel_caller:
            reset_task.cancel()
            await asyncio.sleep(0)
        assert not reset_task.done()
        assert session.closed is True
        assert (service._http_bridge_sessions.get(session.key) is session) is (failed_step == "detach")
        assert requests[0].response_create_attempt is not None
        assert requests[0].response_create_attempt.disarmed is True
        assert requests[1].response_create_attempt is not None
        assert requests[1].response_create_attempt.disarmed is (failed_step != "disarm")
        if failed_step is not None:
            assert await service._http_bridge_precreated_retry_allowed(sibling) is False
            release_probe.assert_not_awaited()
            assert circuit.half_open_owner_token is requests[0]
            assert circuit.half_open_until == deadline
            assert circuit.half_open_lease_generation == generation
        else:
            release_probe.assert_awaited_once()
            assert circuit.half_open_until == 0.0
            assert service._http_bridge_detached_sessions[id(session)] is session
            assert await service._http_bridge_precreated_retry_allowed(sibling) is True
            assert circuit.half_open_owner_session is sibling
            assert circuit.half_open_lease_generation > generation
        resume_settle.set()
        if failed_step is not None:
            with pytest.raises(RuntimeError) as exc_info:
                await asyncio.wait_for(reset_task, timeout=1.0)
            assert exc_info.value is transition_error
        elif cancel_caller:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(reset_task, timeout=1.0)
        else:
            await asyncio.wait_for(reset_task, timeout=1.0)
        close_session.assert_awaited_once_with(session, release_durable_session=True)
        cast(AsyncMock, session.upstream.close).assert_awaited_once()
        assert session.account_lease is None
        assert [call.args[0] for call in release_lease.await_args_list if call.args[0] is not None] == [lease]
        assert not session.pending_requests
        assert id(session) not in service._http_bridge_detached_sessions
        assert circuit.consecutive_failures == 2
        assert release_probe.await_count == (0 if failed_step is not None else 1)
        if failed_step is not None:
            assert await service._http_bridge_precreated_retry_allowed(sibling) is False
            assert circuit.half_open_owner_token is requests[0]
        for request in requests:
            assert request.event_queue is not None
            terminal = proxy_service.parse_sse_data_json(request.event_queue.get_nowait())
            assert terminal is not None
            assert terminal["type"] == "response.failed"
            assert cast(dict[str, Any], terminal["response"])["id"] == request.response_id
            assert request.event_queue.get_nowait() is None
            assert request.event_queue.empty()
        assert session.resource_close_task is not None and session.resource_close_task.done()
    finally:
        resume_settle.set()
        if not reset_task.done():
            reset_task.cancel()
        await asyncio.wait_for(asyncio.gather(reset_task, return_exceptions=True), timeout=1.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_step", ["detach", "disarm"])
@pytest.mark.parametrize("preserve_primary", [False, True])
async def test_local_reset_keeps_first_error_when_later_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch, failed_step: str, preserve_primary: bool
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request = _accepted_bridge_request_state(
        response_create_attempt=support._HTTPBridgeResponseCreateAttempt(ordinal=1),
    )
    session = _make_bridge_session(pending_requests=deque([request]))
    service._http_bridge_sessions[session.key] = session
    transition_error = RuntimeError(f"{failed_step} failed")
    if failed_step == "detach":
        monkeypatch.setattr(service, "_detach_http_bridge_session_locked", Mock(side_effect=transition_error))
    else:
        pending = _FailAfterFirstDisarm([request])
        pending.error = transition_error
        session.pending_requests = pending
    release_probe = AsyncMock()
    settle_pending = AsyncMock(side_effect=RuntimeError("settlement failed"))
    close_session = AsyncMock(side_effect=RuntimeError("close failed"))
    monkeypatch.setattr(service, "_release_http_bridge_retry_circuit_half_open", release_probe)
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", settle_pending)
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)
    primary_error = RuntimeError("primary error") if preserve_primary else None

    with pytest.raises(RuntimeError) as exc_info:
        await service._reset_http_bridge_session_after_local_terminal_error(
            session,
            error_code="stream_incomplete",
            error_message="local continuity lost",
            proxy_continuity_loss_detail="continuity_owner_unavailable",
            preserve_error=primary_error,
        )

    assert exc_info.value is (primary_error if preserve_primary else transition_error)
    if preserve_primary:
        assert exc_info.value.__cause__ is transition_error
    release_probe.assert_not_awaited()
    settle_pending.assert_awaited_once()
    close_session.assert_awaited_once_with(session, release_durable_session=True)
