"""Pre-response-start HTTP bridge failure semantics.

The bridge used to report silence *before* ``response.created`` as
``stream_idle_timeout`` (a post-start budget) and to settle its own local
recovery resets with upstream-close wording. These regressions pin the split:
``bridge_eventless_timeout`` for the pre-response phase, ``stream_idle_timeout``
only after a response event, honest local-reset messages, and an explicit
marker for upstream frames that prove liveness but match no pending request.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import time
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.core.config.settings import Settings
from app.core.openai.models import OpenAIError
from app.db.models import AccountStatus
from app.modules.proxy import api as proxy_api_module
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import helpers as http_bridge_helpers_module
from app.modules.proxy._service.http_bridge import streaming as http_bridge_streaming_module

pytestmark = pytest.mark.unit


def _make_bridge_session(
    *,
    key_value: str = "bridge-eventless",
    pending_requests: deque[proxy_service._WebSocketRequestState] | None = None,
    queued_request_count: int = 0,
) -> proxy_service._HTTPBridgeSession:
    return proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", key_value, None),
        headers={"x-codex-session-id": key_value},
        affinity=proxy_service._AffinityPolicy(
            key=key_value,
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.2",
        account=cast(Any, SimpleNamespace(id="acc-bridge", status=AccountStatus.ACTIVE, plan_type="plus")),
        upstream=cast(UpstreamWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=pending_requests or deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=queued_request_count,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


def _eventless_settings(
    *,
    keepalive_interval_seconds: float = 0.001,
    stuck_gate_retire_after_seconds: float = 0.002,
    stream_idle_timeout_seconds: float = 7200.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        sse_keepalive_interval_seconds=keepalive_interval_seconds,
        stream_idle_timeout_seconds=stream_idle_timeout_seconds,
        http_responses_session_bridge_stuck_gate_retire_after_seconds=stuck_gate_retire_after_seconds,
        http_responses_session_bridge_request_budget_seconds=60.0,
        http_responses_session_bridge_anchor_poison_failure_threshold=7,
    )


def test_http_bridge_eventless_budget_is_named_and_settings_derived() -> None:
    """Fix 2: the pre-response budget is derived, not an implicit 6 x 10s."""

    settings = Settings()
    budget_seconds = http_bridge_helpers_module._http_bridge_eventless_budget_seconds(
        settings,
        fallback_seconds=60.0,
    )
    keepalive_interval = settings.sse_keepalive_interval_seconds
    stuck_gate = settings.http_responses_session_bridge_stuck_gate_retire_after_seconds

    # Timeout invariants relating the knobs the audit named.
    assert budget_seconds <= stuck_gate
    assert budget_seconds <= settings.stream_idle_timeout_seconds
    assert budget_seconds <= settings.http_responses_session_bridge_request_budget_seconds
    assert budget_seconds >= keepalive_interval

    # At shipped defaults the pre-response budget is the owner-side stuck gate
    # (300s), not the old implicit _STREAM_KEEPALIVE_MAX_COUNT * interval (60s).
    assert budget_seconds == stuck_gate
    max_count = http_bridge_helpers_module._http_bridge_eventless_max_keepalive_count(
        settings,
        keepalive_interval_seconds=keepalive_interval,
        floor_count=http_bridge_streaming_module._stream_keepalive_max_count(),
    )
    assert max_count * keepalive_interval >= budget_seconds
    assert max_count > http_bridge_streaming_module._stream_keepalive_max_count()

    # A shorter stream idle timeout or request budget still clamps it down.
    # When the budget spans fewer than floor_count intervals, the count follows
    # the budget (5 x 10s covers 45s) so the watchdog never outlives it — the
    # floor only applies when the budget is at least floor_count intervals.
    tight = SimpleNamespace(
        sse_keepalive_interval_seconds=10.0,
        stream_idle_timeout_seconds=45.0,
        http_responses_session_bridge_stuck_gate_retire_after_seconds=300.0,
        http_responses_session_bridge_request_budget_seconds=600.0,
    )
    assert http_bridge_helpers_module._http_bridge_eventless_budget_seconds(tight, fallback_seconds=60.0) == 45.0
    assert (
        http_bridge_helpers_module._http_bridge_eventless_max_keepalive_count(
            tight,
            keepalive_interval_seconds=10.0,
            floor_count=6,
        )
        == 5
    )


@pytest.mark.asyncio
async def test_http_bridge_pre_response_silence_is_bridge_eventless_timeout(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fix 1: pre-response-start kills are not stream_idle_timeout anywhere."""

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _eventless_settings())
    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_http_bridge_precreated_retry_cooldown_seconds", AsyncMock(return_value=0.0))
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(
            lookup_retry_circuit=AsyncMock(return_value=None),
            persist_retry_circuit=AsyncMock(return_value=None),
        ),
    )

    session = _make_bridge_session(key_value="sid-eventless-classification")
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-eventless-classification",
        model="gpt-5.6-luna",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
        request_text='{"type":"response.create"}',
    )

    async def submit(target_session: Any, *, request_state: Any, **kwargs: Any) -> None:
        del kwargs
        target_session.pending_requests.append(request_state)

    monkeypatch.setattr(service, "_submit_http_bridge_request", submit)

    # An unmatched-but-live upstream frame arrived before the kill; the kill
    # must report it so a local matching wedge is not read as a silent upstream.
    session.unmatched_upstream_liveness_count = 2

    with caplog.at_level(logging.INFO, logger="app.modules.proxy.service"):
        events = [
            event
            async for event in service._stream_http_bridge_session_events(
                session,
                request_state=request_state,
                text_data='{"type":"response.create"}',
                queue_limit=8,
                propagate_http_errors=False,
                downstream_turn_state=None,
            )
        ]

    terminal = cast(dict[str, Any], proxy_service.parse_sse_data_json(events[-1]))
    assert terminal["type"] == "response.failed"
    error = cast(dict[str, Any], cast(dict[str, Any], terminal["response"])["error"])
    assert error["code"] == "bridge_eventless_timeout"
    # Honest message: no upstream blame, and unmatched upstream liveness means
    # the retry cannot be promised duplicate-safe.
    assert "Upstream" not in cast(str, error["message"])
    assert "retry may duplicate upstream work" in cast(str, error["message"])

    # Durable request-log detail.
    assert request_state.failure_detail_override == "bridge_eventless_timeout"
    assert request_state.failure_phase_override == "bridge"

    # Retry-circuit last_detail keeps the specific class instead of being
    # collapsed onto stream_idle_timeout.
    circuit_state = cast(Any, service)._http_bridge_retry_circuits[session.key]
    assert circuit_state.last_detail == "bridge_eventless_timeout"

    # Logs.
    messages = [record.getMessage() for record in caplog.records]
    assert any("HTTP bridge eventless timeout request_id=req-eventless-classification" in m for m in messages)
    assert any("unmatched_upstream_liveness=2" in m for m in messages)
    assert not any("HTTP bridge stream idle timeout" in m for m in messages)

    # Client-visible shape stays a retryable 503, not a 502 upstream blame.
    assert proxy_api_module._status_for_error(OpenAIError(code="bridge_eventless_timeout")) == 503


