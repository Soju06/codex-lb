from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import upstream_events
from tests.unit.test_proxy_http_bridge import (
    _CAPACITY_MESSAGE,
    _OVERLOADED_MESSAGE,
    _accepted_bridge_request_state,
    _activate_half_open_probe,
    _capacity_error_text,
    _make_app_settings,
    _make_bridge_session,
)

pytestmark = pytest.mark.unit


def _configure(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy=None))
        ),
    )
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())
    monkeypatch.setattr(service._durable_bridge, "lookup_retry_circuit", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_persist_http_bridge_retry_circuit", AsyncMock())
    monkeypatch.setattr(upstream_events, "_ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS", 0.001)
    monkeypatch.setattr(upstream_events, "_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS", 0.001)


async def _drive(service: Any, session: Any, path: str) -> None:
    if path == "transport":
        assert await service._retry_http_bridge_precreated_request(session) is False
    else:
        await service._process_http_bridge_upstream_text(
            session,
            _capacity_error_text(
                code="server_is_overloaded" if path == "bare" else "model_at_capacity",
                message=_OVERLOADED_MESSAGE if path == "bare" else _CAPACITY_MESSAGE,
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "failure"),
    [("bare", "connect"), ("message", "connect"), ("transport", "cancel"), ("bare", "owner")],
)
async def test_accepted_reconnect_retains_uninstalled_lease_for_account_cleanup(
    monkeypatch: pytest.MonkeyPatch, path: str, failure: str
) -> None:
    """A still owns the socket when reconnect acquires B and then aborts."""
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    _configure(monkeypatch, service)
    request = _accepted_bridge_request_state(skip_request_log=True, response_create_attempt_count=1)
    session = _make_bridge_session(pending_requests=deque([request]), queued_request_count=1)
    if path == "transport":
        session.last_upstream_close_code = 1000
        session.last_upstream_close_generation = 1
    if failure == "owner":
        request.file_required_preferred_account = True
        request.preferred_account_id = session.account.id
    service._http_bridge_sessions[session.key] = session
    old_close = session.upstream.close
    old_lease = await service._load_balancer.acquire_account_lease(session.account.id, kind="stream")
    assert old_lease is not None
    session.account_lease = old_lease
    replacement = SimpleNamespace(id="acc-uninstalled-B", status=session.account.status, plan_type="plus")
    replacement_lease = None
    circuit = _activate_half_open_probe(service, session)
    circuit.half_open_until = 0.0
    circuit.half_open_owner_session = None
    circuit.cooldown_until = time.monotonic() - 1.0
    claims: list[tuple[float, int]] = []
    returned: list[tuple[float, int]] = []
    original_probe_release = service._release_http_bridge_retry_circuit_half_open

    async def release_probe(target: Any, **kwargs: Any) -> bool:
        owned = (circuit.half_open_until, circuit.half_open_lease_generation)
        released = await original_probe_release(target, **kwargs)
        if released:
            assert target is session
            assert kwargs["probe_owner"] is request
            returned.append(owned)
        return released

    monkeypatch.setattr(service, "_release_http_bridge_retry_circuit_half_open", release_probe)

    async def select(_deadline: float, **kwargs: Any) -> Any:
        nonlocal replacement_lease
        assert replacement_lease is None, "failed reconnect must not select again"
        assert circuit.half_open_owner_token is request
        claims.append((circuit.half_open_until, circuit.half_open_lease_generation))
        if failure == "owner":
            assert kwargs["preferred_account_id"] == session.account.id
            assert kwargs["fallback_on_preferred_account_unavailable"] is False
        replacement_lease = await service._load_balancer.acquire_account_lease(replacement.id, kind="stream")
        assert replacement_lease is not None
        return proxy_service.AccountSelection(
            account=cast(Any, replacement), error_message=None, lease=replacement_lease
        )

    async def ensure(account: Any, **_: Any) -> Any:
        if failure == "cancel":
            raise asyncio.CancelledError()
        return account

    open_socket = AsyncMock(side_effect=RuntimeError("replacement socket unavailable"))
    monkeypatch.setattr(service, "_select_account_with_budget_for_stream", select)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", ensure)
    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", open_socket)
    original_release = service._load_balancer.release_account_lease
    retry_started = asyncio.Event()
    allow_retry = asyncio.Event()
    recovered = False
    attempts = 0

    async def release(lease: Any) -> None:
        nonlocal attempts
        if replacement_lease is not None and lease is replacement_lease:
            attempts += 1
            if not recovered:
                raise RuntimeError("B lease store unavailable")
            retry_started.set()
            await allow_retry.wait()
        await original_release(lease)

    monkeypatch.setattr(service._load_balancer, "release_account_lease", release)
    await _drive(service, session, path)
    assert replacement_lease is not None
    assert len(claims) == 1 and claims[0][0] > 0 and claims[0][1] > 0
    assert returned == claims
    assert circuit.half_open_until == 0.0
    assert circuit.half_open_owner_token is None
    assert circuit.half_open_owner_session is None
    assert session.account.id != replacement.id
    assert session.handoff_in_progress is False
    assert session.handoff_future is None
    assert session.key not in service._http_bridge_inflight_sessions
    if failure != "connect":
        open_socket.assert_not_awaited()

    # Owner-loss already closes; other failures leave retirement to the caller.
    if session.resource_close_task is None:
        assert await service.close_http_bridge_sessions_for_account(session.account.id) == 1
    assert session.resource_close_task is not None and session.resource_close_task.done()
    assert service._http_bridge_detached_sessions[id(session)] is session
    assert session.pending_account_lease_releases == [replacement_lease]
    assert session.account_lease is None
    assert not session.pending_requests
    assert not session.response_create_gate.locked()
    assert request.response_create_gate_acquired is False
    assert request.response_create_admission is None
    assert service._load_balancer._runtime[session.account.id].inflight_streams == 0
    assert service._load_balancer._runtime[replacement.id].inflight_streams == 1
    old_close.assert_awaited_once()

    assert request.event_queue is not None
    delivered = []
    while not request.event_queue.empty():
        frame = request.event_queue.get_nowait()
        # The stream consumer stops at the first end-of-stream marker.
        if frame is None:
            break
        payload = proxy_service.parse_sse_data_json(frame)
        if isinstance(payload, dict) and payload.get("type") == "response.failed":
            delivered.append(payload)
    assert len(delivered) == 1
    assert delivered[0]["response"]["id"] == "resp-accepted-visible"

    before_retry = attempts
    recovered = True
    first = asyncio.create_task(service.close_http_bridge_sessions_for_account(replacement.id))
    second = None
    try:
        await asyncio.wait_for(retry_started.wait(), timeout=1.0)
        second = asyncio.create_task(service.close_http_bridge_sessions_for_account(replacement.id))
        await asyncio.sleep(0)
        assert attempts == before_retry + 1
        assert session.resource_close_task is not None and not session.resource_close_task.done()
    finally:
        allow_retry.set()
        await asyncio.gather(first, *([second] if second is not None else []))
    assert attempts == before_retry + 1
    assert session.pending_account_lease_releases == []
    assert id(session) not in service._http_bridge_detached_sessions
    assert service._load_balancer._runtime[replacement.id].inflight_streams == 0
    assert service._load_balancer._runtime[replacement.id].leases == {}
    old_close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["account", "file", "operation"])
