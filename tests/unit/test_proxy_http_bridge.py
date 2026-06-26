from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections import deque
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import aiohttp
import anyio
import pytest
from fastapi import WebSocket

from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket, UpstreamWebSocketMessage
from app.core.config.settings import Settings
from app.core.errors import openai_error
from app.db.models import AccountStatus, HttpBridgeSessionState
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import mixin as http_bridge_mixin_module
from app.modules.proxy._service.http_bridge import streaming as http_bridge_streaming_module
from app.modules.proxy.http_bridge_forwarding import OwnerForwardRelayFailure

pytestmark = pytest.mark.unit


def _make_app_settings(*, bridge_enabled: bool = True, **overrides: Any) -> Settings:
    return Settings(http_responses_session_bridge_enabled=bridge_enabled, **overrides)


def _make_bridge_session(
    *,
    key: proxy_service._HTTPBridgeSessionKey | None = None,
    key_value: str = "bridge-test",
    pending_requests: deque[proxy_service._WebSocketRequestState] | None = None,
    queued_request_count: int = 0,
) -> proxy_service._HTTPBridgeSession:
    session_key = key or proxy_service._HTTPBridgeSessionKey("session_header", key_value, None)
    return proxy_service._HTTPBridgeSession(
        key=session_key,
        headers={"x-codex-session-id": key_value},
        affinity=proxy_service._AffinityPolicy(
            key=key_value,
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.2",
        account=cast(Any, SimpleNamespace(id="acc-bridge", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=pending_requests or deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=queued_request_count,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


async def _wait_for_close_await(close_session: AsyncMock, session: proxy_service._HTTPBridgeSession) -> None:
    for _ in range(10):
        if any(call.args == (session,) for call in close_session.await_args_list):
            return
        await asyncio.sleep(0)
    raise AssertionError("expected HTTP bridge session close to be awaited")


def test_http_bridge_account_capacity_wait_treats_workspace_spend_cap_as_recoverable() -> None:
    exc = ProxyResponseError(
        429,
        openai_error(
            "no_accounts",
            (
                "You hit your spend cap set by the owner of your workspace. "
                "Ask an owner to increase your spend cap to continue."
            ),
        ),
    )

    assert http_bridge_streaming_module._http_bridge_account_capacity_wait_seconds(exc) == 30.0


def _make_api_key(
    *,
    key_id: str,
    assigned_account_ids: list[str],
    account_assignment_scope_enabled: bool | None = None,
) -> proxy_service.ApiKeyData:
    return proxy_service.ApiKeyData(
        id=key_id,
        name="bridge-key",
        key_prefix="sk-bridge",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        last_used_at=None,
        account_assignment_scope_enabled=(
            bool(assigned_account_ids) if account_assignment_scope_enabled is None else account_assignment_scope_enabled
        ),
        assigned_account_ids=assigned_account_ids,
    )


def test_http_bridge_request_budget_falls_back_to_proxy_budget() -> None:
    settings = SimpleNamespace(proxy_request_budget_seconds=42.5)

    assert http_bridge_streaming_module._http_bridge_request_budget_seconds(settings) == 42.5


def test_websocket_top_level_error_payload_uses_error_type_not_event_type() -> None:
    payload: dict[str, proxy_service.JsonValue] = {
        "type": "error",
        "status": 400,
        "error_type": "invalid_request_error",
        "code": "previous_response_not_found",
        "message": "Previous response with id 'resp_missing' not found.",
        "param": "previous_response_id",
    }

    error = proxy_service._websocket_event_error_payload("error", payload)

    assert error == {
        "type": "invalid_request_error",
        "code": "previous_response_not_found",
        "message": "Previous response with id 'resp_missing' not found.",
        "param": "previous_response_id",
    }
    assert proxy_service._websocket_event_error_type("error", payload) == "invalid_request_error"
    assert proxy_service._websocket_event_error_code("error", payload) == "previous_response_not_found"


def test_http_error_status_from_payload_accepts_official_status_code_alias() -> None:
    payload: dict[str, proxy_service.JsonValue] = {
        "type": "error",
        "status_code": 400,
        "error": {"message": "bad request"},
    }

    assert proxy_service._http_error_status_from_payload(payload) == 400


@pytest.mark.asyncio
async def test_http_bridge_precreated_completed_terminal_falls_back_to_unresolved_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    finalize = AsyncMock()
    register_previous = AsyncMock()
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize)
    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", register_previous)

    request_state = proxy_service._WebSocketRequestState(
        request_id="req-precreated-completed",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    session = _make_bridge_session(
        key_value="bridge-precreated-completed",
        pending_requests=deque([request_state]),
        queued_request_count=1,
    )

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps({"type": "response.output_text.delta", "delta": "legacy text"}),
    )
    await service._process_http_bridge_upstream_text(
        session,
        json.dumps({"type": "response.output_text.done", "text": "legacy text"}),
    )
    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_precreated_completed",
                    "object": "response",
                    "status": "completed",
                    "output": [],
                },
            }
        ),
    )

    assert request_state.event_queue is not None
    blocks: list[str | None] = []
    while True:
        block = await asyncio.wait_for(request_state.event_queue.get(), timeout=1.0)
        blocks.append(block)
        if block is None:
            break

    payloads: list[dict[str, Any]] = []
    for block in blocks:
        if block is None:
            continue
        payload = proxy_service.parse_sse_data_json(block)
        assert isinstance(payload, dict)
        payloads.append(payload)
    assert [payload["type"] for payload in payloads] == [
        "response.output_text.delta",
        "response.output_text.done",
        "response.completed",
    ]
    assert request_state.response_id == "resp_precreated_completed"
    assert session.last_completed_response_id == "resp_precreated_completed"
    assert session.queued_request_count == 0
    assert not session.pending_requests
    register_previous.assert_awaited_once()
    finalize.assert_awaited_once()


def test_pop_terminal_websocket_request_state_precreated_completed_does_not_guess_with_ambiguous_pending() -> None:
    draining = proxy_service._WebSocketRequestState(
        request_id="req-draining",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        draining_until_terminal=True,
    )
    visible = proxy_service._WebSocketRequestState(
        request_id="req-visible",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
    )
    pending = deque([draining, visible])

    popped = proxy_service._pop_terminal_websocket_request_state(
        pending,
        response_id="resp_ambiguous_precreated_completed",
        fallback_request_state=None,
        allow_precreated_terminal_fallback=True,
    )

    assert popped is None
    assert list(pending) == [draining, visible]
    assert draining.response_id is None
    assert visible.response_id is None


def test_trim_http_bridge_previous_response_input_items_preserves_context_assistant_message() -> None:
    items: list[proxy_service.JsonValue] = [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "local context"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
    ]

    assert proxy_service._trim_http_bridge_previous_response_input_items(items) == items


def test_trim_http_bridge_previous_response_input_items_trims_marked_replay_outputs() -> None:
    items: list[proxy_service.JsonValue] = [
        {"id": "rs_replay", "type": "reasoning", "summary": []},
        {
            "id": "msg_replay",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior"}],
        },
        {
            "id": "fc_replay",
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
    ]

    assert proxy_service._trim_http_bridge_previous_response_input_items(items) == items[3:]


def test_trim_http_bridge_previous_response_input_items_preserves_unmarked_call_context() -> None:
    items: list[proxy_service.JsonValue] = [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "local context"}]},
        {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
    ]

    assert proxy_service._trim_http_bridge_previous_response_input_items(items) == items


@pytest.mark.asyncio
async def test_http_bridge_stream_masks_single_top_level_previous_response_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(service, "_finalize_websocket_request_state", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-single-prev", None),
        headers={"session_id": "sid-single-prev"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-single-prev",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.1",
        account=cast(Any, SimpleNamespace(id="acc-single-prev", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-single-prev",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_missing_single",
    )
    upstream_text = json.dumps(
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Previous response with id 'resp_missing_single' not found.",
                "param": "previous_response_id",
            },
        },
        separators=(",", ":"),
    )

    async def fake_submit_http_bridge_request(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        del text_data, queue_limit
        target_session.pending_requests.append(request_state)
        await service._process_http_bridge_upstream_text(target_session, upstream_text)

    monkeypatch.setattr(service, "_submit_http_bridge_request", fake_submit_http_bridge_request)

    events = [
        event
        async for event in service._stream_http_bridge_session_events(
            session,
            request_state=request_state,
            text_data="{}",
            queue_limit=8,
            propagate_http_errors=False,
            downstream_turn_state=None,
        )
    ]

    assert session.upstream_control.reconnect_requested is False
    assert request_state.error_http_status_override == 502
    assert len(events) == 1
    event_block = events[0]
    assert "previous_response_not_found" not in event_block
    payload = proxy_service.parse_sse_data_json(event_block)
    assert isinstance(payload, dict)
    assert payload["type"] == "response.failed"
    response = payload["response"]
    assert isinstance(response, dict)
    error = response["error"]
    assert isinstance(error, dict)
    assert error["code"] == "stream_incomplete"


@pytest.mark.asyncio
async def test_http_bridge_keepalive_counts_as_first_yield_before_late_response_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(sse_keepalive_interval_seconds=0.001),
    )
    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS", 0.001)

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-keepalive-first", None),
        headers={"session_id": "sid-keepalive-first"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-keepalive-first",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.1",
        account=cast(Any, SimpleNamespace(id="acc-keepalive-first", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-keepalive-first",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
        response_id="resp_keepalive_first",
    )

    async def fake_submit_http_bridge_request(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        del text_data, queue_limit
        target_session.pending_requests.append(request_state)

    monkeypatch.setattr(service, "_submit_http_bridge_request", fake_submit_http_bridge_request)

    stream = service._stream_http_bridge_session_events(
        session,
        request_state=request_state,
        text_data="{}",
        queue_limit=8,
        propagate_http_errors=True,
        downstream_turn_state=None,
    )

    keepalive = await asyncio.wait_for(anext(stream), timeout=1.0)
    assert "response.in_progress" in keepalive

    event_queue = request_state.event_queue
    assert event_queue is not None
    request_state.error_http_status_override = 502
    await event_queue.put(
        proxy_service.format_sse_event(
            proxy_service.response_failed_event(
                "upstream_unavailable",
                "upstream failed after keepalive",
                response_id="resp_keepalive_first",
            )
        )
    )
    failed = await asyncio.wait_for(anext(stream), timeout=1.0)
    assert "response.failed" in failed
    assert "upstream_unavailable" in failed

    await event_queue.put(None)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1.0)


@pytest.mark.asyncio
async def test_http_bridge_account_capacity_wait_sends_keepalive_instead_of_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(
            sse_keepalive_interval_seconds=0.001,
            stream_idle_timeout_seconds=0.001,
        ),
    )
    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS", 0.001)

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-wait", None),
        headers={"session_id": "sid-capacity-wait"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-capacity-wait",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.1",
        account=cast(Any, SimpleNamespace(id="acc-capacity-wait", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-capacity-wait",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
    )
    request_state.account_capacity_waiting = True
    request_state.account_capacity_wait_reason = "Rate limit exceeded. Try again in 120s"
    request_state.account_capacity_wait_started_at = time.monotonic() - 3.0
    request_state.account_capacity_wait_retry_after_seconds = 120.0

    async def fake_submit_http_bridge_request(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        del text_data, queue_limit
        target_session.pending_requests.append(request_state)

    monkeypatch.setattr(service, "_submit_http_bridge_request", fake_submit_http_bridge_request)

    stream = service._stream_http_bridge_session_events(
        session,
        request_state=request_state,
        text_data="{}",
        queue_limit=8,
        propagate_http_errors=True,
        downstream_turn_state=None,
    )

    keepalive = await asyncio.wait_for(anext(stream), timeout=1.0)
    payload = proxy_service.parse_sse_data_json(keepalive)

    assert payload is not None
    assert payload["type"] == "codex.keepalive"
    assert payload["status"] == "waiting_for_account_capacity"
    assert payload["request_id"] == "req-capacity-wait"
    assert "stream_idle_timeout" not in keepalive

    await stream.aclose()


@pytest.mark.asyncio
async def test_http_bridge_capacity_wait_with_response_id_sends_explicit_keepalive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(
            sse_keepalive_interval_seconds=0.001,
            stream_idle_timeout_seconds=0.001,
        ),
    )
    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS", 0.001)

    session = _make_bridge_session(key_value="sid-capacity-response")
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-capacity-response",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        response_id="resp-capacity-response",
        transport="http",
    )
    request_state.account_capacity_waiting = True
    request_state.account_capacity_wait_reason = "Rate limit exceeded. Try again in 120s"
    request_state.account_capacity_wait_started_at = time.monotonic() - 3.0
    request_state.account_capacity_wait_retry_after_seconds = 120.0

    async def fake_submit_http_bridge_request(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        del text_data, queue_limit
        target_session.pending_requests.append(request_state)

    monkeypatch.setattr(service, "_submit_http_bridge_request", fake_submit_http_bridge_request)

    stream = service._stream_http_bridge_session_events(
        session,
        request_state=request_state,
        text_data="{}",
        queue_limit=8,
        propagate_http_errors=True,
        downstream_turn_state=None,
    )

    keepalive = proxy_service.parse_sse_data_json(await asyncio.wait_for(anext(stream), timeout=1.0))
    in_progress = proxy_service.parse_sse_data_json(await asyncio.wait_for(anext(stream), timeout=1.0))

    assert keepalive is not None
    assert keepalive["type"] == "codex.keepalive"
    assert keepalive["status"] == "waiting_for_account_capacity"
    assert in_progress is not None
    assert in_progress["type"] == "response.in_progress"
    response = in_progress["response"]
    assert isinstance(response, dict)
    assert response["id"] == "resp-capacity-response"

    await stream.aclose()


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_reuses_live_local_session_without_ring_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", "bridge-key", None)
    existing = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace()),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = existing
    monkeypatch.setattr(
        service,
        "_prune_http_bridge_sessions_locked",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )

    async def _unexpected_owner_lookup(*args: object, **kwargs: object) -> str:
        raise AssertionError("live local session reuse must not hit the ring")

    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", _unexpected_owner_lookup)
    monkeypatch.setattr(proxy_service, "_active_http_bridge_instance_ring", _unexpected_owner_lookup)

    reused = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert reused is existing
    assert reused.request_model == "gpt-5.4"
    assert reused.last_used_at > 1.0


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_skips_prune_when_pending_lock_is_wedged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("request", "bridge-wedged-idle", None)
    existing_session = _make_bridge_session(key=key, key_value="bridge-wedged-idle")
    existing_session.last_used_at = time.monotonic() - 300.0
    existing_session.idle_ttl_seconds = 1.0
    service._http_bridge_sessions[key] = existing_session
    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()

    async def hold_pending_lock() -> None:
        async with existing_session.pending_lock:
            lock_acquired.set()
            await release_lock.wait()

    lock_holder = asyncio.create_task(hold_pending_lock())
    await asyncio.wait_for(lock_acquired.wait(), timeout=1.0)

    create_http_bridge_session = AsyncMock()
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    close_http_bridge_session = AsyncMock()
    monkeypatch.setattr(service, "_close_http_bridge_session", close_http_bridge_session)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )

    try:
        resolved = await asyncio.wait_for(
            service._get_or_create_http_bridge_session(
                key,
                headers={},
                affinity=proxy_service._AffinityPolicy(key="bridge-wedged-idle"),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            ),
            timeout=1.0,
        )
    finally:
        release_lock.set()
        await asyncio.wait_for(lock_holder, timeout=1.0)

    assert resolved is existing_session
    assert existing_session.closed is False
    assert service._http_bridge_sessions[key] is existing_session
    close_http_bridge_session.assert_not_awaited()
    create_http_bridge_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_prune_http_bridge_session_skips_wedged_session_with_visible_pending_request() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("request", "bridge-wedged-visible", None)
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-wedged-visible",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
    )
    session = _make_bridge_session(
        key=key,
        key_value="bridge-wedged-visible",
        pending_requests=deque([request_state]),
        queued_request_count=1,
    )
    session.last_used_at = time.monotonic() - 300.0
    session.idle_ttl_seconds = 1.0
    service._http_bridge_sessions[key] = session
    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()

    async def hold_pending_lock() -> None:
        async with session.pending_lock:
            lock_acquired.set()
            await release_lock.wait()

    lock_holder = asyncio.create_task(hold_pending_lock())
    await asyncio.wait_for(lock_acquired.wait(), timeout=1.0)
    try:
        async with service._http_bridge_lock:
            sessions_to_close = service._prune_http_bridge_sessions_locked()
    finally:
        release_lock.set()
        await asyncio.wait_for(lock_holder, timeout=1.0)

    assert sessions_to_close == []
    assert service._http_bridge_sessions[key] is session
    assert session.closed is False


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_replaces_live_session_when_account_is_no_longer_assigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("request", "bridge-key", "key-1")
    stale_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-stale", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    replacement_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-fresh", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = stale_session
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(
        service,
        "_create_http_bridge_session",
        AsyncMock(return_value=replacement_session),
    )
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a"])),
    )
    close_session = AsyncMock()
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)

    reused = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        api_key=_make_api_key(key_id="key-1", assigned_account_ids=["acc-fresh"]),
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert reused is replacement_session
    assert service._http_bridge_sessions[key] is replacement_session
    assert stale_session.closed is True
    await _wait_for_close_await(close_session, stale_session)


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_replaces_prompt_cache_session_promoted_to_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key", "key-1")
    stale_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        codex_session=True,
        downstream_turn_state="http_turn_legacy",
        downstream_turn_state_aliases={"http_turn_legacy"},
        previous_response_ids=set(),
    )
    replacement_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = stale_session
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(
        service,
        "_create_http_bridge_session",
        AsyncMock(return_value=replacement_session),
    )
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a"])),
    )
    close_session = AsyncMock()
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)

    reused = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        api_key=_make_api_key(key_id="key-1", assigned_account_ids=["acc-1"], account_assignment_scope_enabled=True),
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert reused is replacement_session
    assert service._http_bridge_sessions[key] is replacement_session
    assert stale_session.closed is True
    await _wait_for_close_await(close_session, stale_session)


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_registers_turn_state_alias_without_rekeying_prompt_cache_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    prompt_cache_key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key", "key-1")
    session = proxy_service._HTTPBridgeSession(
        key=prompt_cache_key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        codex_session=False,
        downstream_turn_state=None,
        downstream_turn_state_aliases=set(),
        previous_response_ids={"resp_prev_1"},
    )
    service._http_bridge_sessions[prompt_cache_key] = session
    service._http_bridge_previous_response_index[
        proxy_service._http_bridge_previous_response_alias_key("resp_prev_1", "key-1")
    ] = prompt_cache_key
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a"])),
    )
    refresh_durable = AsyncMock()
    monkeypatch.setattr(service, "_refresh_durable_http_bridge_session", refresh_durable)

    resolved = await service._get_or_create_http_bridge_session(
        prompt_cache_key,
        headers={"x-codex-turn-state": "http_turn_promoted"},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        api_key=_make_api_key(key_id="key-1", assigned_account_ids=["acc-1"], account_assignment_scope_enabled=True),
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
    )

    assert resolved is session
    assert session.key == prompt_cache_key
    assert service._http_bridge_sessions[prompt_cache_key] is session
    assert (
        service._http_bridge_previous_response_index[
            proxy_service._http_bridge_previous_response_alias_key("resp_prev_1", "key-1")
        ]
        == prompt_cache_key
    )
    assert (
        service._http_bridge_turn_state_index[
            proxy_service._http_bridge_turn_state_alias_key("http_turn_promoted", "key-1")
        ]
        == prompt_cache_key
    )
    refresh_durable.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_stream_via_http_bridge_turn_state_request_ignores_prompt_cache_owner_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": "hello"}
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-hard-turn-state",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)

    def fake_prepare(
        _prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_promoted", None),
        headers={"x-codex-turn-state": "http_turn_promoted"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_promoted",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    captured_key: dict[str, object] = {}
    captured_lookup: dict[str, object] = {}

    async def fake_get_or_create_http_bridge_session(*args: object, **kwargs: object):
        captured_key["value"] = args[0]
        captured_lookup["value"] = kwargs.get("durable_lookup")
        return session

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="durable-prompt-cache",
                canonical_kind="prompt_cache",
                canonical_key="cache-derived",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-remote",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_promoted",
                latest_response_id=None,
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", fake_get_or_create_http_bridge_session)
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-turn-state": "http_turn_promoted"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert request_state.affinity_policy.key == "http_turn_promoted"
    assert request_state.affinity_policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    key = cast(proxy_service._HTTPBridgeSessionKey, captured_key["value"])
    assert key.affinity_kind == "prompt_cache"
    assert key.affinity_key == "cache-derived"
    lookup = cast(proxy_service.DurableBridgeLookup, captured_lookup["value"])
    assert lookup.canonical_kind == "prompt_cache"
    assert lookup.canonical_key == "cache-derived"
    assert lookup.owner_instance_id == "instance-remote"
    assert lookup.lease_expires_at is not None


@pytest.mark.asyncio
async def test_stream_via_http_bridge_keeps_sse_alive_while_session_creation_waits_for_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    settings = SimpleNamespace(
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=1800,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
    )
    session = _make_bridge_session(key_value="sid-capacity-create")
    get_or_create = AsyncMock(
        side_effect=[
            ProxyResponseError(
                503,
                openai_error("no_accounts", "Rate limit exceeded. Try again in 120s"),
            ),
            session,
        ]
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-capacity-create",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic() - 10.0,
        transport="http",
    )

    def fake_prepare(
        _prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        return request_state, '{"type":"response.create"}'

    async def fake_stream_events(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        del request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_capacity_create_ok",'
            '"usage":{"input_tokens":1,"output_tokens":2}}}\n\n'
        )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=settings)),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(
            proxy_request_budget_seconds=0.001,
            http_responses_session_bridge_request_budget_seconds=120.0,
        ),
    )
    monkeypatch.setattr(http_bridge_streaming_module, "_http_bridge_account_capacity_wait_seconds", lambda _exc: 0.001)
    monkeypatch.setattr(http_bridge_streaming_module, "_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_events)

    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True}
    )

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"session_id": "sid-capacity-create"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    keepalive = proxy_service.parse_sse_data_json(chunks[0])
    completed = proxy_service.parse_sse_data_json(chunks[-1])

    assert keepalive is not None
    assert completed is not None
    assert keepalive["type"] == "codex.keepalive"
    assert keepalive["status"] == "waiting_for_account_capacity"
    assert completed["type"] == "response.completed"
    assert get_or_create.await_count == 2
    expected_deadline = request_state.started_at + 120.0
    assert get_or_create.await_args_list[0].kwargs["request_deadline"] == pytest.approx(expected_deadline)
    assert get_or_create.await_args_list[1].kwargs["request_deadline"] == pytest.approx(expected_deadline)


@pytest.mark.asyncio
async def test_stream_via_http_bridge_stops_session_creation_retry_after_budget_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    settings = SimpleNamespace(
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=1800,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
    )
    get_or_create = AsyncMock(
        side_effect=ProxyResponseError(
            503,
            openai_error("no_accounts", "Rate limit exceeded. Try again in 120s"),
        )
    )
    now = 100.0
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-capacity-create-budget",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=now,
        transport="http",
    )

    def fake_prepare(
        _prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        return request_state, '{"type":"response.create"}'

    async def fake_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=settings)),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(
            proxy_request_budget_seconds=1.0,
            http_responses_session_bridge_request_budget_seconds=1.0,
        ),
    )
    monkeypatch.setattr(
        http_bridge_streaming_module,
        "_service_time",
        lambda: SimpleNamespace(monotonic=lambda: now),
    )
    monkeypatch.setattr(http_bridge_streaming_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(http_bridge_streaming_module, "_http_bridge_account_capacity_wait_seconds", lambda _exc: 120.0)
    monkeypatch.setattr(http_bridge_streaming_module, "_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS", 120.0)
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)

    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True}
    )
    chunks: list[str] = []

    with pytest.raises(ProxyResponseError):
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"session_id": "sid-capacity-create-budget"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        ):
            chunks.append(chunk)

    keepalive = proxy_service.parse_sse_data_json(chunks[0])

    assert keepalive is not None
    assert keepalive["type"] == "codex.keepalive"
    assert keepalive["status"] == "waiting_for_account_capacity"
    assert get_or_create.await_count == 1


