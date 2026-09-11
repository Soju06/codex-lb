"""A settled old request cannot invalidate a replacement probe."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import retry_circuit
from tests.simulation.virtual_time import VirtualClock
from tests.unit.test_proxy_http_bridge import _make_terminal_error_bridge_fixture, _stateful_retry_circuit_persistence

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("interpreted", [False, True])
@pytest.mark.parametrize("boundary", ["before_record", "during_load"])
@pytest.mark.parametrize("event_type", ["response.incomplete", "response.failed"])
async def test_late_failed_probe_preserves_replacement_and_terminal_cleanup(
    monkeypatch: pytest.MonkeyPatch, boundary: str, event_type: str, interpreted: bool
) -> None:
    clock = VirtualClock(monotonic_value=1000.0)
    service, session, request = _make_terminal_error_bridge_fixture(
        request_id="old-failure", key_value="late-failure", response_event_count=0
    )
    _, replacement, new_request = _make_terminal_error_bridge_fixture(
        request_id="replacement", key_value="late-failure", response_event_count=0
    )
    cast(Any, service)._clock = clock
    service._durable_bridge = SimpleNamespace(**_stateful_retry_circuit_persistence())
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=proxy_service.get_settings())),
    )
    finalize = AsyncMock(wraps=service._finalize_websocket_request_state)
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize)
    for owner in (request, new_request):
        owner.started_at = clock.monotonic()
        owner.bridge_request_deadline = clock.monotonic() + 600.0
        owner.awaiting_response_created = True
        owner.response_create_attempt_count = 1
        owner.response_create_sent_at = clock.monotonic()
    session.account.chatgpt_account_id = "diagnostic-workspace"
    circuit = retry_circuit._HTTPBridgeRetryCircuitState(
        consecutive_failures=2,
        cooldown_until=clock.monotonic() - 1,
        last_detail="stream_incomplete",
        last_touched_monotonic=clock.monotonic(),
    )
    cast(Any, service)._http_bridge_retry_circuits[session.key] = circuit
    assert await service._http_bridge_precreated_retry_allowed(session, probe_owner=request)
    request.claimed_half_open_generation = circuit.half_open_lease_generation
    old_generation = request.claimed_half_open_generation
    original_record = service._record_http_bridge_retry_circuit_failure
    original_load = service._load_http_bridge_retry_circuit
    recording = asyncio.Event()
    release = asyncio.Event()
    recorder_task: asyncio.Task[Any] | None = None

    async def record(*args: Any, **kwargs: Any) -> int | None:
        nonlocal recorder_task
        recorder_task = asyncio.current_task()
        if boundary == "before_record":
            recording.set()
            await release.wait()
        return await original_record(*args, **kwargs)

    async def load(*args: Any, **kwargs: Any) -> bool:
        if boundary == "during_load" and asyncio.current_task() is recorder_task:
            recording.set()
            await release.wait()
        return await original_load(*args, **kwargs)

    monkeypatch.setattr(service, "_record_http_bridge_retry_circuit_failure", record)
    monkeypatch.setattr(service, "_load_http_bridge_retry_circuit", load)
    processed = asyncio.Event()
    calls = 0
    response: dict[str, Any] = {"id": "resp-old-failure", "status": event_type.removeprefix("response.")}
    if event_type == "response.incomplete":
        response["incomplete_details"] = {"reason": "stream_incomplete"}
    # Both terminal forms use main's existing explicit-error classification.
    # Reason-only classification belongs to the independent #2273 change.
    response["error"] = {"code": "stream_incomplete", "type": "server_error", "message": "Stream failed"}

    async def receive() -> UpstreamWebSocketMessage:
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = {"type": event_type, "response": response}
            return UpstreamWebSocketMessage(
                kind="text",
                text=json.dumps(payload),
                responses_interpreted=interpreted,
                event_type=event_type if interpreted else None,
                payload=cast(Any, payload) if interpreted else None,
            )
        processed.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(session.upstream, "receive", receive, raising=False)
    queue = request.event_queue
    assert queue is not None
    reader = asyncio.create_task(service._relay_http_bridge_upstream_messages(session))
    reader.add_done_callback(lambda _: processed.set())
    session.upstream_reader = reader
    try:
        await asyncio.wait_for(recording.wait(), timeout=2)
        assert request.terminal_settlement_phase == "claimed"
        assert request not in session.pending_requests
        assert not await service._http_bridge_precreated_retry_allowed(replacement, probe_owner=new_request)
        clock.advance(circuit.half_open_until - clock.monotonic() + 1)
        new_request.started_at = clock.monotonic()
        new_request.bridge_request_deadline = clock.monotonic() + 600
        assert await service._http_bridge_precreated_retry_allowed(replacement, probe_owner=new_request)
        new_request.claimed_half_open_generation = circuit.half_open_lease_generation
        assert new_request.claimed_half_open_generation > old_generation
        assert new_request.claimed_half_open_episode is not None
        expected_episode = (*new_request.claimed_half_open_episode, new_request.claimed_half_open_generation)
        replacement_deadline = circuit.half_open_until
        replacement_cooldown = circuit.cooldown_until
        release.set()
        await asyncio.wait_for(processed.wait(), timeout=2)
        if reader.done():
            reader.result()
        assert circuit.half_open_owner_token is new_request
        assert circuit.half_open_owner_session is replacement
        assert circuit.half_open_lease_generation == new_request.claimed_half_open_generation
        assert circuit.half_open_until == replacement_deadline
        assert circuit.cooldown_until == replacement_cooldown
        assert (
            circuit.persisted_updated_at_epoch,
            circuit.consecutive_failures,
            circuit.persisted_admission_generation,
        ) == expected_episode[:3]
        service._durable_bridge.persist_retry_circuit.assert_not_awaited()
        block = queue.get_nowait()
        assert block is not None
        terminal = proxy_service.parse_sse_data_json(block)
        assert terminal is not None and terminal["type"] == event_type
        assert terminal["response"] == response
        assert queue.get_nowait() is None
        assert request.terminal_settlement_phase is None
        finalize.assert_awaited_once()
        cast(AsyncMock, service._write_request_log).assert_awaited_once()
        assert await service._clear_http_bridge_retry_circuit(replacement, expected_episode=expected_episode)
        assert replacement.key not in cast(Any, service)._http_bridge_retry_circuits
    finally:
        release.set()
        if not reader.done():
            reader.cancel()
        await asyncio.wait_for(asyncio.gather(reader, return_exceptions=True), timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse_owner", [False, True])
@pytest.mark.parametrize("detail", ["stream_incomplete", "continuity_owner_unavailable"])
async def test_stale_request_cleanup_keeps_its_captured_probe_claim(
    monkeypatch: pytest.MonkeyPatch, reuse_owner: bool, detail: str
) -> None:
    clock = VirtualClock(monotonic_value=1000.0)
    service, session, request = _make_terminal_error_bridge_fixture(
        request_id="retiring-owner", key_value="retiring-probe", response_event_count=0
    )
    _, replacement, new_request = _make_terminal_error_bridge_fixture(
        request_id="replacement", key_value="retiring-probe", response_event_count=0
    )
    cast(Any, service)._clock = clock
    service._durable_bridge = SimpleNamespace(**_stateful_retry_circuit_persistence())
    request.response_create_attempt_count = 1
    request.started_at = clock.monotonic()
    request.bridge_request_deadline = clock.monotonic() + 600
    session.account.chatgpt_account_id = "diagnostic-workspace"
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=proxy_service.get_settings())),
    )
    circuit = retry_circuit._HTTPBridgeRetryCircuitState(
        consecutive_failures=2,
        cooldown_until=clock.monotonic() - 1,
        last_detail="stream_incomplete",
        last_touched_monotonic=clock.monotonic(),
    )
    cast(Any, service)._http_bridge_retry_circuits[session.key] = circuit
    assert await service._http_bridge_precreated_retry_allowed(session, probe_owner=request)
    request.claimed_half_open_generation = circuit.half_open_lease_generation
    old_generation = request.claimed_half_open_generation
    original_record = service._record_http_bridge_retry_circuit_failure
    recording = asyncio.Event()
    release = asyncio.Event()

    async def record(*args: Any, **kwargs: Any) -> int | None:
        recording.set()
        await release.wait()
        return await original_record(*args, **kwargs)

    monkeypatch.setattr(service, "_record_http_bridge_retry_circuit_failure", record)
    queue = request.event_queue
    assert queue is not None
    cleanup = asyncio.create_task(service._fail_stale_http_bridge_pending_requests(session, [request], detail=detail))
    try:
        await asyncio.wait_for(recording.wait(), timeout=2)
        assert request not in session.pending_requests
        clock.advance(circuit.half_open_until - clock.monotonic() + 1)
        if reuse_owner:
            replacement, new_request = session, request
        new_request.started_at = clock.monotonic()
        new_request.bridge_request_deadline = clock.monotonic() + 600
        assert await service._http_bridge_precreated_retry_allowed(replacement, probe_owner=new_request)
        new_request.claimed_half_open_generation = circuit.half_open_lease_generation
        assert new_request.claimed_half_open_generation > old_generation
        assert new_request.claimed_half_open_episode is not None
        expected_episode = (*new_request.claimed_half_open_episode, new_request.claimed_half_open_generation)
        replacement_deadline = circuit.half_open_until
        release.set()
        await asyncio.wait_for(cleanup, timeout=2)
        assert circuit.half_open_owner_session is replacement
        assert circuit.half_open_owner_token is new_request
        assert circuit.half_open_lease_generation == new_request.claimed_half_open_generation
        assert circuit.half_open_until == replacement_deadline
        assert circuit.consecutive_failures == 2
        service._durable_bridge.persist_retry_circuit.assert_not_awaited()
        assert queue.get_nowait() is not None
        assert queue.get_nowait() is None
        cast(AsyncMock, service._write_request_log).assert_awaited_once()
        assert await service._clear_http_bridge_retry_circuit(replacement, expected_episode=expected_episode)
    finally:
        release.set()
        if not cleanup.done():
            cleanup.cancel()
        await asyncio.wait_for(asyncio.gather(cleanup, return_exceptions=True), timeout=2)