@pytest.mark.asyncio
async def test_http_bridge_eventless_timeout_without_liveness_promises_safe_retry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Zero unmatched liveness keeps the unconditional safe-to-retry contract.

    Companion to the count=2 caution case above: with no unmatched upstream
    frames the terminal message must state the request is safe to retry, so an
    unconditional caution string cannot silently replace the safe wording.
    """

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _eventless_settings())
    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_http_bridge_precreated_retry_cooldown_seconds", AsyncMock(return_value=0.0))
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(
            lookup_retry_circuit=AsyncMock(return_value=None),
            persist_retry_circuit=AsyncMock(return_value=None),
        ),
    )

    session = _make_bridge_session(key_value="sid-eventless-safe-retry")
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-eventless-safe-retry",
        model="gpt-5.6-luna",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
        request_text='{"type":"response.create"}',
    )

    async def submit(target_session: Any, *, request_state: Any, **kwargs: Any) -> None:
        del kwargs
        target_session.pending_requests.append(request_state)

    monkeypatch.setattr(service, "_submit_http_bridge_request", submit)

    assert session.unmatched_upstream_liveness_count == 0

    with caplog.at_level(logging.INFO, logger="app.modules.proxy.service"):
        events = [
            event
            async for event in service._stream_http_bridge_session_events(
                session,
                request_state=request_state,
                text_data='{"type":"response.create"}',
                queue_limit=8,
                propagate_http_errors=False,
                downstream_turn_state=None,
            )
        ]

    terminal = cast(dict[str, Any], proxy_service.parse_sse_data_json(events[-1]))
    assert terminal["type"] == "response.failed"
    error = cast(dict[str, Any], cast(dict[str, Any], terminal["response"])["error"])
    assert error["code"] == "bridge_eventless_timeout"
    assert error["message"] == http_bridge_helpers_module._HTTP_BRIDGE_EVENTLESS_TIMEOUT_MESSAGE
    assert "safe to retry" in cast(str, error["message"])
    assert "retry may duplicate upstream work" not in cast(str, error["message"])

    messages = [record.getMessage() for record in caplog.records]
    assert any("unmatched_upstream_liveness=0" in m for m in messages)


@pytest.mark.asyncio
async def test_http_bridge_post_response_start_still_uses_stream_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fix 1 counterpart: after response.created the old classification stands."""

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _eventless_settings(stream_idle_timeout_seconds=0.002),
    )
    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    session = _make_bridge_session(key_value="sid-post-start-idle")
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-post-start-idle",
        model="gpt-5.6-luna",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
        response_id="resp-post-start-idle",
    )
    request_state.response_event_count = 3

    async def submit(target_session: Any, *, request_state: Any, **kwargs: Any) -> None:
        del kwargs
        target_session.pending_requests.append(request_state)

    monkeypatch.setattr(service, "_submit_http_bridge_request", submit)

    with caplog.at_level(logging.INFO, logger="app.modules.proxy.service"):
        events = [
            event
            async for event in service._stream_http_bridge_session_events(
                session,
                request_state=request_state,
                text_data='{"type":"response.create"}',
                queue_limit=8,
                propagate_http_errors=False,
                downstream_turn_state=None,
            )
        ]

    terminal = cast(dict[str, Any], proxy_service.parse_sse_data_json(events[-1]))
    error = cast(dict[str, Any], cast(dict[str, Any], terminal["response"])["error"])
    assert error["code"] == "stream_idle_timeout"
    assert request_state.failure_detail_override is None
    messages = [record.getMessage() for record in caplog.records]
    assert any("HTTP bridge stream idle timeout request_id=req-post-start-idle" in m for m in messages)

    # The post-start budget is still the configured stream idle timeout, not the
    # pre-response eventless budget.
    live_settings = Settings()
    post_start_count = max(
        http_bridge_streaming_module._stream_keepalive_max_count(),
        math.ceil(live_settings.stream_idle_timeout_seconds / live_settings.sse_keepalive_interval_seconds),
    )
    assert post_start_count * live_settings.sse_keepalive_interval_seconds >= 7200.0