def test_http_bridge_session_key_infers_strength_from_affinity_kind() -> None:
    assert proxy_service._HTTPBridgeSessionKey("turn_state_header", "turn", None).strength == "hard"
    assert proxy_service._HTTPBridgeSessionKey("session_header", "session", None).strength == "hard"
    assert proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache", None).strength == "soft"
    assert proxy_service._HTTPBridgeSessionKey("request", "request", None).strength == "soft"


def test_http_bridge_owner_check_required_keeps_prompt_cache_soft() -> None:
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache", None)

    assert proxy_service._http_bridge_owner_check_required(key, gateway_safe_mode=False) is False
    assert proxy_service._http_bridge_owner_check_required(key, gateway_safe_mode=True) is False


def test_http_bridge_owner_check_required_enables_sticky_thread_in_gateway_safe_mode() -> None:
    key = proxy_service._HTTPBridgeSessionKey("sticky_thread", "thread-key", None)

    assert proxy_service._http_bridge_owner_check_required(key, gateway_safe_mode=False) is False
    assert proxy_service._http_bridge_owner_check_required(key, gateway_safe_mode=True) is True


@pytest.mark.asyncio
async def test_stream_via_http_bridge_soft_prompt_cache_queue_full_reroutes(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "prompt_cache_key": "soft-queue-full",
        }
    )
    saturated_session = _make_bridge_session(key_value="soft-queue-full", queued_request_count=8)
    saturated_session.key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "soft-queue-full", None)
    reroute_session = _make_bridge_session(key_value="soft-reroute")
    capacity_unavailable = ProxyResponseError(
        503,
        proxy_service.openai_error("no_accounts", "Rate limit exceeded. Try again in 120s"),
    )
    get_or_create = AsyncMock(side_effect=[saturated_session, capacity_unavailable, reroute_session])

    async def fake_stream_events(
        session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        del request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        if session is saturated_session:
            raise ProxyResponseError(
                429,
                proxy_service.openai_error(
                    "bridge_queue_full",
                    "HTTP responses session bridge queue is full",
                    error_type="rate_limit_error",
                ),
            )
        yield 'data: {"type":"response.completed"}\n\n'

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_events)
    monkeypatch.setattr(http_bridge_streaming_module, "_http_bridge_account_capacity_wait_seconds", lambda _exc: 0.001)
    monkeypatch.setattr(http_bridge_streaming_module, "_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS", 0.001)

    caplog.set_level(logging.INFO, logger="app.modules.proxy.service")
    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=True,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    keepalive = proxy_service.parse_sse_data_json(chunks[0])
    assert keepalive is not None
    assert keepalive["status"] == "waiting_for_account_capacity"
    assert chunks[-1] == 'data: {"type":"response.completed"}\n\n'
    assert get_or_create.await_count == 3
    reroute_key = get_or_create.await_args_list[1].args[0]
    retry_reroute_key = get_or_create.await_args_list[2].args[0]
    assert reroute_key.affinity_kind == "internal_soft_affinity_reroute"
    assert reroute_key.strength == "soft"
    assert retry_reroute_key.affinity_kind == "internal_soft_affinity_reroute"
    assert retry_reroute_key.strength == "soft"
    assert get_or_create.await_args_list[1].kwargs["previous_response_id"] is None
    assert get_or_create.await_args_list[2].kwargs["previous_response_id"] is None
    assert "internal_soft_affinity_reroute" in caplog.text


@pytest.mark.asyncio
async def test_stream_via_http_bridge_file_pin_queue_full_does_not_reroute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"type": "input_file", "file_id": "file_doc"}],
        }
    )
    saturated_session = _make_bridge_session(key_value="file-pin-queue-full", queued_request_count=8)
    get_or_create = AsyncMock(return_value=saturated_session)

    async def fake_stream_events(
        session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        del session, request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        raise ProxyResponseError(
            429,
            proxy_service.openai_error(
                "bridge_queue_full",
                "HTTP responses session bridge queue is full",
                error_type="rate_limit_error",
            ),
        )
        yield ""

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_events)

    with pytest.raises(ProxyResponseError) as info:
        async for _ in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=True,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
            rewritten_file_account_id="acc-file",
        ):
            pass

    assert info.value.status_code == 429
    assert get_or_create.await_count == 1
    create_call = get_or_create.await_args
    assert create_call is not None
    assert create_call.kwargs["preferred_account_id"] == "acc-file"
    assert create_call.kwargs["fallback_on_preferred_account_unavailable"] is False


@pytest.mark.asyncio
async def test_select_account_with_budget_prefers_durable_account_id_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    select_account = AsyncMock(
        return_value=proxy_service.AccountSelection(
            account=cast(Any, SimpleNamespace(id="acc-preferred")),
            error_message=None,
            error_code=None,
        )
    )
    service._load_balancer = cast(Any, SimpleNamespace(select_account=select_account))
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(sticky_reallocation_budget_threshold_pct=95.0))
        ),
    )

    selection = await service._select_account_with_budget(
        time.monotonic() + 60.0,
        request_id="req-1",
        kind="http_bridge",
        request_stage="reattach",
        prefer_earlier_reset_window="primary",
        preferred_account_id="acc-preferred",
    )

    assert selection.account is not None
    assert selection.account.id == "acc-preferred"
    assert select_account.await_count == 1
    first_call = select_account.await_args_list[0]
    assert first_call.kwargs["account_ids"] == {"acc-preferred"}


@pytest.mark.asyncio
async def test_select_account_with_budget_skips_preferred_account_outside_assignment_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    select_account = AsyncMock(
        return_value=proxy_service.AccountSelection(
            account=cast(Any, SimpleNamespace(id="acc-allowed")),
            error_message=None,
            error_code=None,
        )
    )
    service._load_balancer = cast(Any, SimpleNamespace(select_account=select_account))
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(sticky_reallocation_budget_threshold_pct=95.0))
        ),
    )

    selection = await service._select_account_with_budget(
        time.monotonic() + 60.0,
        request_id="req-2",
        kind="http_bridge",
        request_stage="reattach",
        api_key=_make_api_key(key_id="key-1", assigned_account_ids=["acc-allowed"]),
        prefer_earlier_reset_window="primary",
        preferred_account_id="acc-preferred",
    )

    assert selection.account is not None
    assert selection.account.id == "acc-allowed"
    assert select_account.await_count == 1
    first_call = select_account.await_args_list[0]
    assert first_call.kwargs["account_ids"] == {"acc-allowed"}


@pytest.mark.asyncio
async def test_create_http_bridge_session_passes_dashboard_reset_window_to_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=True,
        prefer_earlier_reset_window="primary",
        routing_strategy="usage_weighted",
    )
    selection_kwargs: list[dict[str, object]] = []

    async def select_account(_deadline: float, **kwargs: object) -> proxy_service.AccountSelection:
        selection_kwargs.append(kwargs)
        return proxy_service.AccountSelection(account=None, error_message="No active accounts available")

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service, "get_settings_cache", lambda: SimpleNamespace(get=AsyncMock(return_value=settings))
    )
    monkeypatch.setattr(service, "_select_account_with_budget_compatible", select_account)

    with pytest.raises(ProxyResponseError):
        await service._create_http_bridge_session(
            proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
            headers={},
            affinity=proxy_service._AffinityPolicy(key="sid-123"),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
        )

    assert selection_kwargs[0]["prefer_earlier_reset_accounts"] is True
    assert selection_kwargs[0]["prefer_earlier_reset_window"] == "primary"


@pytest.mark.asyncio
async def test_reconnect_http_bridge_session_passes_dashboard_reset_window_to_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session()
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=True,
        prefer_earlier_reset_window="primary",
        routing_strategy="usage_weighted",
    )
    selection_kwargs: list[dict[str, object]] = []

    async def select_account(_deadline: float, **kwargs: object) -> proxy_service.AccountSelection:
        selection_kwargs.append(kwargs)
        return proxy_service.AccountSelection(account=None, error_message="No active accounts available")

    request_state = proxy_service._WebSocketRequestState(
        request_id="req-reconnect",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service, "get_settings_cache", lambda: SimpleNamespace(get=AsyncMock(return_value=settings))
    )
    monkeypatch.setattr(service, "_select_account_with_budget_compatible", select_account)

    with pytest.raises(ProxyResponseError):
        await service._reconnect_http_bridge_session(session, request_state=request_state)

    assert selection_kwargs[0]["prefer_earlier_reset_accounts"] is True
    assert selection_kwargs[0]["prefer_earlier_reset_window"] == "primary"


@pytest.mark.asyncio
async def test_reconnect_http_bridge_session_uses_bridge_budget_for_capacity_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session()
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        prefer_earlier_reset_window="secondary",
        routing_strategy="usage_weighted",
    )
    sleep_calls: list[dict[str, object]] = []

    async def select_account(_deadline: float, **_kwargs: object) -> proxy_service.AccountSelection:
        return proxy_service.AccountSelection(
            account=None,
            error_message="Rate limit exceeded. Try again in 120s",
            error_code="no_accounts",
        )

    async def sleep_for_recovery(*_args: object, **kwargs: object) -> bool:
        sleep_calls.append(kwargs)
        return False

    request_state = proxy_service._WebSocketRequestState(
        request_id="req-reconnect-bridge-budget",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=100.0,
    )
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.5)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(
            proxy_request_budget_seconds=0.001,
            http_responses_session_bridge_request_budget_seconds=120.0,
        ),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=settings)),
    )
    monkeypatch.setattr(service, "_select_account_with_budget_compatible", select_account)
    monkeypatch.setattr(http_bridge_mixin_module, "_sleep_for_account_selection_recovery", sleep_for_recovery)

    with pytest.raises(ProxyResponseError):
        await service._reconnect_http_bridge_session(session, request_state=request_state)

    assert sleep_calls
    assert sleep_calls[0]["max_sleep_seconds"] == pytest.approx(119.5)


@pytest.mark.asyncio
async def test_reconnect_http_bridge_session_preserves_exclusions_after_capacity_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session()
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        prefer_earlier_reset_window="secondary",
        routing_strategy="usage_weighted",
    )
    selection_kwargs: list[dict[str, object]] = []
    account = cast(Any, SimpleNamespace(id=session.account.id, status=AccountStatus.ACTIVE))

    async def select_account(_deadline: float, **kwargs: object) -> proxy_service.AccountSelection:
        selection_kwargs.append(kwargs)
        if len(selection_kwargs) == 1:
            return proxy_service.AccountSelection(account=account, error_message=None)
        return proxy_service.AccountSelection(
            account=None,
            error_message="Rate limit exceeded. Try again in 120s",
            error_code="no_accounts",
        )

    async def fail_refresh(*_args: object, **_kwargs: object) -> Any:
        raise RefreshError("invalid_grant", "refresh failed", True)

    sleep_calls = 0

    async def sleep_for_recovery(*_args: object, **_kwargs: object) -> bool:
        nonlocal sleep_calls
        sleep_calls += 1
        return sleep_calls == 1

    request_state = proxy_service._WebSocketRequestState(
        request_id="req-reconnect-exclusions",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
    )
    request_state.excluded_account_ids.add("acc-request-state")
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=settings)),
    )
    monkeypatch.setattr(service, "_select_account_with_budget_compatible", select_account)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", fail_refresh)
    service._load_balancer = cast(
        Any,
        SimpleNamespace(
            mark_permanent_failure=AsyncMock(),
            release_account_lease=AsyncMock(),
        ),
    )
    monkeypatch.setattr(http_bridge_mixin_module, "_sleep_for_account_selection_recovery", sleep_for_recovery)

    with pytest.raises(ProxyResponseError):
        await service._reconnect_http_bridge_session(session, request_state=request_state)

    assert len(selection_kwargs) == 3
    assert selection_kwargs[2]["exclude_account_ids"] == {"acc-request-state", session.account.id}


async def test_select_account_with_budget_required_file_pin_does_not_fallback_on_account_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    select_account = AsyncMock(
        side_effect=[
            proxy_service.AccountSelection(
                account=None,
                error_message="Account stream capacity is exhausted",
                error_code="account_stream_cap",
            ),
            proxy_service.AccountSelection(
                account=cast(Any, SimpleNamespace(id="acc-other")),
                error_message=None,
                error_code=None,
            ),
        ]
    )
    service._load_balancer = cast(Any, SimpleNamespace(select_account=select_account))
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(sticky_reallocation_budget_threshold_pct=95.0))
        ),
    )

    selection = await service._select_account_with_budget(
        time.monotonic() + 60.0,
        request_id="req-file-pin",
        kind="stream",
        request_stage="first_turn",
        prefer_earlier_reset_window="secondary",
        preferred_account_id="acc-file-owner",
        lease_kind="stream",
        fallback_on_preferred_account_unavailable=False,
    )

    assert selection.account is None
    assert selection.error_code == "account_stream_cap"
    assert select_account.await_count == 1
    first_call = select_account.await_args_list[0]
    assert first_call.kwargs["account_ids"] == {"acc-file-owner"}


@pytest.mark.asyncio
async def test_select_account_with_budget_required_file_pin_overrides_single_account_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    select_account = AsyncMock(
        return_value=proxy_service.AccountSelection(
            account=cast(Any, SimpleNamespace(id="acc-file-owner")),
            error_message=None,
            error_code=None,
        )
    )
    service._load_balancer = cast(Any, SimpleNamespace(select_account=select_account))
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    routing_strategy="single_account",
                    single_account_id="acc-dashboard-selected",
                    sticky_reallocation_budget_threshold_pct=95.0,
                )
            )
        ),
    )

    selection = await service._select_account_with_budget(
        time.monotonic() + 60.0,
        request_id="req-file-pin-single-account",
        kind="stream",
        request_stage="first_turn",
        prefer_earlier_reset_window="secondary",
        preferred_account_id="acc-file-owner",
        lease_kind="stream",
        fallback_on_preferred_account_unavailable=False,
    )

    assert selection.account is not None
    assert selection.account.id == "acc-file-owner"
    assert select_account.await_count == 1
    first_call = select_account.await_args_list[0]
    assert first_call.kwargs["account_ids"] == {"acc-file-owner"}
    assert first_call.kwargs["routing_strategy"] == "capacity_weighted"


@pytest.mark.asyncio
async def test_select_account_with_budget_required_preferred_does_not_fallback_when_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    select_account = AsyncMock(
        return_value=proxy_service.AccountSelection(
            account=cast(Any, SimpleNamespace(id="acc-other")),
            error_message=None,
            error_code=None,
        )
    )
    service._load_balancer = cast(Any, SimpleNamespace(select_account=select_account))
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(sticky_reallocation_budget_threshold_pct=95.0))
        ),
    )

    selection = await service._select_account_with_budget(
        time.monotonic() + 60.0,
        request_id="req-file-pin-excluded",
        kind="stream",
        request_stage="retry",
        preferred_account_id="acc-file-owner",
        exclude_account_ids={"acc-file-owner"},
        fallback_on_preferred_account_unavailable=False,
    )

    assert selection.account is None
    assert selection.error_code == "preferred_account_unavailable"
    select_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_select_account_with_budget_soft_preference_can_fallback_after_account_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    select_account = AsyncMock(
        side_effect=[
            proxy_service.AccountSelection(
                account=None,
                error_message="Account stream capacity is exhausted",
                error_code="account_stream_cap",
            ),
            proxy_service.AccountSelection(
                account=cast(Any, SimpleNamespace(id="acc-other")),
                error_message=None,
                error_code=None,
            ),
        ]
    )
    service._load_balancer = cast(Any, SimpleNamespace(select_account=select_account))
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(sticky_reallocation_budget_threshold_pct=95.0))
        ),
    )

    selection = await service._select_account_with_budget(
        time.monotonic() + 60.0,
        request_id="req-soft-preferred",
        kind="stream",
        request_stage="first_turn",
        prefer_earlier_reset_window="secondary",
        preferred_account_id="acc-soft",
        lease_kind="stream",
    )

    assert selection.account is not None
    assert selection.account.id == "acc-other"
    assert select_account.await_count == 2
    first_call = select_account.await_args_list[0]
    second_call = select_account.await_args_list[1]
    assert first_call.kwargs["account_ids"] == {"acc-soft"}
    assert second_call.kwargs["account_ids"] is None


def test_headers_with_authorization_restores_missing_proxy_api_header() -> None:
    headers = proxy_service._headers_with_authorization({"x-request-id": "req-1"}, "Bearer proxy-key")

    assert headers["Authorization"] == "Bearer proxy-key"
    assert headers["x-request-id"] == "req-1"


def test_headers_with_authorization_does_not_override_existing_value() -> None:
    headers = proxy_service._headers_with_authorization({"authorization": "Bearer existing"}, "Bearer proxy-key")

    assert headers["authorization"] == "Bearer existing"


def test_make_http_bridge_session_key_prefers_signed_forwarded_affinity_over_generated_turn_state() -> None:
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})

    key = proxy_service._make_http_bridge_session_key(
        payload,
        headers={
            "x-codex-turn-state": "http_turn_generated",
            "x-codex-bridge-affinity-kind": "session_header",
            "x-codex-bridge-affinity-key": "sid-123",
        },
        affinity=proxy_service._AffinityPolicy(key="sid-123"),
        api_key=None,
        request_id="req-1",
        allow_forwarded_affinity_headers=True,
    )

    assert key.affinity_kind == "session_header"
    assert key.affinity_key == "sid-123"
    assert key.strength == "hard"


def test_make_http_bridge_session_key_ignores_forwarded_affinity_headers_on_public_requests() -> None:
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})

    key = proxy_service._make_http_bridge_session_key(
        payload,
        headers={
            "x-codex-bridge-affinity-kind": "session_header",
            "x-codex-bridge-affinity-key": "sid-123",
        },
        affinity=proxy_service._AffinityPolicy(key="cache-123", kind=proxy_service.StickySessionKind.PROMPT_CACHE),
        api_key=None,
        request_id="req-1",
        allow_forwarded_affinity_headers=False,
    )

    assert key.affinity_kind == "prompt_cache"
    assert key.affinity_key == "cache-123"
    assert key.strength == "soft"


def test_http_bridge_requires_cluster_registration_for_non_loopback_advertise_url() -> None:
    settings = Settings(
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_advertise_base_url="http://instance-a.codex-lb-bridge.default.svc.cluster.local:2455",
    )

    assert proxy_service._http_bridge_requires_cluster_registration(settings) is True


def test_http_bridge_requires_cluster_registration_skips_loopback_single_replica() -> None:
    settings = Settings(http_responses_session_bridge_advertise_base_url="http://127.0.0.1:2455")

    assert proxy_service._http_bridge_requires_cluster_registration(settings) is False


def test_durable_bridge_lookup_active_owner_accepts_naive_datetime() -> None:
    lookup = proxy_service.DurableBridgeLookup(
        session_id="sess-1",
        canonical_kind="session_header",
        canonical_key="sid-123",
        api_key_scope="__anonymous__",
        account_id="acc-1",
        owner_instance_id="instance-a",
        owner_epoch=1,
        lease_expires_at=datetime(2099, 1, 1, 0, 0, 0),
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state=None,
        latest_response_id=None,
    )

    assert proxy_service._durable_bridge_lookup_active_owner(lookup) == "instance-a"


@pytest.mark.asyncio
async def test_stream_via_http_bridge_injects_durable_previous_response_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": "hello"},
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-1",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured: dict[str, object] = {}

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        captured["previous_response_id"] = prepared_payload.previous_response_id
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-1",
                canonical_kind="session_header",
                canonical_key="sid-123",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-a",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_1",
                latest_response_id="resp_latest",
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-session-id": "sid-123"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured["previous_response_id"] == "resp_latest"


@pytest.mark.asyncio
async def test_stream_via_http_bridge_trims_replayed_tool_call_items_with_previous_response_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "previous_response_id": "resp_prev_tool_call",
            "input": [
                {"id": "rs_repeat", "type": "reasoning", "summary": []},
                {
                    "id": "msg_repeat",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "running command"}],
                },
                {
                    "id": "fc_repeat",
                    "type": "function_call",
                    "call_id": "call_repeat",
                    "name": "exec_command",
                    "arguments": '{"cmd":"date"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_repeat",
                    "output": "Wed May 6 16:00:00 UTC 2026",
                },
            ],
        }
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-trim-tool-call",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured_input: list[proxy_service.JsonValue] = []

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        assert isinstance(prepared_payload.input, list)
        captured_input[:] = cast(list[proxy_service.JsonValue], prepared_payload.input)
        request_state.previous_response_id = prepared_payload.previous_response_id
        return request_state, json.dumps({"type": "response.create", "input": prepared_payload.input})

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-session-id": "sid-123"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured_input == [
        {
            "type": "function_call_output",
            "call_id": "call_repeat",
            "output": "Wed May 6 16:00:00 UTC 2026",
        }
    ]


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_inject_session_anchor_for_soft_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": "hello"},
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-soft",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    prepared_previous_response_ids: list[str | None] = []

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        prepared_previous_response_ids.append(prepared_payload.previous_response_id)
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache-123", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="cache-123",
            kind=proxy_service.StickySessionKind.PROMPT_CACHE,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        last_completed_response_id="resp_soft_latest",
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_enabled=True,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert prepared_previous_response_ids == [None]


