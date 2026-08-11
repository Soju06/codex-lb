"""Tests for the WebSocket model-source guard.

Model sources are only reachable from the HTTP request path, so the WebSocket
transport must refuse them. Two guards cover the two ways a turn can reach an
upstream:

* the connect guard, which fails the connect with a service-level ``503`` that
  Codex clients transparently fall back from onto HTTP;
* the reuse guard, which fails a later ``response.create`` that switches to a
  source-owned model on an already-open subscription upstream.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi import WebSocket

import app.modules.model_sources.selection as source_selection
import app.modules.proxy._service.websocket.mixin as ws_mixin
from app.modules.api_keys.service import ApiKeyData
from app.modules.model_sources.selection import (
    effective_model_for_api_key,
    responses_model_is_source_owned,
)
from app.modules.proxy import service as proxy_service
from tests.unit.test_proxy_utils import (
    _make_account,
    _make_proxy_settings,
    _QueuedTestUpstreamWebSocket,
    _repo_factory,
    _RequestLogsRecorder,
    _SettingsCache,
)

pytestmark = pytest.mark.unit


def _api_key(*, enforced_model: str | None = None) -> ApiKeyData:
    from datetime import datetime

    return ApiKeyData(
        id="key_ws_guard",
        name="ws guard",
        key_prefix="sk-test-ws-guard",
        allowed_models=[],
        enforced_model=enforced_model,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 1, 1),
        last_used_at=None,
    )


def _request_state(model: str) -> ws_mixin._WebSocketRequestState:
    return proxy_service._WebSocketRequestState(
        request_id="req-ws-guard",
        model=model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=anyio.current_time(),
    )


def test_effective_model_prefers_enforced_model() -> None:
    assert effective_model_for_api_key(None, "gpt-5.6-sol") == "gpt-5.6-sol"
    assert effective_model_for_api_key(_api_key(), "gpt-5.6-sol") == "gpt-5.6-sol"
    assert effective_model_for_api_key(_api_key(enforced_model="qwen3.8-max"), "gpt-5.6-sol") == "qwen3.8-max"


@pytest.mark.asyncio
async def test_source_ownership_fails_open_when_resolution_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A database failure must not be able to reject a subscription turn.

    The lookup runs after the turn's usage reservation is acquired but before it
    is registered for cleanup, so a propagating error would tear the session
    down and strand the reservation. Failing open degrades to the behaviour that
    existed before the guard.
    """

    async def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("model_sources table is unavailable")

    monkeypatch.setattr(source_selection, "select_responses_model_source", boom)

    assert await responses_model_is_source_owned("qwen3.8-max", None) is False


async def _run_connect_guard(
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_source_owned: bool,
    api_key: ApiKeyData | None = None,
    request_state_api_key: ApiKeyData | None = None,
):
    """Drive ``_connect_proxy_websocket`` far enough to observe the connect guard.

    ``_select_websocket_connect_account`` stands in for the failover loop the
    guard short-circuits, so reaching it means the guard did not fire.
    """
    emitted: dict[str, object] = {}
    selection_calls = 0
    seen_api_keys: list[ApiKeyData | None] = []

    async def fake_is_source_owned(model, key):  # noqa: ANN001
        seen_api_keys.append(key)
        return is_source_owned

    async def fake_emit(self, websocket, **kwargs):  # noqa: ANN001
        emitted.update(kwargs)

    async def fake_select(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        nonlocal selection_calls
        selection_calls += 1
        return None

    settings = _make_proxy_settings()
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(ws_mixin, "responses_model_is_source_owned", fake_is_source_owned)
    monkeypatch.setattr(proxy_service.ProxyService, "_emit_websocket_connect_failure", fake_emit)
    monkeypatch.setattr(proxy_service.ProxyService, "_select_websocket_connect_account", fake_select)

    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    request_state = _request_state("qwen3.8-max")
    request_state.api_key = request_state_api_key

    account, upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="capacity_weighted",
        model="qwen3.8-max",
        request_state=request_state,
        api_key=api_key,
        client_send_lock=anyio.Lock(),
        websocket=AsyncMock(),
    )
    return account, upstream, emitted, selection_calls, seen_api_keys


@pytest.mark.asyncio
async def test_connect_guard_fails_session_for_source_owned_model(monkeypatch: pytest.MonkeyPatch) -> None:
    account, upstream, emitted, selection_calls, _ = await _run_connect_guard(monkeypatch, is_source_owned=True)

    assert account is None
    assert upstream is None
    assert selection_calls == 0, "the guard must short-circuit before account selection"
    assert emitted["error_code"] == "model_source_requires_http_transport"
    assert emitted["status_code"] == 503, "a 4xx is terminal client-side and would strand the fallback"
    assert emitted["account_id"] is None


@pytest.mark.asyncio
async def test_connect_guard_ignores_subscription_models(monkeypatch: pytest.MonkeyPatch) -> None:
    account, _upstream, emitted, selection_calls, _ = await _run_connect_guard(monkeypatch, is_source_owned=False)

    assert account is None  # the stubbed selector returns no account
    assert selection_calls >= 1, "subscription models must proceed to account selection"
    assert emitted == {}