@pytest.mark.parametrize("path", ["bare", "message", "transport"])
async def test_accepted_bound_replay_never_selects_a_fallback_account(
    monkeypatch: pytest.MonkeyPatch, bound: str, path: str
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    _configure(monkeypatch, service)
    request = _accepted_bridge_request_state(skip_request_log=True, response_create_attempt_count=1)
    session = _make_bridge_session(pending_requests=deque([request]), queued_request_count=1)
    service._http_bridge_sessions[session.key] = session
    if bound == "account":
        request.request_text = json.dumps(
            {"type": "response.create", "model": request.model, "input": [], "conversation": "conv-owner"}
        )
    elif bound == "file":
        request.file_required_preferred_account = True
        request.preferred_account_id = session.account.id
    else:
        request.operation_id = "operation-owner"
    if path == "transport":
        session.last_upstream_close_code = 1000
        session.last_upstream_close_generation = 1

    async def select(_deadline: float, **kwargs: Any) -> Any:
        assert kwargs["preferred_account_id"] == session.account.id
        assert kwargs["fallback_on_preferred_account_unavailable"] is False
        assert session.account.id not in kwargs["exclude_account_ids"]
        return proxy_service.AccountSelection(
            account=None, error_code="usage_limit_reached", error_message="Owner unavailable"
        )

    selection = AsyncMock(side_effect=select)
    connect = AsyncMock()
    monkeypatch.setattr(service, "_select_account_with_budget_for_stream", selection)
    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", connect)
    await _drive(service, session, path)
    selection.assert_awaited_once()
    connect.assert_not_awaited()
    assert session.closed is True
    assert request.error_code_override == "previous_response_owner_unavailable"
    assert not session.response_create_gate.locked()
    assert session.key not in service._http_bridge_sessions
    assert id(session) not in service._http_bridge_detached_sessions