@pytest.mark.asyncio
async def test_stream_via_http_bridge_skips_session_anchor_injection_when_trim_would_not_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard session-level previous_response_id injection.

    The session anchor must only be injected when the trim branch would
    actually strip the already-stored prefix. If the incoming payload is
    a full resend whose prefix cannot be trimmed (non-list input, shorter
    history, or a prefix fingerprint mismatch), injecting an anchor would
    send both the full history and a previous_response_id upstream, which
    duplicates context and distorts output/cost.
    """
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    # Non-list input: trim cannot possibly apply, so no anchor should be
    # injected even though the session has a completed response.
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": "fresh turn text"},
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-session-anchor-guard",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    prepared_previous_response_ids: list[str | None] = []

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        prepared_previous_response_ids.append(prepared_payload.previous_response_id)
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-anchor-guard", None),
        headers={"x-codex-session-id": "sid-anchor-guard"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-anchor-guard",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        codex_session=True,
        last_completed_response_id="resp_session_latest",
        last_completed_input_count=3,
        last_completed_input_prefix_fingerprint=proxy_service._fingerprint_input_items(
            [
                {"role": "user", "content": [{"type": "input_text", "text": "a"}]},
                {"role": "assistant", "content": [{"type": "output_text", "text": "b"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "c"}]},
            ]
        ),
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_enabled=True,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-session-id": "sid-anchor-guard"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    # No anchor should have been injected because the non-list input
    # would have left the trim branch inert, which would have duplicated
    # context upstream.
    assert prepared_previous_response_ids == [None]


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_inject_durable_previous_response_anchor_for_full_resend_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
                {"role": "user", "content": "follow up"},
            ],
        },
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-full-resend",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured: dict[str, object] = {}
    prepared_input_lengths: list[int] = []

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        captured["previous_response_id"] = prepared_payload.previous_response_id
        inp = prepared_payload.input
        prepared_input_lengths.append(len(inp) if isinstance(inp, list) else 1)
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-1",
                canonical_kind="session_header",
                canonical_key="sid-123",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-a",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_1",
                latest_response_id="resp_latest",
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-session-id": "sid-123"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured["previous_response_id"] is None
    # Full-resend payloads are explicitly excluded from durable anchor
    # injection, so the bridge prepares the original request exactly once.
    assert prepared_input_lengths == [3]
    # This path never reaches the trim branch, so the fake request_state
    # returned by fake_prepare keeps its default metadata.
    assert request_state.input_full_fingerprint is None


@pytest.mark.asyncio
async def test_stream_via_http_bridge_injects_durable_anchor_for_trimmable_full_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    input_items = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
        {"role": "user", "content": "follow up"},
    ]
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": input_items,
        },
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-full-resend-trim",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    prepared_previous_response_ids: list[str | None] = []
    prepared_input_lengths: list[int] = []

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        prepared_previous_response_ids.append(prepared_payload.previous_response_id)
        inp = prepared_payload.input
        prepared_input_lengths.append(len(inp) if isinstance(inp, list) else 1)
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-1",
                canonical_kind="session_header",
                canonical_key="sid-123",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-a",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_1",
                latest_response_id="resp_latest",
                latest_input_item_count=2,
                latest_input_full_fingerprint=proxy_service._fingerprint_input_items(
                    cast(list[Any], payload.input)[:2]
                ),
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-session-id": "sid-123"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert prepared_previous_response_ids == [None, "resp_latest", "resp_latest"]
    assert prepared_input_lengths == [3, 3, 1]


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_inject_durable_previous_response_anchor_for_explicit_prompt_cache_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "prompt_cache_key": "thread-123",
        },
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-prompt-cache-anchor",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured: dict[str, object] = {}

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        captured["previous_response_id"] = prepared_payload.previous_response_id
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "thread-123", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="thread-123",
            kind=proxy_service.StickySessionKind.PROMPT_CACHE,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-1",
                canonical_kind="prompt_cache",
                canonical_key="thread-123",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-a",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_1",
                latest_response_id="resp_latest",
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured["previous_response_id"] is None


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_prefer_durable_account_for_soft_prompt_cache_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "prompt_cache_key": "thread-soft",
        },
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-soft-prompt-cache",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured: dict[str, object] = {}

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        captured["previous_response_id"] = prepared_payload.previous_response_id
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "thread-soft", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="thread-soft",
            kind=proxy_service.StickySessionKind.PROMPT_CACHE,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-soft-prompt-cache",
                canonical_kind="prompt_cache",
                canonical_key="thread-soft",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-a",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_soft",
                latest_response_id="resp_latest",
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)

    async def fake_get_or_create(
        *args: object,
        **kwargs: object,
    ) -> proxy_service._HTTPBridgeSession:
        captured["preferred_account_id"] = kwargs.get("preferred_account_id")
        return session

    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", fake_get_or_create)
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured["previous_response_id"] is None
    assert captured["preferred_account_id"] is None


@pytest.mark.asyncio
async def test_stream_via_http_bridge_prefers_durable_account_for_soft_prompt_cache_follow_up_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello again",
            "prompt_cache_key": "thread-soft-follow-up",
        },
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-soft-prompt-cache-follow-up",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured: dict[str, object] = {}

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        captured["previous_response_id"] = prepared_payload.previous_response_id
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "thread-soft-follow-up", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="thread-soft-follow-up",
            kind=proxy_service.StickySessionKind.PROMPT_CACHE,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-soft-follow-up",
                canonical_kind="prompt_cache",
                canonical_key="thread-soft-follow-up",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-a",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_soft_follow_up",
                latest_response_id="resp_latest",
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)

    async def fake_get_or_create(
        *args: object,
        **kwargs: object,
    ) -> proxy_service._HTTPBridgeSession:
        captured["preferred_account_id"] = kwargs.get("preferred_account_id")
        captured["request_stage"] = kwargs.get("request_stage")
        return session

    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", fake_get_or_create)
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-turn-state": "http_turn_soft_follow_up"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured["previous_response_id"] is None
    assert captured["request_stage"] == "follow_up"
    assert captured["preferred_account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_close_http_bridge_session_fails_pending_downstream_requests() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-bridge-close",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=event_queue,
        transport="http",
    )
    pending_requests = deque([request_state])
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "close-thread", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="close-thread",
            kind=proxy_service.StickySessionKind.PROMPT_CACHE,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-close", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )

    await service._close_http_bridge_session(session)

    failed_event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
    assert failed_event is not None
    assert '"code":"stream_incomplete"' in failed_event
    assert "HTTP bridge session closed before response.completed" in failed_event
    assert await asyncio.wait_for(event_queue.get(), timeout=1.0) is None
    assert list(session.pending_requests) == []
    assert session.queued_request_count == 0


@pytest.mark.asyncio
async def test_close_http_bridge_session_releases_lease_before_pending_cleanup_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session(key_value="close-wedged-pending")
    lease = proxy_service.AccountLease(
        lease_id="lease-close-wedged-pending",
        account_id=session.account.id,
        kind="stream",
        acquired_at=1.0,
    )
    session.account_lease = lease
    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()
    lease_released = asyncio.Event()

    async def hold_pending_lock() -> None:
        async with session.pending_lock:
            lock_acquired.set()
            await release_lock.wait()

    async def release_account_lease(account_lease: proxy_service.AccountLease | None) -> None:
        assert account_lease is lease
        lease_released.set()

    lock_holder = asyncio.create_task(hold_pending_lock())
    await asyncio.wait_for(lock_acquired.wait(), timeout=1.0)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)

    close_task = asyncio.create_task(service._close_http_bridge_session(session))
    try:
        await asyncio.wait_for(lease_released.wait(), timeout=1.0)
        assert session.account_lease is None
        assert not close_task.done()
        close_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_task
    finally:
        release_lock.set()
        await asyncio.wait_for(lock_holder, timeout=1.0)


@pytest.mark.asyncio
async def test_close_http_bridge_session_continues_when_lease_release_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    close = AsyncMock()
    session = _make_bridge_session(key_value="close-lease-release-fails")
    session.upstream = cast(UpstreamResponsesWebSocket, SimpleNamespace(close=close))
    lease = proxy_service.AccountLease(
        lease_id="lease-close-release-fails",
        account_id=session.account.id,
        kind="stream",
        acquired_at=1.0,
    )
    session.account_lease = lease

    async def release_account_lease(_account_lease: proxy_service.AccountLease | None) -> None:
        raise RuntimeError("release failed")

    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)

    await service._close_http_bridge_session(session)

    assert session.account_lease is None
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_http_bridge_session_bounded_timeout_keeps_close_task_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session(key_value="close-timeout-continues")
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    close_finished = asyncio.Event()
    close_cancelled = False

    async def close_http_bridge_session(target: proxy_service._HTTPBridgeSession) -> None:
        nonlocal close_cancelled
        assert target is session
        close_started.set()
        try:
            await release_close.wait()
        except asyncio.CancelledError:
            close_cancelled = True
            raise
        finally:
            close_finished.set()

    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(service, "_close_http_bridge_session", close_http_bridge_session)

    await service._close_http_bridge_session_bounded(session, reason="test")

    await asyncio.wait_for(close_started.wait(), timeout=1.0)
    assert close_cancelled is False
    assert service._background_cleanup_tasks

    release_close.set()
    await asyncio.wait_for(close_finished.wait(), timeout=1.0)
    for _ in range(10):
        if not service._background_cleanup_tasks:
            break
        await asyncio.sleep(0)
    assert service._background_cleanup_tasks == set()
    assert close_cancelled is False


@pytest.mark.asyncio
async def test_close_http_bridge_session_bounded_cancellation_keeps_close_task_tracked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session(key_value="close-cancel-continues")
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    close_finished = asyncio.Event()
    close_cancelled = False

    async def close_http_bridge_session(target: proxy_service._HTTPBridgeSession) -> None:
        nonlocal close_cancelled
        assert target is session
        close_started.set()
        try:
            await release_close.wait()
        except asyncio.CancelledError:
            close_cancelled = True
            raise
        finally:
            close_finished.set()

    monkeypatch.setattr(service, "_close_http_bridge_session", close_http_bridge_session)
    bounded_task = asyncio.create_task(service._close_http_bridge_session_bounded(session, reason="test"))
    await asyncio.wait_for(close_started.wait(), timeout=1.0)

    bounded_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await bounded_task

    assert close_cancelled is False
    assert service._background_cleanup_tasks
    release_close.set()
    await asyncio.wait_for(close_finished.wait(), timeout=1.0)
    for _ in range(10):
        if not service._background_cleanup_tasks:
            break
        await asyncio.sleep(0)
    assert service._background_cleanup_tasks == set()
    assert close_cancelled is False


@pytest.mark.asyncio
async def test_http_bridge_unregister_aliases_preserves_new_owner_mapping() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    old_session = _make_bridge_session(key_value="old-alias-owner")
    new_key = proxy_service._HTTPBridgeSessionKey("session_header", "new-alias-owner", None)
    turn_state = "http_turn_shared_alias"
    previous_response_id = "resp_shared_alias"
    old_session.downstream_turn_state_aliases.add(turn_state)
    old_session.previous_response_ids.add(previous_response_id)
    turn_state_alias_key = proxy_service._http_bridge_turn_state_alias_key(turn_state, None)
    previous_response_alias_key = proxy_service._http_bridge_previous_response_alias_key(previous_response_id, None)
    service._http_bridge_turn_state_index[turn_state_alias_key] = new_key
    service._http_bridge_previous_response_index[previous_response_alias_key] = new_key

    await service._unregister_http_bridge_turn_states(old_session)
    await service._unregister_http_bridge_previous_response_ids(old_session)

    assert service._http_bridge_turn_state_index[turn_state_alias_key] == new_key
    assert service._http_bridge_previous_response_index[previous_response_alias_key] == new_key
    assert old_session.downstream_turn_state_aliases == set()
    assert old_session.previous_response_ids == set()


@pytest.mark.asyncio
async def test_http_bridge_unregister_aliases_removes_same_key_alias_not_owned_by_new_generation() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "same-key-alias-owner", None)
    old_session = _make_bridge_session(key=key, key_value="same-key-alias-owner")
    new_session = _make_bridge_session(key=key, key_value="same-key-alias-owner")
    turn_state = "http_turn_same_key_alias"
    previous_response_id = "resp_same_key_alias"
    old_session.downstream_turn_state_aliases.add(turn_state)
    old_session.previous_response_ids.add(previous_response_id)
    turn_state_alias_key = proxy_service._http_bridge_turn_state_alias_key(turn_state, None)
    previous_response_alias_key = proxy_service._http_bridge_previous_response_alias_key(previous_response_id, None)
    service._http_bridge_sessions[key] = new_session
    service._http_bridge_turn_state_index[turn_state_alias_key] = key
    service._http_bridge_previous_response_index[previous_response_alias_key] = key

    await service._unregister_http_bridge_turn_states(old_session)
    await service._unregister_http_bridge_previous_response_ids(old_session)

    assert turn_state_alias_key not in service._http_bridge_turn_state_index
    assert previous_response_alias_key not in service._http_bridge_previous_response_index
    assert old_session.downstream_turn_state_aliases == set()
    assert old_session.previous_response_ids == set()


@pytest.mark.asyncio
async def test_http_bridge_unregister_aliases_preserves_same_key_alias_owned_by_new_generation() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "same-key-owned-alias", None)
    old_session = _make_bridge_session(key=key, key_value="same-key-owned-alias")
    new_session = _make_bridge_session(key=key, key_value="same-key-owned-alias")
    turn_state = "http_turn_same_key_owned_alias"
    previous_response_id = "resp_same_key_owned_alias"
    old_session.downstream_turn_state_aliases.add(turn_state)
    old_session.previous_response_ids.add(previous_response_id)
    new_session.downstream_turn_state_aliases.add(turn_state)
    new_session.previous_response_ids.add(previous_response_id)
    turn_state_alias_key = proxy_service._http_bridge_turn_state_alias_key(turn_state, None)
    previous_response_alias_key = proxy_service._http_bridge_previous_response_alias_key(previous_response_id, None)
    service._http_bridge_sessions[key] = new_session
    service._http_bridge_turn_state_index[turn_state_alias_key] = key
    service._http_bridge_previous_response_index[previous_response_alias_key] = key

    await service._unregister_http_bridge_turn_states(old_session)
    await service._unregister_http_bridge_previous_response_ids(old_session)

    assert service._http_bridge_turn_state_index[turn_state_alias_key] == key
    assert service._http_bridge_previous_response_index[previous_response_alias_key] == key
    assert old_session.downstream_turn_state_aliases == set()
    assert old_session.previous_response_ids == set()


def test_http_bridge_drain_detach_removes_old_previous_response_alias() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "drain-detach-alias-owner", None)
    old_session = _make_bridge_session(key=key, key_value="drain-detach-alias-owner")
    previous_response_id = "resp_drain_detach_old"
    old_session.previous_response_ids.add(previous_response_id)
    previous_response_alias_key = proxy_service._http_bridge_previous_response_alias_key(previous_response_id, None)
    service._http_bridge_sessions[key] = old_session
    service._http_bridge_previous_response_index[previous_response_alias_key] = key

    detached = service._detach_http_bridge_session_locked(key, expected_session=old_session, mark_closed=False)

    assert detached is old_session
    assert old_session.closed is False
    assert previous_response_alias_key not in service._http_bridge_previous_response_index
    assert old_session.previous_response_ids == set()


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_inject_durable_anchor_for_live_turn_state_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": "hello"},
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-live-turn-state",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured: dict[str, object] = {}

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        captured["previous_response_id"] = prepared_payload.previous_response_id
        return request_state, '{"type":"response.create"}'

    session_key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_live", None)
    session = proxy_service._HTTPBridgeSession(
        key=session_key,
        headers={"x-codex-turn-state": "http_turn_live"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_live",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[session_key] = session
    service._http_bridge_turn_state_index[proxy_service._http_bridge_turn_state_alias_key("http_turn_live", None)] = (
        session_key
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-live-turn-state",
                canonical_kind="turn_state_header",
                canonical_key="http_turn_live",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-a",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_live",
                latest_response_id="resp_latest",
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-turn-state": "http_turn_live"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured["previous_response_id"] is None


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_inject_durable_anchor_for_live_prompt_cache_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "prompt_cache_key": "thread-live",
        },
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-live-prompt-cache",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured: dict[str, object] = {}

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        captured["previous_response_id"] = prepared_payload.previous_response_id
        return request_state, '{"type":"response.create"}'

    session_key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "thread-live", None)
    session = proxy_service._HTTPBridgeSession(
        key=session_key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="thread-live",
            kind=proxy_service.StickySessionKind.PROMPT_CACHE,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[session_key] = session

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-live-prompt-cache",
                canonical_kind="prompt_cache",
                canonical_key="thread-live",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-a",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state=None,
                latest_response_id="resp_latest",
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured["previous_response_id"] is None


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_inject_durable_anchor_when_forwarding_to_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": "hello"},
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-forward-owner",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured: dict[str, object] = {}

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        captured["previous_response_id"] = prepared_payload.previous_response_id
        return request_state, '{"type":"response.create"}'

    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_forward", None),
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: Settings(
            http_responses_session_bridge_enabled=True,
            http_responses_session_bridge_instance_id="instance-a",
        ),
    )
    service._ring_membership = cast(
        Any,
        SimpleNamespace(resolve_endpoint=AsyncMock(return_value="http://instance-b")),
    )
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-forward-owner",
                canonical_kind="turn_state_header",
                canonical_key="http_turn_forward",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-b",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_forward",
                latest_response_id="resp_latest",
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=owner_forward))

    async def fake_forward_http_bridge_request_to_owner(**kwargs: object):
        del kwargs
        if False:
            yield ""
        return

    monkeypatch.setattr(service, "_forward_http_bridge_request_to_owner", fake_forward_http_bridge_request_to_owner)

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-turn-state": "http_turn_forward"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured["previous_response_id"] is None


@pytest.mark.asyncio
async def test_stream_via_http_bridge_clears_injected_anchor_after_owner_unavailable_fresh_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    prefix_items = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}]
    input_items = [*prefix_items, {"role": "user", "content": "three"}]
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": input_items},
    )
    payload_prefix_items = cast(list[proxy_service.JsonValue], payload.input)[: len(prefix_items)]
    request_states: list[proxy_service._WebSocketRequestState] = []
    prepared_previous_response_ids: list[str | None] = []

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        prepared_previous_response_ids.append(prepared_payload.previous_response_id)
        state = proxy_service._WebSocketRequestState(
            request_id=f"req-{len(request_states)}",
            model="gpt-5.4",
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=1.0,
            event_queue=asyncio.Queue(),
            previous_response_id=prepared_payload.previous_response_id,
            transport="http",
        )
        request_states.append(state)
        return state, proxy_service._response_create_text(
            prepared_payload,
            include_type_field=True,
            client_metadata=None,
        )

    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_fresh", None),
    )
    owner_unavailable = proxy_service.ProxyResponseError(
        502,
        {
            "error": {
                "type": "server_error",
                "code": "upstream_unavailable",
                "message": "Previous response owner account is unavailable; retry later.",
            }
        },
    )
    get_or_create_calls = 0

    async def fake_get_or_create_http_bridge_session(*args: object, **kwargs: object):
        nonlocal get_or_create_calls
        del args, kwargs
        get_or_create_calls += 1
        if get_or_create_calls == 1:
            raise owner_unavailable
        return owner_forward

    forwarded_payloads: list[proxy_service.ResponsesRequest] = []

    async def fake_forward_http_bridge_request_to_owner(**kwargs: object):
        forwarded_payloads.append(cast(proxy_service.ResponsesRequest, kwargs["payload"]))
        if False:
            yield ""
        return

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: Settings(
            http_responses_session_bridge_enabled=True,
            http_responses_session_bridge_instance_id="instance-a",
        ),
    )
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-fresh-owner-unavailable",
                canonical_kind="turn_state_header",
                canonical_key="http_turn_fresh",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-b",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_fresh",
                latest_response_id="resp_latest",
                latest_input_item_count=len(prefix_items),
                latest_input_full_fingerprint=proxy_service._fingerprint_input_items(payload_prefix_items),
            )
        ),
    )
    monkeypatch.setattr(service, "_http_bridge_has_live_local_session", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_http_bridge_can_forward_to_active_owner", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", AsyncMock(return_value="acc-1"))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", fake_get_or_create_http_bridge_session)
    monkeypatch.setattr(service, "_forward_http_bridge_request_to_owner", fake_forward_http_bridge_request_to_owner)

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-turn-state": "http_turn_fresh"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert prepared_previous_response_ids[-2:] == ["resp_latest", None]
    assert forwarded_payloads == [payload]
    assert request_states[-1].previous_response_id is None
    assert request_states[-1].proxy_injected_previous_response_id is False


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_inject_durable_previous_response_anchor_for_derived_prompt_cache_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": "hello"},
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-derived-prompt-cache-anchor",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured: dict[str, object] = {}

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        captured["previous_response_id"] = prepared_payload.previous_response_id
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "derived-thread-123", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="derived-thread-123",
            kind=proxy_service.StickySessionKind.PROMPT_CACHE,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                        openai_prompt_cache_key_derivation_enabled=True,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="sess-1",
                canonical_kind="prompt_cache",
                canonical_key="derived-thread-123",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-a",
                owner_epoch=1,
                lease_expires_at=datetime.now(timezone.utc),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state="http_turn_1",
                latest_response_id="resp_latest",
            )
        ),
    )
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert captured["previous_response_id"] is None


@pytest.mark.asyncio
async def test_stream_via_http_bridge_resolves_previous_response_owner_from_request_logs_when_durable_lookup_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "previous_response_id": "resp_prev_owner_lookup",
        }
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-owner-lookup",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_prev_owner_lookup",
        session_id="turn_http_owner",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await event_queue.put(None)
    captured_preferred: dict[str, object] = {}

    def fake_prepare(
        _prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        return request_state, '{"type":"response.create"}'

    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    owner_lookup = AsyncMock(return_value="acc-owner-from-logs")
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", owner_lookup)
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)

    async def fake_get_or_create_http_bridge_session(*args: object, **kwargs: object):
        captured_preferred["value"] = kwargs.get("preferred_account_id")
        return session

    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", fake_get_or_create_http_bridge_session)
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-turn-state": "turn_http_owner"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    owner_lookup.assert_awaited_once_with(
        previous_response_id="resp_prev_owner_lookup",
        api_key=None,
        session_id="turn_http_owner",
        surface="http_bridge",
    )
    assert captured_preferred["value"] == "acc-owner-from-logs"


@pytest.mark.asyncio
async def test_stream_via_http_bridge_uses_generated_downstream_turn_state_for_owner_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "previous_response_id": "resp_prev_owner_lookup",
        }
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-generated-turn-state",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_prev_owner_lookup",
        session_id="sid-shared",
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-shared", None),
        headers={"x-codex-session-id": "sid-shared"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-shared",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    prepared_input_lengths: list[int] = []

    def fake_prepare(
        _prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id
        inp = _prepared_payload.input
        prepared_input_lengths.append(len(inp) if isinstance(inp, list) else 1)
        return request_state, '{"type":"response.create"}'

    async def fake_stream_http_bridge_session_events(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        del request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        yield 'data: {"type":"response.completed"}\n\n'

    owner_lookup = AsyncMock(return_value="acc-owner-from-turn-state")

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", owner_lookup)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_http_bridge_session_events)

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-session-id": "sid-shared"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
            downstream_turn_state="http_turn_generated",
        )
    ]

    assert chunks == ['data: {"type":"response.completed"}\n\n']
    owner_lookup.assert_awaited_once_with(
        previous_response_id="resp_prev_owner_lookup",
        api_key=None,
        session_id="http_turn_generated",
        surface="http_bridge",
    )
    assert request_state.session_id == "http_turn_generated"
    assert request_state.preferred_account_id == "acc-owner-from-turn-state"
    # No durable anchor is injected in this path; the request is prepared
    # once with the original single-item input while owner lookup uses the
    # generated downstream turn state for scoping.
    assert prepared_input_lengths == [1]


@pytest.mark.asyncio
async def test_http_bridge_waits_for_registration_for_hard_keys_before_startup_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.startup as startup_module

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    settings = Settings(
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_advertise_base_url="http://instance-a.bridge.default.svc.cluster.local:2455",
    )
    monkeypatch.setattr(startup_module, "_startup_complete", False)
    monkeypatch.setattr(startup_module, "_bridge_registration_complete", False)

    assert (
        await proxy_service._http_bridge_should_wait_for_registration(
            service,
            proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
            settings,
        )
        is True
    )


@pytest.mark.asyncio
async def test_forward_http_bridge_request_to_owner_preserves_session_header_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
    )
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    captured: dict[str, object] = {}

    async def fake_stream_responses(**kwargs: object):
        captured.update(kwargs)
        if False:
            yield ""
        return

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service,
        "_http_bridge_owner_client",
        cast(Any, SimpleNamespace(stream_responses=fake_stream_responses)),
    )

    chunks = [
        chunk
        async for chunk in service._forward_http_bridge_request_to_owner(
            owner_forward=owner_forward,
            payload=payload,
            headers={"x-codex-session-id": "sid-123"},
            api_key_reservation=None,
            codex_session_affinity=True,
            downstream_turn_state="http_turn_generated",
            request_started_at=10.0,
            proxy_api_authorization=None,
        )
    ]

    assert chunks == []
    context = cast(proxy_service.HTTPBridgeForwardContext, captured["context"])
    assert context.downstream_turn_state == "http_turn_generated"
    assert context.original_affinity_kind == "session_header"
    assert context.original_affinity_key == "sid-123"
    assert cast(dict[str, str], captured["headers"])["x-codex-session-id"] == "sid-123"


@pytest.mark.asyncio
async def test_forward_http_bridge_request_to_owner_raises_proxy_error_on_relay_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
    )
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})

    async def fake_stream_responses(**kwargs: object):
        del kwargs
        raise OwnerForwardRelayFailure("data: ignored\n\n")
        yield ""

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service,
        "_http_bridge_owner_client",
        cast(Any, SimpleNamespace(stream_responses=fake_stream_responses)),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        async for _ in service._forward_http_bridge_request_to_owner(
            owner_forward=owner_forward,
            payload=payload,
            headers={"x-codex-session-id": "sid-123"},
            api_key_reservation=None,
            codex_session_affinity=True,
            downstream_turn_state="http_turn_generated",
            request_started_at=10.0,
            proxy_api_authorization=None,
        ):
            pass

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"]["code"] == "bridge_owner_unreachable"


@pytest.mark.asyncio
async def test_forward_http_bridge_request_to_owner_emits_terminal_sse_after_forwarded_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
    )
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})

    async def fake_stream_responses(**kwargs: object):
        del kwargs
        yield "data: first\n\n"
        raise OwnerForwardRelayFailure("data: terminal\n\n")

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service,
        "_http_bridge_owner_client",
        cast(Any, SimpleNamespace(stream_responses=fake_stream_responses)),
    )

    chunks = [
        chunk
        async for chunk in service._forward_http_bridge_request_to_owner(
            owner_forward=owner_forward,
            payload=payload,
            headers={"x-codex-session-id": "sid-123"},
            api_key_reservation=None,
            codex_session_affinity=True,
            downstream_turn_state="http_turn_generated",
            request_started_at=10.0,
            proxy_api_authorization=None,
        )
    ]

    assert chunks == ["data: first\n\n", "data: terminal\n\n"]


@pytest.mark.asyncio
async def test_forward_http_bridge_request_to_owner_emits_terminal_sse_after_forwarded_proxy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
    )
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})

    async def fake_stream_responses(**kwargs: object):
        del kwargs
        yield proxy_service.format_sse_event(
            cast(Any, {"type": "response.created", "response": {"id": "resp_owner_1"}})
        )
        raise ProxyResponseError(503, proxy_service.openai_error("bridge_owner_unreachable", "boom"))

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service,
        "_http_bridge_owner_client",
        cast(Any, SimpleNamespace(stream_responses=fake_stream_responses)),
    )

    chunks = [
        chunk
        async for chunk in service._forward_http_bridge_request_to_owner(
            owner_forward=owner_forward,
            payload=payload,
            headers={"x-codex-session-id": "sid-123"},
            api_key_reservation=None,
            codex_session_affinity=True,
            downstream_turn_state="http_turn_generated",
            request_started_at=10.0,
            proxy_api_authorization=None,
        )
    ]

    assert chunks[0] == proxy_service.format_sse_event(
        cast(Any, {"type": "response.created", "response": {"id": "resp_owner_1"}})
    )
    terminal_event = cast(dict[str, Any], proxy_service.parse_sse_data_json(chunks[1]))
    assert terminal_event["type"] == "response.failed"
    assert terminal_event["response"]["id"] == "resp_owner_1"
    assert terminal_event["response"]["error"]["code"] == "bridge_owner_unreachable"
    assert terminal_event["response"]["error"]["message"] == "boom"


@pytest.mark.asyncio
async def test_forward_http_bridge_request_to_owner_emits_terminal_sse_after_forwarded_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
    )
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})

    async def fake_stream_responses(**kwargs: object):
        del kwargs
        yield proxy_service.format_sse_event(
            cast(Any, {"type": "response.created", "response": {"id": "resp_owner_2"}})
        )
        raise aiohttp.ClientError("connection reset")

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service,
        "_http_bridge_owner_client",
        cast(Any, SimpleNamespace(stream_responses=fake_stream_responses)),
    )

    chunks = [
        chunk
        async for chunk in service._forward_http_bridge_request_to_owner(
            owner_forward=owner_forward,
            payload=payload,
            headers={"x-codex-session-id": "sid-123"},
            api_key_reservation=None,
            codex_session_affinity=True,
            downstream_turn_state="http_turn_generated",
            request_started_at=10.0,
            proxy_api_authorization=None,
        )
    ]

    assert chunks[0] == proxy_service.format_sse_event(
        cast(Any, {"type": "response.created", "response": {"id": "resp_owner_2"}})
    )
    terminal_event = cast(dict[str, Any], proxy_service.parse_sse_data_json(chunks[1]))
    assert terminal_event["type"] == "response.failed"
    assert terminal_event["response"]["id"] == "resp_owner_2"
    assert terminal_event["response"]["error"]["code"] == "bridge_owner_unreachable"
    assert terminal_event["response"]["error"]["message"] == "HTTP bridge owner request failed"


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_rebind_after_forwarded_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
    )
    forward_calls = {"count": 0}

    async def fake_forward(**kwargs: object):
        del kwargs
        forward_calls["count"] += 1
        yield "data: first\n\n"
        raise ProxyResponseError(503, proxy_service.openai_error("bridge_owner_unreachable", "boom"))

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", AsyncMock(return_value=owner_forward))
    monkeypatch.setattr(service, "_forward_http_bridge_request_to_owner", fake_forward)

    seen: list[str] = []
    async for chunk in service._stream_via_http_bridge(
        payload,
        {"x-codex-session-id": "sid-123"},
        codex_session_affinity=True,
        openai_cache_affinity=False,
        api_key=None,
        api_key_reservation=None,
        propagate_http_errors=False,
        suppress_text_done_events=False,
        idle_ttl_seconds=120.0,
        codex_idle_ttl_seconds=900.0,
        max_sessions=8,
        queue_limit=4,
    ):
        seen.append(chunk)

    assert len(seen) == 2
    assert seen[0] == "data: first\n\n"
    terminal_payload = proxy_service.parse_sse_data_json(seen[1])
    assert isinstance(terminal_payload, dict)
    assert terminal_payload["type"] == "response.failed"
    terminal_response = terminal_payload["response"]
    assert isinstance(terminal_response, dict)
    terminal_error = terminal_response["error"]
    assert isinstance(terminal_error, dict)
    assert terminal_error["code"] == "bridge_owner_unreachable"
    assert terminal_error["message"] == "boom"
    assert forward_calls["count"] == 1
    service._get_or_create_http_bridge_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_forward_http_bridge_request_to_owner_masks_partial_previous_response_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
    )
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hi",
            "previous_response_id": "resp_partial_anchor",
        }
    )

    async def fake_stream_responses(**kwargs: object):
        del kwargs
        yield "data: first\n\n"
        error_payload = proxy_service.openai_error(
            "previous_response_not_found",
            "Previous response with id 'resp_partial_anchor' not found.",
            error_type="invalid_request_error",
        )
        error_payload["error"]["param"] = "previous_response_id"
        raise ProxyResponseError(400, error_payload)

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service,
        "_http_bridge_owner_client",
        cast(Any, SimpleNamespace(stream_responses=fake_stream_responses)),
    )

    chunks = [
        chunk
        async for chunk in service._forward_http_bridge_request_to_owner(
            owner_forward=owner_forward,
            payload=payload,
            headers={"x-codex-session-id": "sid-123"},
            api_key_reservation=None,
            codex_session_affinity=True,
            downstream_turn_state="http_turn_generated",
            request_started_at=10.0,
            proxy_api_authorization=None,
        )
    ]

    assert chunks[0] == "data: first\n\n"
    terminal_payload = proxy_service.parse_sse_data_json(chunks[1])
    assert isinstance(terminal_payload, dict)
    terminal_response = terminal_payload["response"]
    assert isinstance(terminal_response, dict)
    terminal_error = terminal_response["error"]
    assert isinstance(terminal_error, dict)
    assert terminal_payload["type"] == "response.failed"
    assert terminal_error["code"] == "stream_incomplete"
    assert terminal_error["message"] == "Upstream websocket closed before response.completed"
    assert "previous_response_not_found" not in chunks[1]
    assert "resp_partial_anchor" not in chunks[1]


@pytest.mark.asyncio
async def test_stream_via_http_bridge_fails_closed_on_forward_loop_prevented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
    )

    async def fake_forward(**kwargs: object):
        del kwargs
        raise ProxyResponseError(503, proxy_service.openai_error("bridge_forward_loop_prevented", "loop"))
        yield ""

    get_or_create = AsyncMock(return_value=owner_forward)
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_forward_http_bridge_request_to_owner", fake_forward)

    with pytest.raises(ProxyResponseError) as exc_info:
        async for _ in service._stream_via_http_bridge(
            payload,
            {"x-codex-session-id": "sid-123"},
            codex_session_affinity=True,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            propagate_http_errors=False,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=900.0,
            max_sessions=8,
            queue_limit=4,
        ):
            pass

    assert exc_info.value.payload["error"]["code"] == "bridge_forward_loop_prevented"
    get_or_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_via_http_bridge_reacquires_api_key_reservation_for_local_previous_response_rebind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    started_at = time.monotonic()
    api_key = _make_api_key(key_id="key-1", assigned_account_ids=[])
    initial_reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="resv-initial",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    retried_reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="resv-retry",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "prompt_cache_key": "bridge-prev-rebind",
            "previous_response_id": "resp_prev_1",
        }
    )

    request_state_initial = proxy_service._WebSocketRequestState(
        request_id="req-initial",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=initial_reservation,
        started_at=started_at,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_prev_1",
    )
    request_state_initial.request_stage = "follow_up"
    request_state_initial.preferred_account_id = "acc-1"
    request_state_retry = proxy_service._WebSocketRequestState(
        request_id="req-retry",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=retried_reservation,
        started_at=started_at,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_prev_1",
    )

    prepare_reservations: list[proxy_service.ApiKeyUsageReservationData | None] = []

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del prepared_payload, api_key, request_id
        prepare_reservations.append(api_key_reservation)
        if len(prepare_reservations) == 1:
            return request_state_initial, '{"type":"response.create","request":"initial"}'
        return request_state_retry, '{"type":"response.create","request":"retry"}'

    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-prev-rebind", api_key.id)
    session_initial = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-prev-rebind", kind=proxy_service.StickySessionKind.PROMPT_CACHE
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    session_retry = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-prev-rebind", kind=proxy_service.StickySessionKind.PROMPT_CACHE
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = session_initial

    stream_calls = {"count": 0}

    async def fake_stream_http_bridge_session_events(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        del request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        stream_calls["count"] += 1
        if stream_calls["count"] == 1:
            raise ProxyResponseError(400, proxy_service.openai_error("previous_response_not_found", "missing"))
        yield 'data: {"type":"response.completed"}\n\n'

    reserve_retry = AsyncMock(return_value=retried_reservation)
    capacity_unavailable = ProxyResponseError(
        503,
        proxy_service.openai_error("no_accounts", "Rate limit exceeded. Try again in 120s"),
    )
    get_or_create = AsyncMock(side_effect=[session_initial, capacity_unavailable, session_retry])

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_http_bridge_session_events)
    monkeypatch.setattr(service, "_close_http_bridge_session", AsyncMock())
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve_retry)
    monkeypatch.setattr(http_bridge_streaming_module, "_http_bridge_account_capacity_wait_seconds", lambda _exc: 0.001)
    monkeypatch.setattr(http_bridge_streaming_module, "_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS", 0.001)

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=api_key,
            api_key_reservation=initial_reservation,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=900.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    keepalive = proxy_service.parse_sse_data_json(chunks[0])
    assert keepalive is not None
    assert keepalive["status"] == "waiting_for_account_capacity"
    assert chunks[-1] == 'data: {"type":"response.completed"}\n\n'
    assert get_or_create.await_count == 3
    assert prepare_reservations == [initial_reservation, retried_reservation]
    reserve_retry.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_via_http_bridge_does_not_rebind_after_downstream_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "prompt_cache_key": "bridge-visible-rebind",
            "previous_response_id": "resp_prev_visible",
        }
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-visible-rebind",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_prev_visible",
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-visible-rebind", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-visible-rebind", kind=proxy_service.StickySessionKind.PROMPT_CACHE
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    stream_calls = 0
    get_or_create = AsyncMock(return_value=session)

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del prepared_payload, api_key, api_key_reservation, request_id
        return request_state, '{"type":"response.create"}'

    async def fake_stream_http_bridge_session_events(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        nonlocal stream_calls
        del _session, request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        stream_calls += 1
        yield 'data: {"type":"response.output_text.delta","delta":"already visible"}\n\n'
        raise ProxyResponseError(400, proxy_service.openai_error("previous_response_not_found", "missing"))

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", AsyncMock(return_value="acc-1"))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_http_bridge_session_events)

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=900.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert stream_calls == 1
    get_or_create.assert_awaited_once()
    assert chunks[0] == 'data: {"type":"response.output_text.delta","delta":"already visible"}\n\n'
    terminal = proxy_service.parse_sse_data_json(chunks[1])
    assert isinstance(terminal, dict)
    assert terminal["type"] == "response.failed"
    response = terminal["response"]
    assert isinstance(response, dict)
    error = response["error"]
    assert isinstance(error, dict)
    assert error["code"] == "stream_incomplete"


@pytest.mark.asyncio
async def test_http_bridge_local_owner_account_id_records_resolution_source(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))

    class _ObservedCounter:
        def __init__(self) -> None:
            self.samples: list[dict[str, object]] = []

        def labels(self, **labels: str):
            sample: dict[str, object] = {"labels": dict(labels), "value": 0.0}
            self.samples.append(sample)

            def inc(amount: float = 1.0) -> None:
                sample["value"] = cast(float, sample["value"]) + amount

            return SimpleNamespace(inc=inc)

    counter = _ObservedCounter()
    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_service, "continuity_owner_resolution_total", counter, raising=False)
    caplog.set_level(logging.INFO, logger="app.modules.proxy.service")

    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-prev-rebind", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-prev-rebind", kind=proxy_service.StickySessionKind.PROMPT_CACHE
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = session

    owner = await service._http_bridge_local_owner_account_id(
        key=key,
        incoming_turn_state=None,
        previous_response_id="resp_prev_local_owner_metric",
        api_key=None,
    )

    assert owner == "acc-1"
    assert "continuity_owner_resolution surface=http_bridge source=local_bridge_session outcome=hit" in caplog.text
    assert "resp_prev_local_owner_metric" not in caplog.text
    assert counter.samples == [
        {
            "labels": {"surface": "http_bridge", "source": "local_bridge_session", "outcome": "hit"},
            "value": 1.0,
        }
    ]


@pytest.mark.asyncio
async def test_stream_via_http_bridge_reacquires_api_key_reservation_after_owner_forward_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    started_at = time.monotonic()
    api_key = _make_api_key(key_id="key-1", assigned_account_ids=[])
    initial_reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="resv-initial",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    retried_reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="resv-retry",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "previous_response_id": "resp_prev_1",
        }
    )

    request_state_initial = proxy_service._WebSocketRequestState(
        request_id="req-initial",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=initial_reservation,
        started_at=started_at,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_prev_1",
    )
    request_state_initial.request_stage = "follow_up"
    request_state_initial.preferred_account_id = "acc-1"
    request_state_retry = proxy_service._WebSocketRequestState(
        request_id="req-retry",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=retried_reservation,
        started_at=started_at,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_prev_1",
    )

    prepare_reservations: list[proxy_service.ApiKeyUsageReservationData | None] = []

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del prepared_payload, api_key, request_id
        prepare_reservations.append(api_key_reservation)
        if len(prepare_reservations) == 1:
            return request_state_initial, '{"type":"response.create","request":"initial"}'
        return request_state_retry, '{"type":"response.create","request":"retry"}'

    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", api_key.id),
    )
    session_retry = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", api_key.id),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )

    submitted_reservations: list[proxy_service.ApiKeyUsageReservationData | None] = []

    async def fake_forward_http_bridge_request_to_owner(**kwargs: object):
        del kwargs
        raise ProxyResponseError(400, proxy_service.openai_error("previous_response_not_found", "missing"))
        yield ""

    async def fake_submit_http_bridge_request(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        del _session, text_data, queue_limit
        submitted_reservations.append(request_state.api_key_reservation)
        event_queue = request_state.event_queue
        assert event_queue is not None
        await event_queue.put('data: {"type":"response.completed"}\n\n')
        await event_queue.put(None)

    reserve_retry = AsyncMock(return_value=retried_reservation)
    capacity_unavailable = ProxyResponseError(
        503,
        proxy_service.openai_error("no_accounts", "Rate limit exceeded. Try again in 120s"),
    )
    get_or_create = AsyncMock(side_effect=[owner_forward, capacity_unavailable, session_retry])

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", AsyncMock(return_value="acc-1"))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_forward_http_bridge_request_to_owner", fake_forward_http_bridge_request_to_owner)
    monkeypatch.setattr(service, "_submit_http_bridge_request", fake_submit_http_bridge_request)
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve_retry)
    monkeypatch.setattr(http_bridge_streaming_module, "_http_bridge_account_capacity_wait_seconds", lambda _exc: 0.001)
    monkeypatch.setattr(http_bridge_streaming_module, "_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS", 0.001)

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-session-id": "sid-123"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=api_key,
            api_key_reservation=initial_reservation,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=900.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    keepalive = proxy_service.parse_sse_data_json(chunks[0])
    assert keepalive is not None
    assert keepalive["status"] == "waiting_for_account_capacity"
    assert chunks[-1] == 'data: {"type":"response.completed"}\n\n'
    assert get_or_create.await_count == 3
    assert prepare_reservations == [initial_reservation, retried_reservation]
    assert submitted_reservations == [retried_reservation]
    reserve_retry.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_via_http_bridge_local_previous_response_rebind_fails_existing_pending_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "prompt_cache_key": "bridge-prev-rebind",
            "previous_response_id": "resp_prev_1",
        }
    )

    request_state_initial = proxy_service._WebSocketRequestState(
        request_id="req-initial",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_prev_1",
    )
    request_state_initial.request_stage = "follow_up"
    request_state_initial.preferred_account_id = "acc-1"
    request_state_retry = proxy_service._WebSocketRequestState(
        request_id="req-retry",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=2.0,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_prev_1",
    )

    stale_pending_queue: asyncio.Queue[str | None] = asyncio.Queue()
    stale_pending_request = proxy_service._WebSocketRequestState(
        request_id="req-stale",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.5,
        event_queue=stale_pending_queue,
        transport="http",
    )
    stale_pending_request.skip_request_log = True

    prepare_calls = {"count": 0}

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del prepared_payload, api_key, api_key_reservation, request_id
        prepare_calls["count"] += 1
        if prepare_calls["count"] == 1:
            return request_state_initial, '{"type":"response.create","request":"initial"}'
        return request_state_retry, '{"type":"response.create","request":"retry"}'

    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-prev-rebind", None)
    session_initial = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-prev-rebind", kind=proxy_service.StickySessionKind.PROMPT_CACHE
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([stale_pending_request]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    session_retry = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-prev-rebind", kind=proxy_service.StickySessionKind.PROMPT_CACHE
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = session_initial

    stream_calls = {"count": 0}

    async def fake_stream_http_bridge_session_events(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        del request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        stream_calls["count"] += 1
        if stream_calls["count"] == 1:
            raise ProxyResponseError(400, proxy_service.openai_error("previous_response_not_found", "missing"))
        yield 'data: {"type":"response.completed"}\n\n'

    get_or_create = AsyncMock(side_effect=[session_initial, session_retry])

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_http_bridge_session_events)
    monkeypatch.setattr(service, "_close_http_bridge_session", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=900.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    failed_block = await asyncio.wait_for(stale_pending_queue.get(), timeout=0.2)
    done_marker = await asyncio.wait_for(stale_pending_queue.get(), timeout=0.2)

    assert chunks == ['data: {"type":"response.completed"}\n\n']
    assert isinstance(failed_block, str)
    assert '"type":"response.failed"' in failed_block
    assert '"code":"stream_incomplete"' in failed_block
    assert done_marker is None
    assert not session_initial.pending_requests
    assert session_initial.queued_request_count == 0


@pytest.mark.asyncio
async def test_stream_via_http_bridge_rolls_over_session_after_context_length_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "prompt_cache_key": "bridge-context-overflow",
        }
    )

    request_state = proxy_service._WebSocketRequestState(
        request_id="req-context-overflow",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )
    stale_pending_queue: asyncio.Queue[str | None] = asyncio.Queue()
    stale_pending_request = proxy_service._WebSocketRequestState(
        request_id="req-stale",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.5,
        event_queue=stale_pending_queue,
        transport="http",
    )
    stale_pending_request.skip_request_log = True

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del prepared_payload, api_key, api_key_reservation, request_id
        return request_state, '{"type":"response.create","request":"initial"}'

    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-context-overflow", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-context-overflow", kind=proxy_service.StickySessionKind.PROMPT_CACHE
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([stale_pending_request]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = session

    async def fake_stream_http_bridge_session_events(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        del request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        raise ProxyResponseError(
            400,
            proxy_service.openai_error(
                "context_length_exceeded",
                "Your input exceeds the context window of this model.",
                error_type="invalid_request_error",
            ),
        )
        yield

    close_session = AsyncMock()
    get_or_create = AsyncMock(return_value=session)

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_http_bridge_session_events)
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)

    with pytest.raises(ProxyResponseError) as exc_info:
        async for _ in service._stream_via_http_bridge(
            payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=True,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=900.0,
            max_sessions=8,
            queue_limit=4,
        ):
            pass

    failed_block = await asyncio.wait_for(stale_pending_queue.get(), timeout=0.2)
    done_marker = await asyncio.wait_for(stale_pending_queue.get(), timeout=0.2)

    assert exc_info.value.status_code == 400
    assert exc_info.value.payload["error"]["code"] == "context_length_exceeded"
    assert key not in service._http_bridge_sessions
    close_session.assert_awaited_once_with(session)
    assert isinstance(failed_block, str)
    assert '"type":"response.failed"' in failed_block
    assert '"code":"stream_incomplete"' in failed_block
    assert done_marker is None
    assert not session.pending_requests
    assert session.queued_request_count == 0


@pytest.mark.asyncio
async def test_stream_via_http_bridge_context_overflow_keeps_hard_affinity_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
        }
    )

    request_state = proxy_service._WebSocketRequestState(
        request_id="req-context-overflow-hard",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del prepared_payload, api_key, api_key_reservation, request_id
        return request_state, '{"type":"response.create","request":"initial"}'

    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "turn_hard_overflow", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-turn-state": "turn_hard_overflow"},
        affinity=proxy_service._AffinityPolicy(
            key="turn_hard_overflow", kind=proxy_service.StickySessionKind.CODEX_SESSION
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = session

    async def fake_stream_http_bridge_session_events(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        del request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        raise ProxyResponseError(
            400,
            proxy_service.openai_error(
                "context_length_exceeded",
                "Your input exceeds the context window of this model.",
                error_type="invalid_request_error",
            ),
        )
        yield

    close_session = AsyncMock()
    get_or_create = AsyncMock(return_value=session)

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_http_bridge_session_events)
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)

    with pytest.raises(ProxyResponseError) as exc_info:
        async for _ in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-turn-state": "turn_hard_overflow"},
            codex_session_affinity=True,
            propagate_http_errors=True,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=900.0,
            max_sessions=8,
            queue_limit=4,
        ):
            pass

    assert exc_info.value.status_code == 400
    assert exc_info.value.payload["error"]["code"] == "context_length_exceeded"
    assert key in service._http_bridge_sessions
    close_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_via_http_bridge_context_overflow_does_not_retry_hard_affinity_with_previous_response_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hello",
            "previous_response_id": "resp_prev_123",
        }
    )

    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "turn_hard_overflow_recover", None)
    initial_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-turn-state": "turn_hard_overflow_recover"},
        affinity=proxy_service._AffinityPolicy(
            key="turn_hard_overflow_recover",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = initial_session

    prepare_previous_response_ids: list[str | None] = []

    def fake_prepare(
        prepared_payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation
        prepare_previous_response_ids.append(prepared_payload.previous_response_id)
        request_state = proxy_service._WebSocketRequestState(
            request_id=request_id,
            model=prepared_payload.model,
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=1.0,
            event_queue=asyncio.Queue(),
            transport="http",
            previous_response_id=prepared_payload.previous_response_id,
            session_id="turn_hard_overflow_recover",
        )
        return request_state, '{"type":"response.create"}'

    stream_attempt = 0

    async def fake_stream_http_bridge_session_events(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        nonlocal stream_attempt
        del request_state, text_data, queue_limit, propagate_http_errors, downstream_turn_state
        stream_attempt += 1
        if stream_attempt == 1:
            raise ProxyResponseError(
                400,
                proxy_service.openai_error(
                    "context_length_exceeded",
                    "Your input exceeds the context window of this model.",
                    error_type="invalid_request_error",
                ),
            )
        yield 'data: {"type":"response.completed"}\n\n'

    close_session = AsyncMock()
    get_or_create = AsyncMock(return_value=initial_session)

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_http_bridge_session_events)
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)

    with pytest.raises(ProxyResponseError) as exc_info:
        async for _ in service._stream_via_http_bridge(
            payload,
            headers={"x-codex-turn-state": "turn_hard_overflow_recover"},
            codex_session_affinity=True,
            propagate_http_errors=True,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=900.0,
            max_sessions=8,
            queue_limit=4,
            downstream_turn_state="turn_hard_overflow_recover",
        ):
            pass

    assert exc_info.value.status_code == 400
    assert exc_info.value.payload["error"]["code"] == "context_length_exceeded"
    assert prepare_previous_response_ids == ["resp_prev_123"]
    assert stream_attempt == 1
    close_session.assert_not_awaited()
    assert len(get_or_create.await_args_list) == 1


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_returns_owner_forward_for_hard_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_123", None)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )
    service._ring_membership = cast(Any, SimpleNamespace(resolve_endpoint=AsyncMock(return_value="http://instance-b")))

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "http_turn_123"},
        affinity=proxy_service._AffinityPolicy(key="http_turn_123"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
    )

    assert isinstance(resolved, proxy_service._HTTPBridgeOwnerForward)
    assert resolved.owner_instance == "instance-b"
    assert resolved.owner_endpoint == "http://instance-b"
    assert resolved.key == key


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_preserves_explicit_forwarded_affinity_on_missing_turn_state_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    captured: dict[str, object] = {}

    async def fake_create_http_bridge_session(
        create_key: proxy_service._HTTPBridgeSessionKey,
        *,
        headers: dict[str, str],
        affinity: proxy_service._AffinityPolicy,
        api_key: proxy_service.ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        require_preferred_account: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
    ) -> proxy_service._HTTPBridgeSession:
        del (
            headers,
            affinity,
            api_key,
            request_model,
            idle_ttl_seconds,
            request_stage,
            preferred_account_id,
            require_preferred_account,
            fallback_on_preferred_account_unavailable,
        )
        captured["key"] = create_key
        return created_session

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", fake_create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "http_turn_generated"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        forwarded_request=True,
        forwarded_affinity_kind="session_header",
        forwarded_affinity_key="sid-123",
    )

    assert resolved is created_session
    assert captured["key"] == key


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_falls_back_to_session_header_when_turn_state_alias_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    requested_key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_generated", None)
    fallback_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=fallback_key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    captured: dict[str, object] = {}

    async def fake_create_http_bridge_session(
        create_key: proxy_service._HTTPBridgeSessionKey,
        *,
        headers: dict[str, str],
        affinity: proxy_service._AffinityPolicy,
        api_key: proxy_service.ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        require_preferred_account: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
    ) -> proxy_service._HTTPBridgeSession:
        del (
            headers,
            affinity,
            api_key,
            request_model,
            idle_ttl_seconds,
            request_stage,
            preferred_account_id,
            require_preferred_account,
            fallback_on_preferred_account_unavailable,
        )
        captured["key"] = create_key
        return created_session

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", fake_create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        requested_key,
        headers={
            "x-codex-turn-state": "http_turn_generated",
            "x-codex-session-id": "sid-123",
        },
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
    )

    assert resolved is created_session
    assert captured["key"] == fallback_key


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_preserves_durable_canonical_prompt_cache_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    requested_key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "pc-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=requested_key,
        headers={"x-codex-turn-state": "http_turn_generated"},
        affinity=proxy_service._AffinityPolicy(
            key="pc-123",
            kind=proxy_service.StickySessionKind.PROMPT_CACHE,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    captured: dict[str, object] = {}

    async def fake_create_http_bridge_session(
        create_key: proxy_service._HTTPBridgeSessionKey,
        *,
        headers: dict[str, str],
        affinity: proxy_service._AffinityPolicy,
        api_key: proxy_service.ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        require_preferred_account: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
    ) -> proxy_service._HTTPBridgeSession:
        del (
            headers,
            affinity,
            api_key,
            request_model,
            idle_ttl_seconds,
            request_stage,
            preferred_account_id,
            require_preferred_account,
            fallback_on_preferred_account_unavailable,
        )
        captured["key"] = create_key
        return created_session

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", fake_create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        requested_key,
        headers={"x-codex-turn-state": "http_turn_generated"},
        affinity=proxy_service._AffinityPolicy(
            key="pc-123",
            kind=proxy_service.StickySessionKind.PROMPT_CACHE,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
        durable_lookup=proxy_service.DurableBridgeLookup(
            session_id="durable-1",
            canonical_kind="prompt_cache",
            canonical_key="pc-123",
            api_key_scope="__anonymous__",
            account_id="acc-1",
            owner_instance_id="instance-a",
            owner_epoch=2,
            lease_expires_at=proxy_service.utcnow() + timedelta(seconds=60),
            state=HttpBridgeSessionState.ACTIVE,
            latest_turn_state="http_turn_generated",
            latest_response_id="resp_prev_1",
        ),
    )

    assert resolved is created_session
    assert captured["key"] == requested_key


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_recovers_from_previous_response_id_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_missing_alias", None)
    recovered_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    recovered_session = proxy_service._HTTPBridgeSession(
        key=recovered_key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
        previous_response_ids={"resp_prev_1"},
    )
    service._http_bridge_sessions[recovered_key] = recovered_session
    service._http_bridge_previous_response_index[
        proxy_service._http_bridge_previous_response_alias_key("resp_prev_1", None)
    ] = recovered_key
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "http_turn_missing_alias"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
    )

    assert resolved is recovered_session
    assert "http_turn_missing_alias" in recovered_session.downstream_turn_state_aliases


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_closes_stale_session_before_previous_response_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "stale-sid", "key-1")
    stale_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "stale-sid"},
        affinity=proxy_service._AffinityPolicy(
            key="stale-sid",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-stale", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    recovered_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", "key-1")
    recovered_session = proxy_service._HTTPBridgeSession(
        key=recovered_key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
        previous_response_ids={"resp_prev_1"},
    )
    service._http_bridge_sessions[key] = stale_session
    service._http_bridge_sessions[recovered_key] = recovered_session
    service._http_bridge_previous_response_index[
        proxy_service._http_bridge_previous_response_alias_key("resp_prev_1", "key-1")
    ] = recovered_key
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    close_session = AsyncMock(wraps=service._close_http_bridge_session)
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-session-id": "stale-sid"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=_make_api_key(
            key_id="key-1",
            assigned_account_ids=[],
            account_assignment_scope_enabled=True,
        ),
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
    )

    assert resolved is recovered_session
    assert stale_session.closed is True
    for _ in range(10):
        if any(call.args == (stale_session,) for call in close_session.await_args_list):
            break
        await asyncio.sleep(0)
    assert any(call.args == (stale_session,) for call in close_session.await_args_list)


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_drops_stale_previous_response_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_missing_alias", None)
    stale_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-stale", None)
    created_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-new", None)
    stale_session = proxy_service._HTTPBridgeSession(
        key=stale_key,
        headers={"x-codex-session-id": "sid-stale"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-stale",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
        closed=True,
        previous_response_ids={"resp_prev_1"},
    )
    created_session = proxy_service._HTTPBridgeSession(
        key=created_key,
        headers={"x-codex-session-id": "sid-new"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-new",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-2", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=3.0,
        idle_ttl_seconds=120.0,
    )
    alias_key = proxy_service._http_bridge_previous_response_alias_key("resp_prev_1", None)
    service._http_bridge_sessions[stale_key] = stale_session
    service._http_bridge_previous_response_index[alias_key] = stale_key
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    create_http_bridge_session = AsyncMock(return_value=created_session)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={
            "x-codex-turn-state": "http_turn_missing_alias",
            "x-codex-session-id": "sid-new",
        },
        affinity=proxy_service._AffinityPolicy(
            key="sid-new",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
    )

    assert resolved is created_session
    assert alias_key not in service._http_bridge_previous_response_index


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_allows_local_rebind_for_previous_response_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    create_http_bridge_session = AsyncMock(return_value=created_session)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
        allow_previous_response_recovery_rebind=True,
    )

    assert resolved is created_session


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_allows_local_rebind_for_bootstrap_owner_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    create_http_bridge_session = AsyncMock(return_value=created_session)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_bootstrap_owner_rebind=True,
    )

    assert resolved is created_session


@pytest.mark.asyncio
async def test_should_attempt_local_bootstrap_rebind_for_session_header_without_turn_state() -> None:
    exc = ProxyResponseError(
        503,
        {"error": {"code": "bridge_owner_unreachable", "message": "owner down", "type": "server_error"}},
    )

    assert (
        proxy_service._http_bridge_should_attempt_local_bootstrap_rebind(
            exc,
            key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
            headers={"x-codex-session-id": "sid-123"},
            previous_response_id=None,
        )
        is True
    )

    assert (
        proxy_service._http_bridge_should_attempt_local_bootstrap_rebind(
            exc,
            key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
            headers={"x-codex-session-id": "sid-123", "x-codex-turn-state": "http_turn_123"},
            previous_response_id=None,
        )
        is False
    )


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_recovers_locally_when_owner_endpoint_missing_without_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-turn-state": "http_turn_123"},
        affinity=proxy_service._AffinityPolicy(key="http_turn_123"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    create_http_bridge_session = AsyncMock(return_value=created_session)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    claim_durable = AsyncMock()
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", claim_durable)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )
    service._ring_membership = cast(Any, SimpleNamespace(resolve_endpoint=AsyncMock(return_value=None)))

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "http_turn_123"},
        affinity=proxy_service._AffinityPolicy(key="http_turn_123"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
    )

    assert resolved is created_session
    claim_durable.assert_awaited_once()
    await_args = claim_durable.await_args
    assert await_args is not None
    assert await_args.kwargs["allow_takeover"] is True
    service._ring_membership.resolve_endpoint.assert_awaited_once_with("instance-b")


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_recovers_locally_when_stale_owner_endpoint_is_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(key="sid-123", kind=proxy_service.StickySessionKind.CODEX_SESSION),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    claim_durable = AsyncMock()
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", claim_durable)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: Settings(
            http_responses_session_bridge_instance_id="instance-a",
            http_responses_session_bridge_advertise_base_url="http://127.0.0.1:2455",
        ),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-old"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-old"])),
    )
    forward_to_owner = AsyncMock()
    monkeypatch.setattr(service, "_forward_http_bridge_request_to_owner", forward_to_owner)
    service._ring_membership = cast(
        Any,
        SimpleNamespace(resolve_endpoint=AsyncMock(return_value="http://127.0.0.1:2455/")),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(key="sid-123", kind=proxy_service.StickySessionKind.CODEX_SESSION),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
    )

    assert resolved is created_session
    claim_durable.assert_awaited_once()
    await_args = claim_durable.await_args
    assert await_args is not None
    assert await_args.kwargs["allow_takeover"] is True
    forward_to_owner.assert_not_awaited()
    service._ring_membership.resolve_endpoint.assert_awaited_once_with("instance-old")


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_recovers_locally_when_owner_endpoint_missing_but_replay_anchor_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-turn-state": "http_turn_123"},
        affinity=proxy_service._AffinityPolicy(key="http_turn_123"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    claim_durable = AsyncMock()
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", claim_durable)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )
    service._ring_membership = cast(Any, SimpleNamespace(resolve_endpoint=AsyncMock(return_value=None)))

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "http_turn_123"},
        affinity=proxy_service._AffinityPolicy(key="http_turn_123"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
        allow_forward_to_owner=True,
        durable_lookup=proxy_service.DurableBridgeLookup(
            session_id="durable-1",
            canonical_kind="turn_state_header",
            canonical_key="http_turn_123",
            api_key_scope="__anonymous__",
            account_id="acc-1",
            owner_instance_id="instance-b",
            owner_epoch=2,
            lease_expires_at=proxy_service.utcnow() + timedelta(seconds=60),
            state=HttpBridgeSessionState.ACTIVE,
            latest_turn_state="http_turn_123",
            latest_response_id="resp_prev_1",
        ),
    )

    assert resolved is created_session
    claim_durable.assert_awaited_once()
    await_args = claim_durable.await_args
    assert await_args is not None
    assert await_args.kwargs["allow_takeover"] is True


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_recovers_locally_without_anchor_for_single_instance_stale_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "turn_123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-turn-state": "turn_123"},
        affinity=proxy_service._AffinityPolicy(key="turn_123"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    claim_durable = AsyncMock()
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", claim_durable)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    setattr(service, "_ring_membership", None)
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "turn_123"},
        affinity=proxy_service._AffinityPolicy(key="turn_123"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
        durable_lookup=proxy_service.DurableBridgeLookup(
            session_id="durable-1",
            canonical_kind="turn_state_header",
            canonical_key="turn_123",
            api_key_scope="__anonymous__",
            account_id="acc-1",
            owner_instance_id="instance-stale",
            owner_epoch=2,
            lease_expires_at=proxy_service.utcnow() + timedelta(seconds=60),
            state=HttpBridgeSessionState.ACTIVE,
            latest_turn_state="turn_123",
            latest_response_id=None,
        ),
    )

    assert resolved is created_session
    claim_durable.assert_awaited_once()
    await_args = claim_durable.await_args
    assert await_args is not None
    assert await_args.kwargs["allow_takeover"] is True


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_prompt_cache_takes_over_stale_single_instance_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache-key", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    claim_durable = AsyncMock()
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", claim_durable)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    setattr(service, "_ring_membership", None)
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
        durable_lookup=proxy_service.DurableBridgeLookup(
            session_id="durable-1",
            canonical_kind="prompt_cache",
            canonical_key="cache-key",
            api_key_scope="__anonymous__",
            account_id="acc-1",
            owner_instance_id="instance-stale",
            owner_epoch=2,
            lease_expires_at=proxy_service.utcnow() + timedelta(seconds=60),
            state=HttpBridgeSessionState.ACTIVE,
            latest_turn_state="http_turn_prompt_cache",
            latest_response_id=None,
        ),
    )

    assert resolved is created_session
    claim_durable.assert_awaited_once()
    await_args = claim_durable.await_args
    assert await_args is not None
    assert await_args.kwargs["allow_takeover"] is True


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_discards_local_session_when_durable_owner_is_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    existing_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-stale", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-new", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=3.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = existing_session
    close_session = AsyncMock()
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )
    service._ring_membership = cast(Any, SimpleNamespace(resolve_endpoint=AsyncMock(return_value=None)))

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
        allow_forward_to_owner=True,
        durable_lookup=proxy_service.DurableBridgeLookup(
            session_id="durable-1",
            canonical_kind="session_header",
            canonical_key="sid-123",
            api_key_scope="__anonymous__",
            account_id="acc-1",
            owner_instance_id="instance-b",
            owner_epoch=2,
            lease_expires_at=proxy_service.utcnow() + timedelta(seconds=60),
            state=HttpBridgeSessionState.ACTIVE,
            latest_turn_state="http_turn_123",
            latest_response_id="resp_prev_1",
        ),
    )

    assert resolved is created_session
    await _wait_for_close_await(close_session, existing_session)


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_does_not_publish_before_durable_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-race", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-race"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-race",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    close_session = AsyncMock()

    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    monkeypatch.setattr(
        service,
        "_claim_durable_http_bridge_session",
        AsyncMock(side_effect=RuntimeError("db unavailable")),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a"])),
    )

    async def _call() -> proxy_service._HTTPBridgeSession:
        return await service._get_or_create_http_bridge_session(
            key,
            headers={"x-codex-session-id": "sid-race"},
            affinity=proxy_service._AffinityPolicy(
                key="sid-race",
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )

    first = asyncio.create_task(_call())
    await asyncio.sleep(0)
    second = asyncio.create_task(_call())

    with pytest.raises(RuntimeError, match="db unavailable"):
        await first
    with pytest.raises(RuntimeError, match="db unavailable"):
        await second

    assert key not in service._http_bridge_sessions
    assert close_session.await_count >= 1
    assert all(call.args == (created_session,) for call in close_session.await_args_list)


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_waiter_propagates_terminal_inflight_proxy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-race", None)
    inflight_future: asyncio.Future[proxy_service._HTTPBridgeSession] = asyncio.get_running_loop().create_future()
    inflight_future.set_exception(
        ProxyResponseError(
            409,
            proxy_service.openai_error(
                "bridge_instance_mismatch",
                "HTTP bridge session is owned by a different instance; retry to reach the correct replica",
                error_type="server_error",
            ),
        )
    )
    service._http_bridge_inflight_sessions[key] = inflight_future

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await asyncio.wait_for(
            service._get_or_create_http_bridge_session(
                key,
                headers={"x-codex-session-id": "sid-race"},
                affinity=proxy_service._AffinityPolicy(
                    key="sid-race",
                    kind=proxy_service.StickySessionKind.CODEX_SESSION,
                ),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            ),
            timeout=0.1,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.payload["error"]["code"] == "bridge_instance_mismatch"


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_inflight_wait_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-stuck-inflight", None)
    inflight_future: asyncio.Future[proxy_service._HTTPBridgeSession] = asyncio.get_running_loop().create_future()
    service._http_bridge_inflight_sessions[key] = inflight_future
    settings = _make_app_settings()
    settings.proxy_admission_wait_timeout_seconds = 0.01

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await asyncio.wait_for(
            service._get_or_create_http_bridge_session(
                key,
                headers={"x-codex-session-id": "sid-stuck-inflight"},
                affinity=proxy_service._AffinityPolicy(
                    key="sid-stuck-inflight",
                    kind=proxy_service.StickySessionKind.CODEX_SESSION,
                ),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            ),
            timeout=1.0,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.payload["error"]["code"] == "capacity_exhausted_active_sessions"
    assert key not in service._http_bridge_inflight_sessions
    with pytest.raises(ProxyResponseError) as future_exc_info:
        await inflight_future
    assert future_exc_info.value.status_code == 429
    assert future_exc_info.value.payload["error"]["code"] == "capacity_exhausted_active_sessions"


@pytest.mark.asyncio
async def test_close_all_http_bridge_sessions_fails_inflight_waiters() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-shutdown", None)
    inflight_future: asyncio.Future[proxy_service._HTTPBridgeSession] = asyncio.get_running_loop().create_future()
    service._http_bridge_inflight_sessions[key] = inflight_future

    await service.close_all_http_bridge_sessions()

    with pytest.raises(ProxyResponseError) as exc_info:
        await inflight_future

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_close_all_http_bridge_sessions_fails_capacity_waiters_instead_of_creating_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    existing_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-existing", None)
    existing = proxy_service._HTTPBridgeSession(
        key=existing_key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="sid-capacity-existing",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-existing", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=cast(deque[proxy_service._WebSocketRequestState], deque()),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        codex_session=True,
        prewarm_lock=anyio.Lock(),
    )
    service._http_bridge_sessions[existing_key] = existing
    inflight_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-inflight", None)
    inflight_future: asyncio.Future[proxy_service._HTTPBridgeSession] = asyncio.get_running_loop().create_future()
    service._http_bridge_inflight_sessions[inflight_key] = inflight_future

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_http_bridge_pending_count", AsyncMock(return_value=1))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )
    create_http_bridge_session = AsyncMock()
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(service, "_close_http_bridge_session", AsyncMock())

    waiter = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-request", None),
            headers={"x-codex-session-id": "sid-capacity-request"},
            affinity=proxy_service._AffinityPolicy(
                key="sid-capacity-request",
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=1,
        )
    )
    await asyncio.sleep(0)

    await service.close_all_http_bridge_sessions()

    with pytest.raises(ProxyResponseError) as exc_info:
        await asyncio.wait_for(waiter, timeout=0.1)

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    create_http_bridge_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_capacity_wait_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    existing_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-existing", None)
    existing = proxy_service._HTTPBridgeSession(
        key=existing_key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="sid-capacity-existing",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-existing", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=cast(deque[proxy_service._WebSocketRequestState], deque()),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        codex_session=True,
        prewarm_lock=anyio.Lock(),
    )
    service._http_bridge_sessions[existing_key] = existing
    inflight_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-inflight", None)
    inflight_future: asyncio.Future[proxy_service._HTTPBridgeSession] = asyncio.get_running_loop().create_future()
    service._http_bridge_inflight_sessions[inflight_key] = inflight_future
    settings = _make_app_settings()
    settings.proxy_admission_wait_timeout_seconds = 0.01

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_http_bridge_pending_count", AsyncMock(return_value=1))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )
    create_http_bridge_session = AsyncMock()
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(service, "_close_http_bridge_session", AsyncMock())

    with pytest.raises(ProxyResponseError) as exc_info:
        await asyncio.wait_for(
            service._get_or_create_http_bridge_session(
                proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-request", None),
                headers={"x-codex-session-id": "sid-capacity-request"},
                affinity=proxy_service._AffinityPolicy(
                    key="sid-capacity-request",
                    kind=proxy_service.StickySessionKind.CODEX_SESSION,
                ),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=1,
            ),
            timeout=1.0,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.payload["error"]["code"] == "capacity_exhausted_active_sessions"
    assert inflight_key not in service._http_bridge_inflight_sessions
    with pytest.raises(ProxyResponseError) as future_exc_info:
        await inflight_future
    assert future_exc_info.value.status_code == 429
    assert future_exc_info.value.payload["error"]["code"] == "capacity_exhausted_active_sessions"
    create_http_bridge_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_immediate_capacity_exhaustion_is_local_overload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    existing_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-full", None)
    existing = _make_bridge_session(key_value="sid-capacity-full", queued_request_count=1)
    service._http_bridge_sessions[existing_key] = existing

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_http_bridge_pending_count", AsyncMock(return_value=1))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-new", None),
            headers={"x-codex-session-id": "sid-capacity-new"},
            affinity=proxy_service._AffinityPolicy(
                key="sid-capacity-new",
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=1,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.payload["error"]["code"] == "capacity_exhausted_active_sessions"


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_closes_lru_before_replacement_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    existing_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-lru-existing", None)
    existing = _make_bridge_session(key=existing_key, key_value="sid-lru-existing")
    service._http_bridge_sessions[existing_key] = existing
    new_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-lru-new", None)
    events: list[str] = []

    async def close_http_bridge_session_bounded(
        session: proxy_service._HTTPBridgeSession,
        *,
        reason: str,
    ) -> None:
        assert session is existing
        assert reason == "registry_detach"
        events.append("close")
        session.closed = True

    async def create_http_bridge_session(
        key: proxy_service._HTTPBridgeSessionKey,
        **_: object,
    ) -> proxy_service._HTTPBridgeSession:
        assert events == ["close"]
        events.append("create")
        return _make_bridge_session(key=key, key_value=key.affinity_key)

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_close_http_bridge_session_bounded", close_http_bridge_session_bounded)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )

    result = await service._get_or_create_http_bridge_session(
        new_key,
        headers={"x-codex-session-id": "sid-lru-new"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-lru-new",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=1,
    )

    assert events == ["close", "create"]
    assert result.key == new_key
    assert existing_key not in service._http_bridge_sessions
    assert service._http_bridge_sessions[new_key] is result


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_cancel_during_lru_close_cleans_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    existing_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-lru-cancel-existing", None)
    existing = _make_bridge_session(key=existing_key, key_value="sid-lru-cancel-existing")
    service._http_bridge_sessions[existing_key] = existing
    new_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-lru-cancel-new", None)
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close_http_bridge_session_bounded(
        session: proxy_service._HTTPBridgeSession,
        *,
        reason: str,
    ) -> None:
        assert session is existing
        assert reason == "registry_detach"
        close_started.set()
        await release_close.wait()

    create_http_bridge_session = AsyncMock()

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_close_http_bridge_session_bounded", close_http_bridge_session_bounded)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )

    task = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            new_key,
            headers={"x-codex-session-id": "sid-lru-cancel-new"},
            affinity=proxy_service._AffinityPolicy(
                key="sid-lru-cancel-new",
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=1,
        )
    )
    await asyncio.wait_for(close_started.wait(), timeout=1.0)
    assert new_key in service._http_bridge_inflight_sessions

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release_close.set()

    assert new_key not in service._http_bridge_inflight_sessions
    assert existing_key not in service._http_bridge_sessions
    create_http_bridge_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_capacity_scan_does_not_wait_on_wedged_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    existing_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-wedged", None)
    existing = _make_bridge_session(key=existing_key, key_value="sid-capacity-wedged")
    service._http_bridge_sessions[existing_key] = existing
    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()

    async def hold_pending_lock() -> None:
        async with existing.pending_lock:
            lock_acquired.set()
            await release_lock.wait()

    lock_holder = asyncio.create_task(hold_pending_lock())
    await asyncio.wait_for(lock_acquired.wait(), timeout=1.0)

    create_http_bridge_session = AsyncMock()
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )

    try:
        with pytest.raises(ProxyResponseError) as exc_info:
            await asyncio.wait_for(
                service._get_or_create_http_bridge_session(
                    proxy_service._HTTPBridgeSessionKey("session_header", "sid-capacity-new", None),
                    headers={"x-codex-session-id": "sid-capacity-new"},
                    affinity=proxy_service._AffinityPolicy(
                        key="sid-capacity-new",
                        kind=proxy_service.StickySessionKind.CODEX_SESSION,
                    ),
                    api_key=None,
                    request_model="gpt-5.4",
                    idle_ttl_seconds=120.0,
                    max_sessions=1,
                ),
                timeout=1.0,
            )
    finally:
        release_lock.set()
        await asyncio.wait_for(lock_holder, timeout=1.0)

    assert exc_info.value.status_code == 429
    assert exc_info.value.payload["error"]["code"] == "capacity_exhausted_active_sessions"
    create_http_bridge_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_http_bridge_session_cancellation_before_connect_handoff_releases_stream_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    account = cast(
        Any,
        SimpleNamespace(
            id="acc-bridge-cancel-handoff",
            chatgpt_account_id="acc-bridge-cancel-handoff",
            status=AccountStatus.ACTIVE,
            access_token_encrypted="encrypted",
        ),
    )
    selected_lease = await service._load_balancer.acquire_account_lease(account.id, kind="stream")
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_ensure_fresh(*_: object, **__: object) -> Any:
        started.set()
        await release.wait()
        return account

    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(return_value=proxy_service.AccountSelection(account, None, lease=selected_lease)),
    )
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", blocking_ensure_fresh)
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        prefer_earlier_reset_accounts=False,
                        routing_strategy="usage_weighted",
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())

    task = asyncio.create_task(
        service._create_http_bridge_session(
            proxy_service._HTTPBridgeSessionKey("session_header", "sid-bridge-cancel", None),
            headers={"x-codex-session-id": "sid-bridge-cancel"},
            affinity=proxy_service._AffinityPolicy(
                key="sid-bridge-cancel",
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert await service._load_balancer.account_pressure_snapshot(account.id) == (0, 1, 0.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()

    assert await service._load_balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_cancel_during_stale_close_cleans_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-stale-close-cancel", None)
    stale = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="sid-stale-close-cancel",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-stale", status=AccountStatus.DEACTIVATED)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=cast(deque[proxy_service._WebSocketRequestState], deque()),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        codex_session=True,
        prewarm_lock=anyio.Lock(),
    )
    service._http_bridge_sessions[key] = stale
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close_stale_session(session: proxy_service._HTTPBridgeSession, **_: object) -> None:
        assert session is stale
        close_started.set()
        await release_close.wait()

    monkeypatch.setattr(service, "_close_http_bridge_session", close_stale_session)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )
    replacement = _make_bridge_session(key=key, key_value="sid-stale-close-cancel")
    create_http_bridge_session = AsyncMock(return_value=replacement)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())

    task = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            key,
            headers={"x-codex-session-id": "sid-stale-close-cancel"},
            affinity=proxy_service._AffinityPolicy(
                key="sid-stale-close-cancel",
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )
    )
    await asyncio.wait_for(close_started.wait(), timeout=1.0)
    resolved = await asyncio.wait_for(task, timeout=1.0)
    release_close.set()
    await asyncio.sleep(0)

    assert resolved is replacement
    assert key not in service._http_bridge_inflight_sessions
    create_http_bridge_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_late_owner_after_inflight_evict_closes_unregistered_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-late-owner", None)
    settings = _make_app_settings()
    settings.proxy_admission_wait_timeout_seconds = 0.01
    created = _make_bridge_session(key_value="sid-late-owner")
    create_started = asyncio.Event()
    finish_create = asyncio.Event()

    async def create_session(*_: object, **__: object) -> proxy_service._HTTPBridgeSession:
        create_started.set()
        await finish_create.wait()
        return created

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", create_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    close_http_bridge_session = AsyncMock()
    monkeypatch.setattr(service, "_close_http_bridge_session", close_http_bridge_session)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ("instance-a",))),
    )

    async def get_session() -> proxy_service._HTTPBridgeSession | proxy_service._HTTPBridgeOwnerForward:
        return await service._get_or_create_http_bridge_session(
            key,
            headers={"x-codex-session-id": "sid-late-owner"},
            affinity=proxy_service._AffinityPolicy(
                key="sid-late-owner",
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )

    owner_task = asyncio.create_task(get_session())
    await asyncio.wait_for(create_started.wait(), timeout=1.0)
    assert key in service._http_bridge_inflight_sessions

    with pytest.raises(ProxyResponseError) as waiter_exc_info:
        await asyncio.wait_for(get_session(), timeout=1.0)

    assert waiter_exc_info.value.status_code == 429
    assert waiter_exc_info.value.payload["error"]["code"] == "capacity_exhausted_active_sessions"
    assert key not in service._http_bridge_inflight_sessions

    finish_create.set()
    with pytest.raises(ProxyResponseError) as owner_exc_info:
        await asyncio.wait_for(owner_task, timeout=1.0)

    assert owner_exc_info.value.status_code == 429
    assert owner_exc_info.value.payload["error"]["code"] == "capacity_exhausted_active_sessions"
    assert key not in service._http_bridge_sessions
    close_http_bridge_session.assert_awaited_once_with(created)


@pytest.mark.asyncio
async def test_claim_durable_http_bridge_session_propagates_claim_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(
        service._durable_bridge,
        "claim_live_session",
        AsyncMock(side_effect=RuntimeError("db unavailable")),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())

    with pytest.raises(RuntimeError, match="db unavailable"):
        await service._claim_durable_http_bridge_session(session, allow_takeover=True)


@pytest.mark.asyncio
async def test_claim_durable_http_bridge_session_falls_back_when_tables_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(
        service._durable_bridge,
        "claim_live_session",
        AsyncMock(side_effect=RuntimeError("no such table: http_bridge_sessions")),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())

    await service._claim_durable_http_bridge_session(session, allow_takeover=True)

    assert session.durable_session_id is None
    assert session.durable_owner_epoch is None


@pytest.mark.asyncio
async def test_claim_durable_http_bridge_session_rejects_remote_owner_without_takeover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(
        service._durable_bridge,
        "claim_live_session",
        AsyncMock(
            return_value=proxy_service.DurableBridgeLookup(
                session_id="durable-1",
                canonical_kind="session_header",
                canonical_key="sid-123",
                api_key_scope="__anonymous__",
                account_id="acc-1",
                owner_instance_id="instance-b",
                owner_epoch=2,
                lease_expires_at=proxy_service.utcnow() + timedelta(seconds=60),
                state=HttpBridgeSessionState.ACTIVE,
                latest_turn_state=None,
                latest_response_id=None,
            )
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._claim_durable_http_bridge_session(session, allow_takeover=False)

    assert exc_info.value.status_code == 409
    assert exc_info.value.payload["error"]["code"] == "bridge_instance_mismatch"


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_hard_continuity_lookup_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    create_http_bridge_session = AsyncMock(return_value=created_session)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service,
        "_http_bridge_owner_instance",
        AsyncMock(side_effect=ConnectionRefusedError("db unavailable")),
    )
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(side_effect=ConnectionRefusedError("db unavailable")),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            key,
            headers={"x-codex-session-id": "sid-123"},
            affinity=proxy_service._AffinityPolicy(
                key="sid-123",
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )

    create_http_bridge_session.assert_not_awaited()
    exc = exc_info.value
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    assert exc.payload["error"]["message"] == "HTTP bridge owner metadata unavailable; retry later."


@pytest.mark.asyncio
async def test_maybe_prewarm_http_bridge_session_skips_continuity_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=AsyncMock(), close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        codex_session=True,
        prewarm_lock=anyio.Lock(),
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-1",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_prev_1",
        transport="http",
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_codex_prewarm_enabled=True),
    )
    reconnect = AsyncMock()
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    await service._maybe_prewarm_http_bridge_session(
        session,
        request_state=request_state,
        text_data='{"model":"gpt-5.4","input":"hello"}',
    )

    assert session.prewarmed is False
    reconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_masks_single_previous_response_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-prev-miss",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_missing_single",
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 400,
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Previous response with id 'resp_missing_single' not found.",
                    "param": "previous_response_id",
                },
            },
            separators=(",", ":"),
        ),
    )

    event_queue = request_state.event_queue
    assert event_queue is not None
    event_block = await event_queue.get()
    assert event_block is not None
    assert await event_queue.get() is None
    payload = proxy_service.parse_sse_data_json(event_block)
    assert isinstance(payload, dict)
    response = payload.get("response")
    assert isinstance(response, dict)
    error = response.get("error")
    assert isinstance(error, dict)

    assert payload["type"] == "response.failed"
    assert error["code"] == "stream_incomplete"
    assert error["message"] == "Upstream websocket closed before response.completed"
    assert "previous_response_not_found" not in json.dumps(payload)
    assert request_state.error_http_status_override == 502
    assert request_state.previous_response_not_found_rewritten is True
    assert session.upstream_control.reconnect_requested is False
    assert session.pending_requests == deque()
    assert session.queued_request_count == 0


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_masks_previous_response_not_found_when_anchor_was_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-lost-anchor",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 400,
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": (
                        "Previous response with id 'resp_03ac4d75eac7c5d1016a0a619e8a688191b5267ba7ffac3111' not found."
                    ),
                    "param": "previous_response_id",
                },
            },
            separators=(",", ":"),
        ),
    )

    event_queue = request_state.event_queue
    assert event_queue is not None
    event_block = await event_queue.get()
    assert event_block is not None
    assert await event_queue.get() is None
    payload = proxy_service.parse_sse_data_json(event_block)
    assert isinstance(payload, dict)
    response = payload.get("response")
    assert isinstance(response, dict)
    error = response.get("error")
    assert isinstance(error, dict)

    assert payload["type"] == "response.failed"
    assert error["code"] == "stream_incomplete"
    assert error["message"] == "Upstream websocket closed before response.completed"
    payload_text = json.dumps(payload)
    assert "previous_response_not_found" not in payload_text
    assert "resp_03ac4d75eac7c5d1016a0a619e8a688191b5267ba7ffac3111" not in payload_text
    assert request_state.error_http_status_override == 502
    assert request_state.previous_response_not_found_rewritten is True
    assert session.pending_requests == deque()
    assert session.queued_request_count == 0


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_retries_precreated_usage_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-precreated-limit",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"hello"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_limit", None),
        headers={"x-codex-turn-state": "http_turn_limit"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_limit",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-limited", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    handle_stream_error = AsyncMock()
    retry_precreated = AsyncMock(return_value=True)
    finalize = AsyncMock()
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", retry_precreated)
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 429,
                "error": {
                    "type": "usage_limit_reached",
                    "message": "The usage limit has been reached",
                    "plan_type": "team",
                    "resets_at": 1_778_790_595,
                    "resets_in_seconds": 14_555,
                },
            },
            separators=(",", ":"),
        ),
    )

    handle_stream_error.assert_awaited_once()
    retry_precreated.assert_awaited_once_with(session)
    finalize.assert_not_awaited()
    assert request_state.event_queue is not None
    assert request_state.event_queue.empty()
    assert session.pending_requests == deque([request_state])
    assert session.queued_request_count == 1


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_masks_failed_replay_usage_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-precreated-replay-failed",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"hello"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_limit", None),
        headers={"x-codex-turn-state": "http_turn_limit"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_limit",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-limited", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    handle_stream_error = AsyncMock()

    async def failed_replay(target_session: proxy_service._HTTPBridgeSession) -> bool:
        target_session.account = cast(Any, SimpleNamespace(id="acc-replacement", status=AccountStatus.ACTIVE))
        return False

    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", failed_replay)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 429,
                "error": {
                    "type": "usage_limit_reached",
                    "message": "The usage limit has been reached",
                    "plan_type": "team",
                    "resets_at": 1_778_790_595,
                    "resets_in_seconds": 14_555,
                },
            },
            separators=(",", ":"),
        ),
    )

    handle_stream_error.assert_awaited_once()
    event_queue = request_state.event_queue
    assert event_queue is not None
    event_block = await event_queue.get()
    assert event_block is not None
    assert await event_queue.get() is None
    payload = proxy_service.parse_sse_data_json(event_block)
    assert isinstance(payload, dict)
    response = payload.get("response")
    assert isinstance(response, dict)
    error = response.get("error")
    assert isinstance(error, dict)

    assert payload["type"] == "response.failed"
    assert error["code"] == "stream_incomplete"
    assert "usage_limit_reached" not in json.dumps(payload)
    assert request_state.error_http_status_override == 502
    assert session.pending_requests == deque()
    assert session.queued_request_count == 0


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_masks_previous_response_usage_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-prev-limit",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_owner_only",
        preferred_account_id="acc-limited",
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text=(
            '{"type":"response.create","model":"gpt-5.5","previous_response_id":"resp_owner_only","input":"follow-up"}'
        ),
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_prev_limit", None),
        headers={"x-codex-turn-state": "http_turn_prev_limit"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_prev_limit",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-limited", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    handle_stream_error = AsyncMock()
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 429,
                "error": {
                    "type": "usage_limit_reached",
                    "message": "The usage limit has been reached",
                    "plan_type": "team",
                    "resets_at": 1_778_790_595,
                    "resets_in_seconds": 14_555,
                },
            },
            separators=(",", ":"),
        ),
    )

    handle_stream_error.assert_awaited_once()
    event_queue = request_state.event_queue
    assert event_queue is not None
    event_block = await event_queue.get()
    assert event_block is not None
    assert await event_queue.get() is None
    payload = proxy_service.parse_sse_data_json(event_block)
    assert isinstance(payload, dict)
    response = payload.get("response")
    assert isinstance(response, dict)
    error = response.get("error")
    assert isinstance(error, dict)

    assert payload["type"] == "response.failed"
    assert error["code"] == "upstream_unavailable"
    assert "usage_limit_reached" not in json.dumps(payload)
    assert request_state.error_http_status_override == 502
    assert session.upstream_control.reconnect_requested is True
    assert session.pending_requests == deque()
    assert session.queued_request_count == 0


@pytest.mark.asyncio
async def test_http_bridge_masks_owner_pinned_quota_error_with_queued_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-prev-limit-queued",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_owner_queued",
        preferred_account_id="acc-limited",
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text=(
            '{"type":"response.create","model":"gpt-5.5","previous_response_id":"resp_owner_queued",'
            '"input":"follow-up"}'
        ),
        transport="http",
        skip_request_log=True,
    )
    queued_request_state = proxy_service._WebSocketRequestState(
        request_id="req-still-pending",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=2.0,
        response_id="resp_still_pending",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"next"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_prev_limit_queued", None),
        headers={"x-codex-turn-state": "http_turn_prev_limit_queued"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_prev_limit_queued",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-limited", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state, queued_request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=2,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    handle_stream_error = AsyncMock()
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 429,
                "error": {
                    "type": "usage_limit_reached",
                    "message": "The usage limit has been reached",
                    "plan_type": "team",
                    "resets_at": 1_778_790_595,
                    "resets_in_seconds": 14_555,
                },
            },
            separators=(",", ":"),
        ),
    )

    handle_stream_error.assert_awaited_once()
    event_queue = request_state.event_queue
    assert event_queue is not None
    event_block = await event_queue.get()
    assert event_block is not None
    assert await event_queue.get() is None
    payload = proxy_service.parse_sse_data_json(event_block)
    assert isinstance(payload, dict)
    response = payload.get("response")
    assert isinstance(response, dict)
    error = response.get("error")
    assert isinstance(error, dict)

    assert payload["type"] == "response.failed"
    assert error["code"] == "upstream_unavailable"
    assert "usage_limit_reached" not in json.dumps(payload)
    assert request_state.error_http_status_override == 502
    assert session.upstream_control.reconnect_requested is True
    assert session.upstream_control.retire_after_drain is True
    assert session.pending_requests == deque([queued_request_state])
    assert session.queued_request_count == 1


@pytest.mark.asyncio
async def test_http_bridge_retire_after_drain_closes_session_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-prev-limit-cancel",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_owner_cancel",
        preferred_account_id="acc-limited",
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text=(
            '{"type":"response.create","model":"gpt-5.5","previous_response_id":"resp_owner_cancel",'
            '"input":"follow-up"}'
        ),
        transport="http",
        skip_request_log=True,
    )
    queued_request_state = proxy_service._WebSocketRequestState(
        request_id="req-cancelled",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=2.0,
        response_id="resp_cancelled",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"next"}',
        transport="http",
        skip_request_log=True,
    )
    close = AsyncMock()
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_prev_limit_cancel", None),
        headers={"x-codex-turn-state": "http_turn_prev_limit_cancel"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_prev_limit_cancel",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-limited", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=close)),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state, queued_request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=2,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    handle_stream_error = AsyncMock()
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 429,
                "error": {
                    "type": "usage_limit_reached",
                    "message": "The usage limit has been reached",
                    "plan_type": "team",
                    "resets_at": 1_778_790_595,
                    "resets_in_seconds": 14_555,
                },
            },
            separators=(",", ":"),
        ),
    )

    assert session.upstream_control.retire_after_drain is True
    assert session.closed is False
    assert await service._detach_http_bridge_request(session, request_state=queued_request_state) is True

    assert session.closed is True
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_bridge_retire_after_drain_waits_for_queued_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    close = AsyncMock()
    release_live_session = AsyncMock()
    release_account_lease = AsyncMock()
    service._durable_bridge = cast(Any, SimpleNamespace(release_live_session=release_live_session))
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(http_responses_session_bridge_instance_id="instance-retire-drain"),
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_retire_queued", None),
        headers={"x-codex-turn-state": "http_turn_retire_queued"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_retire_queued",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-limited", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=close)),
        upstream_control=proxy_service._WebSocketUpstreamControl(
            reconnect_requested=True,
            retire_after_drain=True,
        ),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    lease = proxy_service.AccountLease(
        lease_id="lease-retire-drain",
        account_id=session.account.id,
        kind="stream",
        acquired_at=1.0,
    )
    session.account_lease = lease
    session.durable_session_id = "durable-retire-drain"
    session.durable_owner_epoch = 3

    assert await service._retire_http_bridge_after_drain_if_ready(session) is False
    assert session.closed is False
    close.assert_not_awaited()
    release_live_session.assert_not_awaited()
    release_account_lease.assert_not_awaited()

    async with session.pending_lock:
        session.queued_request_count = 0

    assert await service._retire_http_bridge_after_drain_if_ready(session) is True
    assert session.closed is True
    release_live_session.assert_awaited_once_with(
        session_id="durable-retire-drain",
        instance_id="instance-retire-drain",
        owner_epoch=3,
        draining=False,
    )
    release_account_lease.assert_awaited_once_with(lease)
    assert session.account_lease is None
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_bridge_retire_after_drain_does_not_cancel_current_upstream_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    close = AsyncMock()
    release_live_session = AsyncMock()
    release_account_lease = AsyncMock()
    service._durable_bridge = cast(Any, SimpleNamespace(release_live_session=release_live_session))
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(http_responses_session_bridge_instance_id="instance-reader-retire"),
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_reader_retire", None),
        headers={"x-codex-turn-state": "http_turn_reader_retire"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_reader_retire",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-reader-retire", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=close)),
        upstream_control=proxy_service._WebSocketUpstreamControl(
            reconnect_requested=True,
            retire_after_drain=True,
        ),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    lease = proxy_service.AccountLease(
        lease_id="lease-reader-retire",
        account_id=session.account.id,
        kind="stream",
        acquired_at=1.0,
    )
    session.account_lease = lease
    session.durable_session_id = "durable-reader-retire"
    session.durable_owner_epoch = 7
    current_task = asyncio.current_task()
    assert current_task is not None
    session.upstream_reader = current_task

    assert await service._retire_http_bridge_after_drain_if_ready(session) is True

    assert session.closed is True
    assert session.upstream_reader is None
    close.assert_awaited_once()
    release_live_session.assert_awaited_once_with(
        session_id="durable-reader-retire",
        instance_id="instance-reader-retire",
        owner_epoch=7,
        draining=False,
    )
    release_account_lease.assert_awaited_once_with(lease)
    assert session.account_lease is None


@pytest.mark.asyncio
async def test_submit_http_bridge_request_starts_api_key_reservation_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    api_key = _make_api_key(key_id="key-http-heartbeat", assigned_account_ids=[])
    reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="reservation-http-heartbeat",
        key_id=api_key.id,
        model="gpt-5.5",
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-http-heartbeat",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=reservation,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"new"}',
        transport="http",
        api_key=api_key,
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_heartbeat", api_key.id),
        headers={"x-codex-turn-state": "http_turn_heartbeat"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_heartbeat",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-http-heartbeat", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[session.key] = session
    started = asyncio.Event()
    seen: dict[str, object] = {}

    async def fake_heartbeat(**kwargs: object) -> None:
        seen.update(kwargs)
        started.set()
        stop_event = cast(asyncio.Event, kwargs["stop_event"])
        await stop_event.wait()

    admission_saw_heartbeat = False

    async def fake_acquire_admission(
        state: proxy_service._WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
        bridge_session: proxy_service._HTTPBridgeSession | None = None,
        compact: bool = False,
        account_id: str | None = None,
        surface: str = "websocket",
    ) -> None:
        del bridge_session
        del compact
        del account_id
        del surface
        nonlocal admission_saw_heartbeat
        admission_saw_heartbeat = state.api_key_reservation_heartbeat_task is not None
        state.response_create_gate = response_create_gate
        await response_create_gate.acquire()
        state.response_create_gate_acquired = True
        state.awaiting_response_created = True

    monkeypatch.setattr(service, "_run_api_key_reservation_heartbeat", fake_heartbeat)
    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", fake_acquire_admission)

    await service._submit_http_bridge_request(
        session,
        request_state=request_state,
        text_data=request_state.request_text or "{}",
        queue_limit=8,
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    assert seen["api_key"] is api_key
    assert seen["reservation"] is reservation
    assert seen["request_id"] == "req-http-heartbeat"
    assert seen["surface"] == "http_bridge"
    assert admission_saw_heartbeat is True
    assert request_state.api_key_reservation_heartbeat_task is not None
    send_text.assert_awaited_once_with(request_state.request_text)

    service._cancel_request_state_api_key_reservation_heartbeat(request_state)


def test_websocket_admission_rejection_cancels_reservation_heartbeat_before_release() -> None:
    source = inspect.getsource(proxy_service.ProxyService.proxy_responses_websocket)
    start_index = source.index("except ProxyResponseError as exc:", source.index("not request_state_registered"))
    branch = source[start_index : source.index("await proxy._emit_websocket_terminal_error", start_index)]

    assert "proxy._release_websocket_request_state_reservation(request_state)" in branch
    assert "_release_websocket_reservation(request_state.api_key_reservation)" not in source


def test_websocket_request_state_reservation_release_cancels_heartbeat_before_release() -> None:
    source = inspect.getsource(proxy_service.ProxyService._release_websocket_request_state_reservation)

    assert source.index("_cancel_request_state_api_key_reservation_heartbeat") < source.index(
        "_release_websocket_reservation"
    )


@pytest.mark.asyncio
async def test_submit_http_bridge_request_rejects_retiring_session() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    close = AsyncMock()
    pending_request_state = proxy_service._WebSocketRequestState(
        request_id="req-pending-retire",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        response_id="resp_pending_retire",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"pending"}',
        transport="http",
        skip_request_log=True,
    )
    new_request_state = proxy_service._WebSocketRequestState(
        request_id="req-new-retire",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=2.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_retiring", None),
        headers={"x-codex-turn-state": "http_turn_retiring"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_retiring",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-limited", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=close)),
        upstream_control=proxy_service._WebSocketUpstreamControl(
            reconnect_requested=True,
            retire_after_drain=True,
        ),
        pending_requests=deque([pending_request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    with pytest.raises(proxy_service.ProxyResponseError) as exc_info:
        await service._submit_http_bridge_request(
            session,
            request_state=new_request_state,
            text_data=new_request_state.request_text or "{}",
            queue_limit=8,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    assert session.pending_requests == deque([pending_request_state])
    assert session.queued_request_count == 1
    assert session.closed is False
    send_text.assert_not_awaited()
    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_http_bridge_request_rejects_unregistered_session_after_admission() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-unregistered-submit",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_unregistered", None),
        headers={"x-codex-turn-state": "http_turn_unregistered"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_unregistered",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-unregistered", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    with pytest.raises(proxy_service.ProxyResponseError) as exc_info:
        await service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=8,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    assert session.pending_requests == deque()
    assert session.queued_request_count == 0
    assert request_state.response_create_gate is None
    assert request_state.response_create_gate_acquired is False
    assert session.response_create_gate.locked() is False
    send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_http_bridge_request_rejects_unregistered_closed_session_without_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    retry_fresh = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_retry_http_bridge_request_on_fresh_upstream", retry_fresh)
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-unregistered-closed-submit",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_unregistered_closed", None),
        headers={"x-codex-turn-state": "http_turn_unregistered_closed"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_unregistered_closed",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-unregistered", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        closed=True,
    )

    with pytest.raises(proxy_service.ProxyResponseError) as exc_info:
        await service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=8,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    retry_fresh.assert_not_awaited()
    send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_http_bridge_request_waits_for_closed_session_retirement_before_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    retry_started = asyncio.Event()

    async def retry_fresh(*_args: object, **_kwargs: object) -> bool:
        retry_started.set()
        return True

    monkeypatch.setattr(service, "_retry_http_bridge_request_on_fresh_upstream", retry_fresh)
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-closed-retiring-submit",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_closed_retiring", None),
        headers={"x-codex-turn-state": "http_turn_closed_retiring"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_closed_retiring",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-retiring", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        closed=True,
    )
    service._http_bridge_sessions[session.key] = session

    async with session.lifecycle_lock:
        submit_task = asyncio.create_task(
            service._submit_http_bridge_request(
                session,
                request_state=request_state,
                text_data=request_state.request_text or "{}",
                queue_limit=8,
            )
        )
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(retry_started.wait(), timeout=0.01)
            service._http_bridge_sessions.pop(session.key, None)
        finally:
            if submit_task.done():
                await submit_task

    with pytest.raises(proxy_service.ProxyResponseError) as exc_info:
        await submit_task

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    assert retry_started.is_set() is False
    send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_http_bridge_request_does_not_send_after_retirement_between_validation_and_send() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-retired-after-submit-validation",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_retire_gap", None),
        headers={"x-codex-turn-state": "http_turn_retire_gap"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_retire_gap",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-retire-gap", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=AsyncMock(), close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[session.key] = session
    stale_send_seen = False

    async def send_text(_text: str) -> None:
        nonlocal stale_send_seen
        stale_send_seen = service._http_bridge_sessions.get(session.key) is not session or session.closed

    cast(Any, session.upstream).send_text.side_effect = send_text

    class RetireAfterValidationLock:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_exc: object) -> None:
            if request_state.response_create_gate_acquired and request_state not in session.pending_requests:
                session.closed = True
                service._http_bridge_sessions.pop(session.key, None)
            return None

    service._http_bridge_lock = cast(Any, RetireAfterValidationLock())

    try:
        await service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=8,
        )
    except proxy_service.ProxyResponseError:
        pass
    finally:
        if request_state.response_create_gate_acquired:
            await proxy_service._release_websocket_response_create_gate(request_state, session.response_create_gate)

    assert stale_send_seen is False


@pytest.mark.asyncio
async def test_submit_http_bridge_request_rejects_state_after_response_event() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-visible-submit",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        response_id="resp_visible_submit",
        response_event_count=1,
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.5","input":"visible"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_visible_submit", None),
        headers={"x-codex-turn-state": "http_turn_visible_submit"},
        affinity=proxy_service._AffinityPolicy(
            key="http_turn_visible_submit",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-visible-submit", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    with pytest.raises(proxy_service.ProxyResponseError) as exc_info:
        await service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=8,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    assert session.pending_requests == deque()
    assert session.queued_request_count == 0
    send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_http_bridge_request_on_fresh_upstream_reconnects_without_resending_previous_response_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-1",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_prev_1",
        transport="http",
        error_code_override="upstream_unavailable",
        error_message_override="Proxy request budget exhausted",
        error_http_status_override=502,
    )
    reconnect = AsyncMock()
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    recovered = await service._retry_http_bridge_request_on_fresh_upstream(
        session=session,
        request_state=request_state,
        text_data='{"type":"response.create","previous_response_id":"resp_prev_1"}',
        send_request=False,
    )

    assert recovered is True
    assert request_state.replay_count == 1
    assert request_state.error_code_override is None
    assert request_state.error_message_override is None
    assert request_state.error_http_status_override is None
    reconnect.assert_awaited_once_with(
        session,
        request_state=request_state,
        restart_reader=True,
    )
    send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_http_bridge_request_on_fresh_upstream_refuses_after_response_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-visible", None),
        headers={"x-codex-session-id": "sid-visible"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-visible",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-visible", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-visible",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_prev_visible",
        response_event_count=1,
        transport="http",
    )
    reconnect = AsyncMock()
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    recovered = await service._retry_http_bridge_request_on_fresh_upstream(
        session=session,
        request_state=request_state,
        text_data='{"type":"response.create","previous_response_id":"resp_prev_visible"}',
        send_request=False,
    )

    assert recovered is False
    assert request_state.replay_count == 0
    reconnect.assert_not_awaited()
    send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_masks_unmatched_missing_tool_output_followups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    finalize_request_state = AsyncMock()
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())

    request_state_a = proxy_service._WebSocketRequestState(
        request_id="req-missing-tool-a",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_missing_tool_a",
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    request_state_b = proxy_service._WebSocketRequestState(
        request_id="req-missing-tool-b",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_missing_tool_a",
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state_a, request_state_b]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(2),
        queued_request_count=2,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 400,
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_request_error",
                    "message": "No tool output found for function call call_missing_output.",
                    "param": "input",
                },
            },
            separators=(",", ":"),
        ),
    )

    for request_state in (request_state_a, request_state_b):
        event_queue = request_state.event_queue
        assert event_queue is not None
        event_block = await event_queue.get()
        assert event_block is not None
        assert await event_queue.get() is None
        payload = proxy_service.parse_sse_data_json(event_block)
        assert isinstance(payload, dict)
        response = payload.get("response")
        assert isinstance(response, dict)
        error = response.get("error")
        assert isinstance(error, dict)
        assert payload["type"] == "response.failed"
        assert error["code"] == "stream_incomplete"
        assert "call_missing_output" not in json.dumps(payload)
        assert request_state.error_http_status_override == 502

    assert session.upstream_control.reconnect_requested is True
    assert session.pending_requests == deque()
    assert session.queued_request_count == 0
    assert finalize_request_state.await_count == 2
    finalized_requests = [call.args[0] for call in finalize_request_state.await_args_list]
    assert finalized_requests == [request_state_a, request_state_b]


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_does_not_mask_unmatched_missing_tool_output_across_chains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    finalize_request_state = AsyncMock()
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())

    request_state_a = proxy_service._WebSocketRequestState(
        request_id="req-missing-tool-a",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_missing_tool_a",
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    request_state_b = proxy_service._WebSocketRequestState(
        request_id="req-missing-tool-b",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_missing_tool_b",
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state_a, request_state_b]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(2),
        queued_request_count=2,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 400,
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_request_error",
                    "message": "No tool output found for function call call_missing_output.",
                    "param": "input",
                },
            },
            separators=(",", ":"),
        ),
    )

    assert session.pending_requests == deque([request_state_a, request_state_b])
    assert session.queued_request_count == 2
    assert finalize_request_state.await_count == 0
    for request_state in (request_state_a, request_state_b):
        event_queue = request_state.event_queue
        assert event_queue is not None
        assert event_queue.empty()


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_scopes_tool_dedupe_to_request_state() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state_a = proxy_service._WebSocketRequestState(
        request_id="req-bridge-tool-a",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        response_id="resp_bridge_tool_a",
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    request_state_b = proxy_service._WebSocketRequestState(
        request_id="req-bridge-tool-b",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=2.0,
        response_id="resp_bridge_tool_b",
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state_a, request_state_b]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(2),
        queued_request_count=2,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    def tool_event(response_id: str, call_id: str) -> str:
        return json.dumps(
            {
                "type": "response.output_item.done",
                "response": {"id": response_id, "status": "in_progress"},
                "response_id": response_id,
                "item": {
                    "type": "function_call",
                    "name": "write_stdin",
                    "arguments": '{"session_id":1,"chars":"","yield_time_ms":1000}',
                    "call_id": call_id,
                },
            },
            separators=(",", ":"),
        )

    await service._process_http_bridge_upstream_text(session, tool_event("resp_bridge_tool_a", "call_a"))
    await service._process_http_bridge_upstream_text(session, tool_event("resp_bridge_tool_b", "call_b"))

    assert request_state_a.suppressed_duplicate_tool_call is False
    assert request_state_b.suppressed_duplicate_tool_call is False
    queue_a = request_state_a.event_queue
    queue_b = request_state_b.event_queue
    assert queue_a is not None
    assert queue_b is not None
    event_a = await asyncio.wait_for(queue_a.get(), timeout=0.1)
    event_b = await asyncio.wait_for(queue_b.get(), timeout=0.1)
    assert event_a is not None
    assert event_b is not None
    payload_a = proxy_service.parse_sse_data_json(event_a)
    payload_b = proxy_service.parse_sse_data_json(event_b)
    assert isinstance(payload_a, dict)
    assert isinstance(payload_b, dict)
    item_a = payload_a.get("item")
    item_b = payload_b.get("item")
    assert isinstance(item_a, dict)
    assert isinstance(item_b, dict)
    assert item_a["call_id"] == "call_a"
    assert item_b["call_id"] == "call_b"


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_marks_text_delta_downstream_visible() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-bridge-visible",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        response_id="resp_bridge_visible",
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-visible", None),
        headers={"x-codex-session-id": "sid-visible"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-visible",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-visible", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.output_text.delta",
                "response_id": "resp_bridge_visible",
                "delta": "I started",
            },
            separators=(",", ":"),
        ),
    )

    assert request_state.downstream_visible is True
    event_queue = request_state.event_queue
    assert event_queue is not None
    forwarded = await asyncio.wait_for(event_queue.get(), timeout=1.0)
    assert forwarded is not None
    forwarded_payload = proxy_service.parse_sse_data_json(forwarded)
    assert forwarded_payload is not None
    assert forwarded_payload["delta"] == "I started"


@pytest.mark.asyncio
async def test_retry_http_bridge_request_on_fresh_upstream_refuses_to_resend_previous_response_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-1",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_prev_1",
        transport="http",
    )
    reconnect = AsyncMock()
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    recovered = await service._retry_http_bridge_request_on_fresh_upstream(
        session=session,
        request_state=request_state,
        text_data='{"type":"response.create","previous_response_id":"resp_prev_1"}',
        send_request=True,
    )

    assert recovered is False
    assert request_state.replay_count == 0
    reconnect.assert_not_awaited()
    send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_http_bridge_request_on_fresh_upstream_replays_retry_safe_injection_without_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Durable-anchor injections opt in to fresh-turn replay on send failure.

    The proxy captures the original unanchored full-resend payload before
    injecting ``previous_response_id`` on durable reattach. That text is a
    safe fresh-turn replay target because it already contains the full
    history; dropping the anchor and replaying is equivalent to the
    client's own retry.
    """
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-safe", None),
        headers={"x-codex-session-id": "sid-safe"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-safe",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-retry-safe",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_prev_safe",
        proxy_injected_previous_response_id=True,
        fresh_upstream_request_text='{"type":"response.create","input":"full-history-fallback"}',
        fresh_upstream_request_is_retry_safe=True,
        transport="http",
    )
    reconnect = AsyncMock()
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    recovered = await service._retry_http_bridge_request_on_fresh_upstream(
        session=session,
        request_state=request_state,
        text_data='{"type":"response.create","previous_response_id":"resp_prev_safe","input":"full-history-fallback"}',
        send_request=True,
    )

    assert recovered is True
    assert request_state.replay_count == 1
    # Replaying should have dropped the anchor metadata so the request
    # executes as a fresh turn using the captured unanchored payload.
    assert request_state.previous_response_id is None
    assert request_state.proxy_injected_previous_response_id is False
    send_text.assert_awaited_once_with('{"type":"response.create","input":"full-history-fallback"}')