@pytest.mark.asyncio
async def test_connect_guard_uses_the_per_request_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A policy refresh mid-session must not be judged against the stale session key.

    ``request_state.api_key`` is refreshed per request; the session key captured
    at connect time can be arbitrarily old on a long-lived socket, and the reuse
    guard already consults the fresh one.
    """
    session_key = _api_key()
    refreshed_key = _api_key(enforced_model="qwen3.8-max")

    *_, seen_api_keys = await _run_connect_guard(
        monkeypatch,
        is_source_owned=True,
        api_key=session_key,
        request_state_api_key=refreshed_key,
    )

    assert seen_api_keys == [refreshed_key]


def _text_frame(payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        kind="text",
        text=json.dumps(payload, separators=(",", ":")),
        data=None,
        close_code=None,
        error=None,
        error_code=None,
    )


def _completed_turn(response_id: str) -> list[SimpleNamespace]:
    return [
        _text_frame({"type": "response.created", "response": {"id": response_id, "status": "in_progress"}}),
        _text_frame(
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "status": "completed",
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            }
        ),
    ]


class _Downstream:
    """A downstream socket that replays a scripted sequence of client frames."""

    def __init__(self, request_texts: list[str]) -> None:
        self.pending = list(request_texts)
        self.done = asyncio.Event()
        self.sent_text: list[str] = []
        self.turn_completed = asyncio.Event()

    async def receive(self) -> dict[str, object]:
        if self.pending:
            # Wait for the previous turn to settle so the frames stay ordered.
            if len(self.pending) < 1 or self.sent_text:
                await self.turn_completed.wait()
                self.turn_completed.clear()
            return {"type": "websocket.receive", "text": self.pending.pop(0)}
        await self.done.wait()
        return {"type": "websocket.disconnect"}

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        payload = json.loads(text)
        if payload.get("type") in {"response.completed", "response.failed", "error"}:
            self.turn_completed.set()
            if not self.pending:
                self.done.set()

    async def send_bytes(self, _data: bytes) -> None:
        return None

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        del code, reason
        self.done.set()


def _create_frame(model: str) -> str:
    return json.dumps(
        {
            "type": "response.create",
            "model": model,
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "stream": True,
        },
        separators=(",", ":"),
    )


@pytest.mark.asyncio
async def test_first_turn_reaches_connect_guard_not_the_reuse_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh socket must reach the connect guard, which emits the 503 that
    makes Codex clients fall back to HTTP.

    The per-frame reuse guard runs before connection, so if it were not gated on
    an already-open upstream it would emit a terminal ``invalid_request_error``
    for the very first ``response.create`` and preempt the fallback, leaving
    model sources unreachable.
    """
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_source_guard_first_turn")
    upstream = _QueuedTestUpstreamWebSocket(_completed_turn("resp_first_turn"))

    connect_called = False

    async def fake_connect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        nonlocal connect_called
        connect_called = True
        return account, upstream

    async def always_source_owned(*args, **kwargs) -> bool:  # noqa: ANN002, ANN003
        return True

    monkeypatch.setattr(ws_mixin, "responses_model_is_source_owned", always_source_owned)
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect)
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))

    downstream = _Downstream([_create_frame("qwen3.8-max")])

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert connect_called, "first turn must reach the connect path, not the per-frame reuse guard"
    assert not any("model_source_requires_http_transport" in text for text in downstream.sent_text), (
        "the reuse guard must not preempt the connect-path 503 on a fresh socket"
    )


@pytest.mark.asyncio
async def test_reuse_guard_rejects_a_later_source_owned_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second turn that switches to a source-owned model must not be forwarded.

    Socket reuse skips connection entirely, so without the reuse guard the frame
    would go to the subscription account already attached to the open upstream
    and be rejected by the backend with the unsupported-model error.
    """
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_source_guard_reuse")
    upstream = _QueuedTestUpstreamWebSocket(_completed_turn("resp_turn_one"))

    async def fake_connect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        return account, upstream

    # Only the second turn's model is source-owned.
    async def source_owned_for_qwen(model, _api_key):  # noqa: ANN001
        return model == "qwen3.8-max"

    released = AsyncMock()

    monkeypatch.setattr(ws_mixin, "responses_model_is_source_owned", source_owned_for_qwen)
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect)
    monkeypatch.setattr(proxy_service.ProxyService, "_release_websocket_request_state_reservation", released)
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))

    downstream = _Downstream([_create_frame("gpt-5.6-sol"), _create_frame("qwen3.8-max")])

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert any("resp_turn_one" in text for text in downstream.sent_text), "the subscription turn must complete"
    assert any("model_source_requires_http_transport" in text for text in downstream.sent_text), (
        "the source-owned turn must be rejected by the reuse guard"
    )
    assert len(upstream.sent_text) == 1, "the rejected turn must not be forwarded upstream"
    assert released.await_count >= 1, "the rejected turn must release its usage reservation"