def test_http_bridge_local_resets_do_not_blame_the_upstream() -> None:
    """Fix 4: grep-style assertion over the bridge streaming module."""

    source = inspect.getsource(http_bridge_streaming_module)
    assert "Upstream websocket closed" not in source

    reset_call = "await self._reset_http_bridge_session_after_local_terminal_error("
    call_count = source.count(reset_call)
    # Pinned to the number of local-reset call sites on current main so a new
    # site that reintroduces upstream-blaming wording cannot slip in unnoticed.
    assert call_count == 5
    for chunk in source.split(reset_call)[1:]:
        call_body = chunk.split(")\n", 1)[0]
        assert "_HTTP_BRIDGE_LOCAL_RESET_MESSAGE" in call_body

    assert "Upstream" not in http_bridge_streaming_module._HTTP_BRIDGE_LOCAL_RESET_MESSAGE
    assert "locally" in http_bridge_streaming_module._HTTP_BRIDGE_LOCAL_RESET_MESSAGE


@pytest.mark.asyncio
async def test_http_bridge_unmatched_upstream_frame_records_liveness_marker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fix 3: unmatched-but-live upstream frames get an explicit marker."""

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-unmatched-liveness",
        model="gpt-5.6-luna",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
    )
    session = _make_bridge_session(
        key_value="sid-unmatched-liveness",
        pending_requests=deque([request_state]),
        queued_request_count=1,
    )

    with caplog.at_level(logging.INFO, logger="app.modules.proxy.service"):
        await service._process_http_bridge_upstream_text(
            session,
            json.dumps(
                {"type": "response.output_text.delta", "response_id": "resp-not-ours", "delta": "hi"},
                separators=(",", ":"),
            ),
        )

    assert session.unmatched_upstream_liveness_count == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any("http_bridge_event event=unmatched_upstream_liveness" in m for m in messages)
    assert any("unmatched_upstream_liveness=1" in m for m in messages)

    caplog.clear()
    # Locally injected keepalives prove nothing about the upstream.
    with caplog.at_level(logging.INFO, logger="app.modules.proxy.service"):
        await service._process_http_bridge_upstream_text(
            session,
            json.dumps({"type": "codex.keepalive"}, separators=(",", ":")),
        )

    assert session.unmatched_upstream_liveness_count == 1
    keepalive_messages = [record.getMessage() for record in caplog.records]
    assert not any("http_bridge_event event=unmatched_upstream_liveness" in m for m in keepalive_messages)
    assert http_bridge_helpers_module._http_bridge_event_proves_upstream_liveness("codex.keepalive") is False
    assert http_bridge_helpers_module._http_bridge_event_proves_upstream_liveness("response.created") is True