@pytest.mark.asyncio
async def test_retry_http_bridge_request_on_fresh_upstream_refuses_session_level_injection_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session-level injections must not be replayed as fresh turns.

    When the proxy injects ``previous_response_id`` from a bridge session's
    last completed response, the original payload may have relied on the
    anchor for context (for example a single-item follow-up whose prior
    turns live only in the stored conversation). Dropping the anchor and
    replaying would silently turn the continuation into a context-free
    fresh turn and return wrong-but-successful output instead of surfacing
    the retriable send failure.
    """
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-unsafe", None),
        headers={"x-codex-session-id": "sid-unsafe"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-unsafe",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-retry-unsafe",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp_prev_unsafe",
        proxy_injected_previous_response_id=True,
        fresh_upstream_request_text='{"type":"response.create","input":"single-item-followup"}',
        fresh_upstream_request_is_retry_safe=False,
        transport="http",
    )
    reconnect = AsyncMock()
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    recovered = await service._retry_http_bridge_request_on_fresh_upstream(
        session=session,
        request_state=request_state,
        text_data='{"type":"response.create","previous_response_id":"resp_prev_unsafe","input":"single-item-followup"}',
        send_request=True,
    )

    assert recovered is False
    assert request_state.replay_count == 0
    reconnect.assert_not_awaited()
    send_text.assert_not_awaited()


def test_http_bridge_can_recover_during_drain_for_previous_response_anchor() -> None:
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_123", None)

    assert (
        proxy_service._http_bridge_can_recover_during_drain(
            key=key,
            headers={"x-codex-turn-state": "http_turn_123"},
            previous_response_id="resp_prev_1",
            durable_lookup=None,
        )
        is True
    )


def test_http_bridge_can_recover_during_drain_for_session_header_bootstrap() -> None:
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)

    assert (
        proxy_service._http_bridge_can_recover_during_drain(
            key=key,
            headers={"x-codex-session-id": "sid-123"},
            previous_response_id=None,
            durable_lookup=None,
        )
        is False
    )


def test_http_bridge_can_recover_during_drain_ignores_soft_prompt_cache_latest_response_anchor() -> None:
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache-key", None)
    durable_lookup = proxy_service.DurableBridgeLookup(
        session_id="sess-soft",
        canonical_kind="prompt_cache",
        canonical_key="cache-key",
        api_key_scope="__anonymous__",
        account_id="acc-1",
        owner_instance_id="instance-a",
        owner_epoch=1,
        lease_expires_at=datetime.now(timezone.utc),
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state="http_turn_soft",
        latest_response_id="resp_soft",
    )

    assert (
        proxy_service._http_bridge_can_recover_during_drain(
            key=key,
            headers={},
            previous_response_id=None,
            durable_lookup=durable_lookup,
        )
        is False
    )


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_soft_mismatch_rebinds_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache-key", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-fresh", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
        gateway_safe_mode=True,
    )

    assert resolved is created_session


@pytest.mark.asyncio
async def test_create_http_bridge_session_fails_closed_when_previous_response_owner_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    preferred_account = cast(Any, SimpleNamespace(id="acc-owner", status=AccountStatus.ACTIVE))
    fallback_account = cast(Any, SimpleNamespace(id="acc-fallback", status=AccountStatus.ACTIVE))
    select_account = AsyncMock(
        side_effect=[
            proxy_service.AccountSelection(account=preferred_account, error_message=None, error_code=None),
            proxy_service.AccountSelection(account=fallback_account, error_message=None, error_code=None),
        ]
    )
    ensure_fresh = AsyncMock(side_effect=[aiohttp.ClientError("preferred connect failed"), fallback_account])
    open_upstream = AsyncMock(
        return_value=cast(Any, SimpleNamespace(response_header=lambda _name: None, close=AsyncMock()))
    )

    async def fake_relay(_session: proxy_service._HTTPBridgeSession) -> None:
        return None

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        prefer_earlier_reset_accounts=False,
                        routing_strategy=None,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(service, "_select_account_with_budget", select_account)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", ensure_fresh)
    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", open_upstream)
    monkeypatch.setattr(service, "_relay_http_bridge_upstream_messages", fake_relay)

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._create_http_bridge_session(
            key,
            headers={"x-codex-session-id": "sid-123"},
            affinity=proxy_service._AffinityPolicy(
                key="sid-123",
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            request_stage="reattach",
            preferred_account_id="acc-owner",
            require_preferred_account=True,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "previous_response_owner_unavailable"
    open_upstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_via_http_bridge_replays_durable_full_resend_when_owner_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    historical_input: list[proxy_service.JsonValue] = [
        {"role": "user", "content": [{"type": "input_text", "text": "old question"}]},
        {"type": "function_call_output", "call_id": "call_old", "output": "old output"},
    ]
    new_input: proxy_service.JsonValue = {
        "role": "user",
        "content": [{"type": "input_text", "text": "next question"}],
    }
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [*historical_input, new_input],
        }
    )
    durable_lookup = proxy_service.DurableBridgeLookup(
        session_id="durable-owner-unavailable",
        canonical_kind="session_header",
        canonical_key="sid-owner-unavailable",
        api_key_scope="__anonymous__",
        account_id="acc-owner",
        owner_instance_id=None,
        owner_epoch=1,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state="sid-owner-unavailable",
        latest_response_id="resp_completed_anchor",
        latest_input_item_count=len(historical_input),
        latest_input_full_fingerprint=proxy_service._fingerprint_input_items(historical_input),
    )
    owner_unavailable = ProxyResponseError(
        502,
        proxy_service.openai_error(
            "previous_response_owner_unavailable",
            "Previous response owner account is unavailable; retry later.",
            error_type="server_error",
        ),
    )
    capacity_unavailable = ProxyResponseError(
        503,
        proxy_service.openai_error("no_accounts", "Rate limit exceeded. Try again in 120s"),
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-owner-unavailable", None),
        headers={"session_id": "sid-owner-unavailable"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-owner-unavailable",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-fallback", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    get_or_create = AsyncMock(side_effect=[owner_unavailable, capacity_unavailable, session])
    captured_request_states: list[proxy_service._WebSocketRequestState] = []
    captured_text_data: list[str] = []

    async def fake_stream_events(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ):
        del queue_limit, propagate_http_errors, downstream_turn_state
        captured_request_states.append(request_state)
        captured_text_data.append(text_data)
        yield 'data: {"type":"response.completed"}\n\n'

    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(http_bridge_streaming_module, "_http_bridge_account_capacity_wait_seconds", lambda _exc: 0.001)
    monkeypatch.setattr(http_bridge_streaming_module, "_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=durable_lookup))
    monkeypatch.setattr(service, "_http_bridge_has_live_local_session", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_http_bridge_can_forward_to_active_owner", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_stream_http_bridge_session_events", fake_stream_events)

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"session_id": "sid-owner-unavailable"},
            codex_session_affinity=True,
            propagate_http_errors=True,
            openai_cache_affinity=True,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    keepalive = proxy_service.parse_sse_data_json(chunks[0])
    assert keepalive is not None
    assert keepalive["status"] == "waiting_for_account_capacity"
    assert chunks[-1] == 'data: {"type":"response.completed"}\n\n'
    assert get_or_create.await_count == 3
    first_call = get_or_create.await_args_list[0]
    second_call = get_or_create.await_args_list[1]
    third_call = get_or_create.await_args_list[2]
    assert first_call.kwargs["previous_response_id"] == "resp_completed_anchor"
    assert first_call.kwargs["preferred_account_id"] == "acc-owner"
    assert second_call.kwargs["previous_response_id"] is None
    assert second_call.kwargs["preferred_account_id"] is None
    assert second_call.kwargs["durable_lookup"] is None
    assert third_call.kwargs["previous_response_id"] is None
    assert third_call.kwargs["preferred_account_id"] is None
    assert third_call.kwargs["durable_lookup"] is None
    assert captured_request_states[0].previous_response_id is None
    replay_payload = json.loads(captured_text_data[0])
    assert "previous_response_id" not in replay_payload
    assert replay_payload["input"] == [*historical_input, new_input]


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_prompt_cache_mismatch_stays_local_when_gateway_safe_mode_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache-key", None)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-fresh", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
        gateway_safe_mode=False,
    )

    assert resolved is created_session


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_sticky_thread_mismatch_forwards_in_gateway_safe_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("sticky_thread", "thread-key", None)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )
    service._ring_membership = cast(Any, SimpleNamespace(resolve_endpoint=AsyncMock(return_value="http://instance-b")))

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="thread-key", kind=proxy_service.StickySessionKind.STICKY_THREAD),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
        gateway_safe_mode=True,
    )

    assert isinstance(resolved, proxy_service._HTTPBridgeOwnerForward)
    assert resolved.owner_instance == "instance-b"
    assert resolved.owner_endpoint == "http://instance-b"


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_prevents_forward_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_123", None)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    create_http_bridge_session = AsyncMock()
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    claim_durable = AsyncMock()
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", claim_durable)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            key,
            headers={"x-codex-turn-state": "http_turn_123"},
            affinity=proxy_service._AffinityPolicy(key="http_turn_123"),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
            allow_forward_to_owner=True,
            forwarded_request=True,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"]["code"] == "bridge_forward_loop_prevented"
    create_http_bridge_session.assert_not_awaited()
    claim_durable.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_replaces_live_session_when_scope_becomes_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("request", "bridge-key", "key-1")
    stale_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-stale", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    replacement_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-fresh", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = stale_session
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    monkeypatch.setattr(
        service,
        "_create_http_bridge_session",
        AsyncMock(return_value=replacement_session),
    )
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a"])),
    )
    close_session = AsyncMock()
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)

    reused = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        api_key=_make_api_key(
            key_id="key-1",
            assigned_account_ids=[],
            account_assignment_scope_enabled=True,
        ),
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert reused is replacement_session
    assert service._http_bridge_sessions[key] is replacement_session
    assert stale_session.closed is True
    await _wait_for_close_await(close_session, stale_session)


@pytest.mark.asyncio
async def test_http_bridge_reader_marks_session_closed_before_reconnect_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session_holder: dict[str, proxy_service._HTTPBridgeSession] = {}

    async def close_upstream() -> None:
        assert session_holder["session"].closed is True

    close = AsyncMock(side_effect=close_upstream)
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=SimpleNamespace(kind="text", text='{"type":"error"}')),
            close=close,
        ),
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )
    session_holder["session"] = session

    async def request_reconnect(
        target_session: proxy_service._HTTPBridgeSession,
        _upstream_text: str,
    ) -> None:
        target_session.upstream_control.reconnect_requested = True
        target_session.upstream_control.retire_after_drain = True

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", request_reconnect)

    await service._relay_http_bridge_upstream_messages(session)

    assert session.closed is True
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_bridge_response_create_gate_timeout_logs_pending_bridge_context(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    held_request = proxy_service._WebSocketRequestState(
        request_id="req-held-gate",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic() - 5.0,
        awaiting_response_created=True,
        transport="http",
    )
    new_request = proxy_service._WebSocketRequestState(
        request_id="req-timeout-gate",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport="http",
    )
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    session = _make_bridge_session(
        key_value="bridge-held-gate",
        pending_requests=deque([held_request]),
        queued_request_count=1,
    )
    session.response_create_gate = gate
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(proxy_admission_wait_timeout_seconds=0.001),
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ProxyResponseError):
            await service._acquire_request_state_response_create_admission(
                new_request,
                response_create_gate=gate,
                bridge_session=session,
            )

    assert "http_bridge_startup_wait_timeout" in caplog.text
    assert "bridge_key=" in caplog.text
    assert "queued_count=1" in caplog.text
    assert "pending_request_ids=req-held-gate" in caplog.text
    assert "pending_request_ages_seconds=" in caplog.text


@pytest.mark.asyncio
async def test_retire_stale_pending_http_bridge_session_unregisters_aliases_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    release_live_session = AsyncMock()
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(release_live_session=release_live_session),
    )
    session = _make_bridge_session(key_value="bridge-stale-cleanup")
    session.downstream_turn_state_aliases.add("http_turn_stale")
    session.previous_response_ids.add("resp_stale")
    session.durable_session_id = "durable-stale"
    session.durable_owner_epoch = 7
    lease = proxy_service.AccountLease(
        lease_id="lease-stale-cleanup",
        account_id=session.account.id,
        kind="stream",
        acquired_at=1.0,
    )
    session.account_lease = lease
    close = cast(Any, session.upstream).close
    release_account_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)
    service._http_bridge_sessions[session.key] = session
    service._http_bridge_turn_state_index[
        proxy_service._http_bridge_turn_state_alias_key("http_turn_stale", session.key.api_key_id)
    ] = session.key
    service._http_bridge_previous_response_index[
        proxy_service._http_bridge_previous_response_alias_key("resp_stale", session.key.api_key_id)
    ] = session.key
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(http_responses_session_bridge_instance_id="instance-cleanup"),
    )

    await service._retire_stale_pending_http_bridge_session(session, detail="stream_incomplete")

    assert session.closed is True
    assert session.key not in service._http_bridge_sessions
    assert service._http_bridge_turn_state_index == {}
    assert service._http_bridge_previous_response_index == {}
    assert session.downstream_turn_state_aliases == set()
    assert session.previous_response_ids == set()
    release_live_session.assert_awaited_once_with(
        session_id="durable-stale",
        instance_id="instance-cleanup",
        owner_epoch=7,
        draining=False,
    )
    release_account_lease.assert_awaited_once_with(lease)
    assert session.account_lease is None
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_bridge_reader_failed_precreated_replay_retires_registered_session(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-precreated-close",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=event_queue,
        request_text='{"type":"response.create","model":"gpt-5.4","input":"hello"}',
        transport="http",
    )
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    request_state.response_create_gate = gate
    request_state.response_create_gate_acquired = True
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=UpstreamWebSocketMessage(kind="close", close_code=1011)),
            close=AsyncMock(),
        ),
    )
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=gate,
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = session

    async def fail_replay(target_session: proxy_service._HTTPBridgeSession) -> bool:
        assert target_session is session
        request_state.error_code_override = "stream_incomplete"
        request_state.error_message_override = "Upstream closed before response.completed"
        return False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", fail_replay)
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())
    write_request_log = AsyncMock()
    monkeypatch.setattr(service, "_write_request_log", write_request_log)

    with caplog.at_level(logging.INFO):
        await service._relay_http_bridge_upstream_messages(session)

    failed_event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
    assert failed_event is not None
    assert '"code":"stream_incomplete"' in failed_event
    assert await asyncio.wait_for(event_queue.get(), timeout=0.1) is None
    assert list(session.pending_requests) == []
    assert session.queued_request_count == 0
    assert gate.locked() is False
    assert request_state.response_create_gate is None
    assert request_state.response_create_gate_acquired is False
    assert request_state.awaiting_response_created is False
    assert session.closed is True
    assert key not in service._http_bridge_sessions
    write_request_log.assert_awaited_once()
    assert write_request_log.await_args.kwargs["error_code"] == "stream_incomplete"
    assert "http_bridge_event event=retire_stale_pending" in caplog.text


@pytest.mark.asyncio
async def test_http_bridge_reader_retirement_rejects_concurrent_gate_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    old_event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    old_request_state = proxy_service._WebSocketRequestState(
        request_id="req-old-retired-while-new-waits",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=old_event_queue,
        request_text='{"type":"response.create","model":"gpt-5.4","input":"old"}',
        transport="http",
        skip_request_log=True,
    )
    new_request_state = proxy_service._WebSocketRequestState(
        request_id="req-new-waits-on-retired-session",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.4","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    old_request_state.response_create_gate = gate
    old_request_state.response_create_gate_acquired = True
    send_text = AsyncMock()
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=UpstreamWebSocketMessage(kind="close", close_code=1011)),
            send_text=send_text,
            close=AsyncMock(),
        ),
    )
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key-concurrent-retire", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key-concurrent-retire"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([old_request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=gate,
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = session

    async def fail_replay(target_session: proxy_service._HTTPBridgeSession) -> bool:
        assert target_session is session
        old_request_state.error_code_override = "stream_incomplete"
        old_request_state.error_message_override = "Upstream closed before response.completed"
        return False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", fail_replay)

    submit_task = asyncio.create_task(
        service._submit_http_bridge_request(
            session,
            request_state=new_request_state,
            text_data=new_request_state.request_text or "{}",
            queue_limit=8,
        )
    )
    try:
        with anyio.fail_after(1.0):
            while session.queued_request_count < 2:
                await asyncio.sleep(0)

        await service._relay_http_bridge_upstream_messages(session)

        with pytest.raises(proxy_service.ProxyResponseError) as exc_info:
            await submit_task

        assert exc_info.value.status_code == 502
        assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
        assert send_text.await_count == 0
        assert list(session.pending_requests) == []
        assert session.queued_request_count == 0
        assert session.closed is True
        assert key not in service._http_bridge_sessions
        assert new_request_state.response_create_gate is None
        assert new_request_state.response_create_gate_acquired is False
        assert gate.locked() is False
    finally:
        if not submit_task.done():
            submit_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await submit_task


@pytest.mark.asyncio
async def test_http_bridge_reader_retirement_skips_concurrent_prewarm_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    old_event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    old_request_state = proxy_service._WebSocketRequestState(
        request_id="req-old-retired-while-prewarm-waits",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=old_event_queue,
        request_text='{"type":"response.create","model":"gpt-5.4","input":"old"}',
        transport="http",
        skip_request_log=True,
    )
    visible_request_state = proxy_service._WebSocketRequestState(
        request_id="req-visible-prewarm-waits-on-retired-session",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.4","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    old_request_state.response_create_gate = gate
    old_request_state.response_create_gate_acquired = True
    send_text = AsyncMock()
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=UpstreamWebSocketMessage(kind="close", close_code=1011)),
            send_text=send_text,
            close=AsyncMock(),
        ),
    )
    key = proxy_service._HTTPBridgeSessionKey("session_header", "bridge-key-prewarm-retire", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "bridge-key-prewarm-retire"},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-key-prewarm-retire",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([old_request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=gate,
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
        codex_session=True,
        prewarm_lock=anyio.Lock(),
    )
    service._http_bridge_sessions[key] = session

    async def fail_replay(target_session: proxy_service._HTTPBridgeSession) -> bool:
        assert target_session is session
        old_request_state.error_code_override = "stream_incomplete"
        old_request_state.error_message_override = "Upstream closed before response.completed"
        return False

    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(http_responses_session_bridge_codex_prewarm_enabled=True),
    )
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", fail_replay)

    prewarm_task = asyncio.create_task(
        service._maybe_prewarm_http_bridge_session(
            session,
            request_state=visible_request_state,
            text_data=visible_request_state.request_text or "{}",
        )
    )
    try:
        with anyio.fail_after(1.0):
            while not session.prewarmed:
                await asyncio.sleep(0)

        await service._relay_http_bridge_upstream_messages(session)
        await asyncio.wait_for(prewarm_task, timeout=1.0)

        assert send_text.await_count == 0
        assert list(session.pending_requests) == []
        assert session.queued_request_count == 0
        assert session.closed is True
        assert session.prewarmed is False
        assert key not in service._http_bridge_sessions
        assert gate.locked() is False
    finally:
        if not prewarm_task.done():
            prewarm_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await prewarm_task


@pytest.mark.asyncio
async def test_maybe_prewarm_http_bridge_session_skips_unregistered_session_after_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    send_text = AsyncMock()
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-visible-unregistered-prewarm",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.4","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "bridge-key-unregistered-prewarm", None),
        headers={"x-codex-session-id": "bridge-key-unregistered-prewarm"},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-key-unregistered-prewarm",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=send_text, close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
        codex_session=True,
        prewarm_lock=anyio.Lock(),
    )

    async def complete_warmup(_text: str) -> None:
        assert session.pending_requests
        warmup_queue = session.pending_requests[-1].event_queue
        assert warmup_queue is not None
        await warmup_queue.put(None)

    send_text.side_effect = complete_warmup
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(http_responses_session_bridge_codex_prewarm_enabled=True),
    )

    await service._maybe_prewarm_http_bridge_session(
        session,
        request_state=request_state,
        text_data=request_state.request_text or "{}",
    )

    assert send_text.await_count == 0
    assert session.pending_requests == deque()
    assert session.queued_request_count == 0
    assert session.prewarmed is False
    assert session.response_create_gate.locked() is False


@pytest.mark.asyncio
async def test_maybe_prewarm_replaced_session_cleanup_preserves_visible_queue_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    held_request = proxy_service._WebSocketRequestState(
        request_id="req-visible-held-during-prewarm-replace",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.4","input":"held"}',
        transport="http",
        skip_request_log=True,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-visible-prewarm-replaced",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.4","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "bridge-key-prewarm-count", None),
        headers={"x-codex-session-id": "bridge-key-prewarm-count"},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-key-prewarm-count",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=AsyncMock(), close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([held_request]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
        codex_session=True,
        prewarm_lock=anyio.Lock(),
    )
    replacement = proxy_service._HTTPBridgeSession(
        key=session.key,
        headers=session.headers,
        affinity=session.affinity,
        request_model=session.request_model,
        account=session.account,
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(send_text=AsyncMock(), close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[session.key] = replacement
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(http_responses_session_bridge_codex_prewarm_enabled=True),
    )

    await service._maybe_prewarm_http_bridge_session(
        session,
        request_state=request_state,
        text_data=request_state.request_text or "{}",
    )

    assert session.queued_request_count == 1
    assert session.pending_requests == deque([held_request])
    cast(Any, session.upstream).send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_bridge_reader_failed_precreated_replay_retires_when_request_log_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    release_live_session = AsyncMock()
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(release_live_session=release_live_session),
    )
    event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-precreated-log-fails",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=event_queue,
        request_text='{"type":"response.create","model":"gpt-5.4","input":"hello"}',
        transport="http",
    )
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    request_state.response_create_gate = gate
    request_state.response_create_gate_acquired = True
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=UpstreamWebSocketMessage(kind="close", close_code=1011)),
            close=AsyncMock(),
        ),
    )
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key-log-fails", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key-log-fails"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=gate,
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
        durable_session_id="durable-log-fails",
        durable_owner_epoch=3,
    )
    session.downstream_turn_state_aliases.add("http_turn_log_fails")
    session.previous_response_ids.add("resp_log_fails")
    service._http_bridge_sessions[key] = session
    service._http_bridge_turn_state_index[
        proxy_service._http_bridge_turn_state_alias_key("http_turn_log_fails", key.api_key_id)
    ] = key
    service._http_bridge_previous_response_index[
        proxy_service._http_bridge_previous_response_alias_key("resp_log_fails", key.api_key_id)
    ] = key

    async def fail_replay(target_session: proxy_service._HTTPBridgeSession) -> bool:
        assert target_session is session
        request_state.error_code_override = "stream_incomplete"
        request_state.error_message_override = "Upstream closed before response.completed"
        return False

    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(http_responses_session_bridge_instance_id="instance-log-fails"),
    )
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", fail_replay)
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())
    monkeypatch.setattr(service, "_write_request_log", AsyncMock(side_effect=RuntimeError("request log failed")))

    await service._relay_http_bridge_upstream_messages(session)

    failed_event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
    assert failed_event is not None
    assert '"code":"stream_incomplete"' in failed_event
    assert await asyncio.wait_for(event_queue.get(), timeout=0.1) is None
    assert list(session.pending_requests) == []
    assert session.queued_request_count == 0
    assert gate.locked() is False
    assert session.closed is True
    assert key not in service._http_bridge_sessions
    assert service._http_bridge_turn_state_index == {}
    assert service._http_bridge_previous_response_index == {}
    release_live_session.assert_awaited_once_with(
        session_id="durable-log-fails",
        instance_id="instance-log-fails",
        owner_epoch=3,
        draining=False,
    )
    cast(Any, upstream).close.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_bridge_reader_unexpected_processing_error_fails_pending_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    release_live_session = AsyncMock()
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(release_live_session=release_live_session),
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-http-reader-crash",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
    )
    event_queue = request_state.event_queue
    assert event_queue is not None
    await asyncio.wait_for(event_queue.put("seed"), timeout=0.1)
    await asyncio.wait_for(event_queue.get(), timeout=0.1)
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    request_state.response_create_gate_acquired = True
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=SimpleNamespace(kind="text", text='{"type":"response.created"}')),
            close=AsyncMock(),
        ),
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=gate,
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
        durable_session_id="durable-reader-crash",
        durable_owner_epoch=9,
    )
    service._http_bridge_sessions[session.key] = session

    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(http_responses_session_bridge_instance_id="instance-reader-crash"),
    )
    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", AsyncMock(side_effect=RuntimeError("boom")))
    write_request_log = AsyncMock()
    monkeypatch.setattr(service, "_write_request_log", write_request_log)

    await service._relay_http_bridge_upstream_messages(session)

    failed_event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
    assert failed_event is not None
    assert '"code":"stream_incomplete"' in failed_event
    assert "reader" in failed_event
    assert await asyncio.wait_for(event_queue.get(), timeout=0.1) is None
    assert session.closed is True
    assert list(session.pending_requests) == []
    assert session.key not in service._http_bridge_sessions
    release_live_session.assert_awaited_once_with(
        session_id="durable-reader-crash",
        instance_id="instance-reader-crash",
        owner_epoch=9,
        draining=False,
    )
    cast(Any, upstream).close.assert_awaited_once()
    write_request_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_bridge_reader_crash_rejects_concurrent_gate_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    old_event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    old_request_state = proxy_service._WebSocketRequestState(
        request_id="req-old-reader-crash",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=old_event_queue,
        request_text='{"type":"response.create","model":"gpt-5.4","input":"old"}',
        transport="http",
    )
    new_request_state = proxy_service._WebSocketRequestState(
        request_id="req-new-reader-crash-waiter",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.4","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    old_request_state.response_create_gate = gate
    old_request_state.response_create_gate_acquired = True
    send_text = AsyncMock()
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=SimpleNamespace(kind="text", text='{"type":"response.created"}')),
            send_text=send_text,
            close=AsyncMock(),
        ),
    )
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key-reader-crash", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key-reader-crash"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([old_request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=gate,
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = session

    async def write_request_log(**_kwargs: object) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(service, "_write_request_log", write_request_log)

    submit_task = asyncio.create_task(
        service._submit_http_bridge_request(
            session,
            request_state=new_request_state,
            text_data=new_request_state.request_text or "{}",
            queue_limit=8,
        )
    )
    try:
        with anyio.fail_after(1.0):
            while session.queued_request_count < 2:
                await asyncio.sleep(0)

        await service._relay_http_bridge_upstream_messages(session)

        with pytest.raises(proxy_service.ProxyResponseError) as exc_info:
            await submit_task

        assert exc_info.value.status_code == 502
        assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
        assert send_text.await_count == 0
        assert list(session.pending_requests) == []
        assert session.queued_request_count == 0
        assert session.closed is True
        assert new_request_state.response_create_gate is None
        assert new_request_state.response_create_gate_acquired is False
        assert gate.locked() is False
    finally:
        if not submit_task.done():
            submit_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await submit_task


@pytest.mark.asyncio
async def test_http_bridge_reader_crash_rejects_concurrent_prewarm_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    old_event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    old_request_state = proxy_service._WebSocketRequestState(
        request_id="req-old-reader-crash-prewarm",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=old_event_queue,
        request_text='{"type":"response.create","model":"gpt-5.4","input":"old"}',
        transport="http",
    )
    visible_request_state = proxy_service._WebSocketRequestState(
        request_id="req-visible-reader-crash-prewarm",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.4","input":"new"}',
        transport="http",
        skip_request_log=True,
    )
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    old_request_state.response_create_gate = gate
    old_request_state.response_create_gate_acquired = True
    send_text = AsyncMock()
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=SimpleNamespace(kind="text", text='{"type":"response.created"}')),
            send_text=send_text,
            close=AsyncMock(),
        ),
    )
    key = proxy_service._HTTPBridgeSessionKey("session_header", "bridge-key-reader-crash-prewarm", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "bridge-key-reader-crash-prewarm"},
        affinity=proxy_service._AffinityPolicy(
            key="bridge-key-reader-crash-prewarm",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([old_request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=gate,
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
        codex_session=True,
        prewarm_lock=anyio.Lock(),
    )
    service._http_bridge_sessions[key] = session

    async def write_request_log(**_kwargs: object) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(http_responses_session_bridge_codex_prewarm_enabled=True),
    )
    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(service, "_write_request_log", write_request_log)

    prewarm_task = asyncio.create_task(
        service._maybe_prewarm_http_bridge_session(
            session,
            request_state=visible_request_state,
            text_data=visible_request_state.request_text or "{}",
        )
    )
    try:
        with anyio.fail_after(1.0):
            while not session.prewarmed:
                await asyncio.sleep(0)

        await service._relay_http_bridge_upstream_messages(session)
        await asyncio.wait_for(prewarm_task, timeout=1.0)

        assert send_text.await_count == 0
        assert list(session.pending_requests) == []
        assert session.queued_request_count == 0
        assert session.closed is True
        assert session.prewarmed is False
        assert visible_request_state.response_create_gate is None
        assert visible_request_state.response_create_gate_acquired is False
        assert gate.locked() is False
    finally:
        if not prewarm_task.done():
            prewarm_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await prewarm_task


@pytest.mark.asyncio
async def test_http_bridge_reader_crash_marks_session_closed_before_releasing_pending_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-http-reader-crash-order",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
    )
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    request_state.response_create_gate = gate
    request_state.response_create_gate_acquired = True
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=SimpleNamespace(kind="text", text='{"type":"response.created"}')),
            close=AsyncMock(),
        ),
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key-reader-crash-order", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key-reader-crash-order"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=gate,
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )

    async def fail_pending_requests(**_kwargs: object) -> None:
        assert session.closed is True
        await proxy_service._release_websocket_response_create_gate(request_state, gate)

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", fail_pending_requests)

    await service._relay_http_bridge_upstream_messages(session)

    assert gate.locked() is False


@pytest.mark.asyncio
async def test_http_bridge_reader_uses_bridge_request_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-http-reader-bridge-budget",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
    )
    gate = asyncio.Semaphore(1)
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=SimpleNamespace(kind="text", text='{"type":"response.created"}')),
            close=AsyncMock(),
        ),
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=upstream,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=gate,
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )

    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(
            proxy_request_budget_seconds=60.0,
            http_responses_session_bridge_request_budget_seconds=2222.0,
            stream_idle_timeout_seconds=300.0,
        ),
    )
    original_next_timeout = service._next_websocket_receive_timeout
    seen_budgets: list[float] = []

    async def record_next_timeout(*args: Any, **kwargs: Any):
        seen_budgets.append(kwargs["proxy_request_budget_seconds"])
        return await original_next_timeout(*args, **kwargs)

    monkeypatch.setattr(service, "_next_websocket_receive_timeout", record_next_timeout)
    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", AsyncMock(side_effect=RuntimeError("stop")))
    monkeypatch.setattr(service, "_write_request_log", AsyncMock())

    await service._relay_http_bridge_upstream_messages(session)

    assert seen_budgets == [2222.0]


@pytest.mark.asyncio
async def test_websocket_reader_unexpected_processing_error_fails_pending_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-ws-reader-crash",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport="websocket",
    )
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    request_state.response_create_gate_acquired = True
    pending_requests: deque[proxy_service._WebSocketRequestState] = deque([request_state])
    pending_lock = anyio.Lock()
    send_text = AsyncMock()
    websocket = cast(
        WebSocket,
        SimpleNamespace(send_text=send_text, send_bytes=AsyncMock(), close=AsyncMock()),
    )
    upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            receive=AsyncMock(return_value=SimpleNamespace(kind="text", text='{"type":"response.created"}')),
            close=AsyncMock(),
        ),
    )

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(service, "_process_upstream_websocket_text", AsyncMock(side_effect=RuntimeError("boom")))
    write_request_log = AsyncMock()
    monkeypatch.setattr(service, "_write_request_log", write_request_log)

    await service._relay_upstream_websocket_messages(
        websocket,
        upstream,
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        account_id_value="acc-1",
        pending_requests=pending_requests,
        pending_lock=pending_lock,
        client_send_lock=anyio.Lock(),
        api_key=None,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        response_create_gate=gate,
        proxy_request_budget_seconds=60.0,
        stream_idle_timeout_seconds=60.0,
        downstream_activity=proxy_service._DownstreamWebSocketActivity(),
    )

    send_text.assert_awaited()
    terminal_payload = send_text.await_args_list[0].args[0]
    assert '"code":"stream_incomplete"' in terminal_payload
    assert "reader" in terminal_payload
    assert list(pending_requests) == []
    write_request_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_maybe_touch_api_key_reservation_keeps_last_touch_when_touch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    api_key = _make_api_key(key_id="key-1", assigned_account_ids=[])
    reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="touch-fails",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    touch_usage_reservation = AsyncMock(side_effect=RuntimeError("db unavailable"))

    class _FakeApiKeysService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def touch_usage_reservation(self, reservation_id: str) -> bool:
            await touch_usage_reservation(reservation_id)
            return False

    class _RepoContext:
        async def __aenter__(self) -> Any:
            return cast(Any, SimpleNamespace(api_keys=cast(Any, object())))

        async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> bool:
            return False

    monkeypatch.setattr(proxy_service, "ApiKeysService", _FakeApiKeysService)
    monkeypatch.setattr(service, "_repo_factory", lambda: _RepoContext())
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 2000.0)

    result = await service._maybe_touch_api_key_reservation(
        api_key=api_key,
        reservation=reservation,
        last_touch_at=1000.0,
        request_id="req-1",
        surface="http_bridge",
    )

    assert result == 1000.0
    touch_usage_reservation.assert_awaited_once_with("touch-fails")


@pytest.mark.asyncio
async def test_maybe_touch_api_key_reservation_keeps_last_touch_when_reservation_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    api_key = _make_api_key(key_id="key-1", assigned_account_ids=[])
    reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="touch-missing",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    touch_usage_reservation = AsyncMock(return_value=False)

    class _FakeApiKeysService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def touch_usage_reservation(self, reservation_id: str) -> bool:
            return await touch_usage_reservation(reservation_id)

    class _RepoContext:
        async def __aenter__(self) -> Any:
            return cast(Any, SimpleNamespace(api_keys=cast(Any, object())))

        async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> bool:
            return False

    monkeypatch.setattr(proxy_service, "ApiKeysService", _FakeApiKeysService)
    monkeypatch.setattr(service, "_repo_factory", lambda: _RepoContext())
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 2000.0)

    result = await service._maybe_touch_api_key_reservation(
        api_key=api_key,
        reservation=reservation,
        last_touch_at=1000.0,
        request_id="req-1",
        surface="http_bridge",
    )

    assert result == 1000.0
    touch_usage_reservation.assert_awaited_once_with("touch-missing")


@pytest.mark.asyncio
async def test_api_key_reservation_background_heartbeat_touches_during_sparse_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    api_key = _make_api_key(key_id="key-sparse", assigned_account_ids=[])
    reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="sparse-reservation",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    stop_event = asyncio.Event()
    touch_state = proxy_service._ApiKeyReservationTouchState(last_touch_at=1.0)
    touch_calls = 0
    seen_last_touch_at: list[float] = []

    async def fake_maybe_touch(**kwargs: object) -> float:
        nonlocal touch_calls
        touch_calls += 1
        assert kwargs["api_key"] is api_key
        assert kwargs["reservation"] is reservation
        assert kwargs["request_id"] == "req-sparse"
        assert kwargs["surface"] == "stream"
        seen_last_touch_at.append(cast(float, kwargs["last_touch_at"]))
        stop_event.set()
        return cast(float, kwargs["last_touch_at"]) + 1.0

    monkeypatch.setattr(proxy_service, "_API_KEY_RESERVATION_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(service, "_maybe_touch_api_key_reservation", fake_maybe_touch)

    task = asyncio.create_task(
        service._run_api_key_reservation_heartbeat(
            api_key=api_key,
            reservation=reservation,
            touch_state=touch_state,
            request_id="req-sparse",
            surface="stream",
            stop_event=stop_event,
        )
    )
    touch_state.last_touch_at = 5.0
    await task

    assert touch_calls == 1
    assert seen_last_touch_at == [5.0]
    assert touch_state.last_touch_at == 6.0


@pytest.mark.asyncio
async def test_cancel_api_key_reservation_heartbeat_task_does_not_wait_for_task_completion() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_heartbeat() -> None:
        started.set()
        await release.wait()

    task = asyncio.create_task(blocked_heartbeat())
    await started.wait()

    service._cancel_api_key_reservation_heartbeat_task(task)
    await asyncio.sleep(0)

    assert task.cancelled()
