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
from contextlib import asynccontextmanager
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

    async def fake_is_source_owned(model, key, *, raw_model=None):  # noqa: ANN001
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
    async def source_owned_for_qwen(model, _api_key, *, raw_model=None):  # noqa: ANN001
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


def _alias_allowlist_api_key() -> ApiKeyData:
    """A key that allowlists exactly the alias an alias-named source exposes.

    ``validate_model_access`` resolves aliases on both sides, so this key also
    admits plain ``gpt-5`` requests — but ``select_responses_model_source``
    filters candidates against the allowlist *exactly*, which keeps the
    normalized ``gpt-5`` candidate away from source lookup. That makes the raw
    alias the only candidate that can match the source, in the unit fake, the
    integration database, and production alike.
    """
    from datetime import datetime

    return ApiKeyData(
        id="key_ws_guard_alias",
        name="ws guard alias",
        key_prefix="sk-test-ws-alias",
        allowed_models=["gpt-5-high"],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 1, 1),
        last_used_at=None,
    )


class _AliasSourceCatalog:
    """Fake only the I/O seams underneath ``select_responses_model_source``.

    The candidate construction in ``responses_model_is_source_owned`` and the
    allowlist/registry filtering in ``select_responses_model_source`` stay
    real; this stands in for the database session/repository and records which
    candidates were actually offered to the catalog, so tests can assert the
    raw client alias physically reached source selection (monkeypatching
    ``responses_model_is_source_owned`` itself would test the stub instead).
    """

    def __init__(self, source_models: set[str], disabled_source_models: set[str] | None = None) -> None:
        self.source_models = source_models
        self.disabled_source_models = disabled_source_models or set()
        self.seen_candidates: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        catalog = self

        class _FakeRepository:
            def __init__(self, _session: object) -> None:
                pass

            async def find_responses_source_for_model(
                self,
                candidate: str,
                *,
                allowed_source_ids=None,  # noqa: ANN001
                require_streaming: bool = False,
                only_disabled: bool = False,
            ):  # noqa: ANN202
                catalog.seen_candidates.append(candidate)
                # ``only_disabled`` selects the complement of the routable
                # set, mirroring the real repository's enablement filter.
                if only_disabled:
                    if candidate in catalog.disabled_source_models:
                        return SimpleNamespace(id="src_disabled", name="disabled-source", is_enabled=False)
                    return None
                if candidate in catalog.source_models:
                    return SimpleNamespace(id="src_alias", name="alias-source", is_enabled=True)
                return None

        @asynccontextmanager
        async def fake_session():  # noqa: ANN202
            yield object()

        monkeypatch.setattr(source_selection, "ModelSourcesRepository", _FakeRepository)
        monkeypatch.setattr(source_selection, "get_background_session", fake_session)
        monkeypatch.setattr(source_selection, "detach_session_objects", lambda _session: None)


class _TurnDrivenUpstream:
    """An upstream that releases each scripted turn only after its request.

    ``_QueuedTestUpstreamWebSocket`` queues every frame up front, which would
    let a second turn's events race ahead of the second ``response.create``.
    Here turn N's events become readable only after the Nth upstream send, so
    a turn that is (correctly) rejected before forwarding leaves its events
    unread, and a (buggy) forwarded turn completes cleanly instead of hanging
    the session — the pre-fix failure stays a crisp assertion failure.
    """

    def __init__(self, turns: list[list[SimpleNamespace]]) -> None:
        self._turns = list(turns)
        self._messages: asyncio.Queue[SimpleNamespace] = asyncio.Queue()
        self.close_seen = asyncio.Event()
        self.sent_text: list[str] = []

    def response_header(self, name: str) -> str | None:
        del name
        return None

    async def receive(self) -> SimpleNamespace:
        message = await self._messages.get()
        if message.kind == "close":
            self.close_seen.set()
        return message

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        if self._turns:
            for event in self._turns.pop(0):
                self._messages.put_nowait(event)

    async def send_bytes(self, _data: bytes) -> None:
        return None

    async def close(self) -> None:
        self.close_seen.set()


@pytest.mark.asyncio
async def test_reuse_guard_sees_the_raw_model_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later turn asking for an alias-only source model must be rejected.

    ``apply_api_key_enforcement`` normalizes ``gpt-5-high`` to ``gpt-5``
    during request preparation, so a guard that judges only
    ``request_state.model`` misses a source that exposes exactly
    ``gpt-5-high`` — while the HTTP path routes the identical request to the
    source via its pre-enforcement ``raw_source_model``. The real
    ``responses_model_is_source_owned`` and ``select_responses_model_source``
    run here; only the catalog I/O is faked (regression for the raw-alias P2).
    """
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    catalog = _AliasSourceCatalog({"gpt-5-high"})
    catalog.install(monkeypatch)

    api_key = _alias_allowlist_api_key()
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_source_guard_alias")
    upstream = _TurnDrivenUpstream([_completed_turn("resp_turn_one"), _completed_turn("resp_turn_two")])

    async def fake_connect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        return account, upstream

    async def fake_refresh(key):  # noqa: ANN001, ANN202
        return api_key

    released = AsyncMock()
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect)
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", fake_refresh)
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_service.ProxyService, "_release_websocket_request_state_reservation", released)
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))

    downstream = _Downstream([_create_frame("gpt-5"), _create_frame("gpt-5-high")])

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=api_key,
    )

    assert any("resp_turn_one" in text for text in downstream.sent_text), "the subscription turn must complete"
    assert "gpt-5-high" in catalog.seen_candidates, (
        "the client's raw alias must reach source selection; the normalized "
        "'gpt-5' is filtered out by the key's exact allowlist"
    )
    assert any("model_source_requires_http_transport" in text for text in downstream.sent_text), (
        "the alias-owned turn must be rejected by the reuse guard"
    )
    assert len(upstream.sent_text) == 1, "the alias turn must not be forwarded to the subscription upstream"
    assert released.await_count >= 1, "the rejected turn must release its usage reservation"


@pytest.mark.asyncio
async def test_connect_guard_sees_the_raw_model_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connect guard must judge the pre-enforcement alias too.

    The session loop hands ``_connect_proxy_websocket`` the post-enforcement
    ``request_state.model``, so the raw alias must ride on the prepared
    request state itself for the connect-time check to see it. This drives the
    real ``_prepare_websocket_response_create_request`` (where enforcement
    normalizes the alias) into the real connect guard.
    """
    settings = _make_proxy_settings()
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    catalog = _AliasSourceCatalog({"gpt-5-high"})
    catalog.install(monkeypatch)

    api_key = _alias_allowlist_api_key()
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))

    emitted: dict[str, object] = {}
    selection_calls = 0

    async def fake_refresh(key):  # noqa: ANN001, ANN202
        return api_key

    async def fake_emit(self, websocket, **kwargs):  # noqa: ANN001, ANN003, ANN202
        emitted.update(kwargs)

    async def fake_select(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        nonlocal selection_calls
        selection_calls += 1
        return None

    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", fake_refresh)
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_service.ProxyService, "_emit_websocket_connect_failure", fake_emit)
    monkeypatch.setattr(proxy_service.ProxyService, "_select_websocket_connect_account", fake_select)

    prepared = await service._prepare_websocket_response_create_request(
        json.loads(_create_frame("gpt-5-high")),
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=0,
        api_key=api_key,
    )
    assert prepared.request_state.model == "gpt-5", "enforcement is expected to normalize the alias"

    account, upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="capacity_weighted",
        # Exactly what the session loop passes: the normalized model.
        model=prepared.request_state.model,
        request_state=prepared.request_state,
        api_key=api_key,
        client_send_lock=anyio.Lock(),
        websocket=AsyncMock(),
    )

    assert account is None
    assert upstream is None
    assert selection_calls == 0, "the connect guard must short-circuit before account selection"
    assert emitted.get("error_code") == "model_source_requires_http_transport"
    assert emitted.get("status_code") == 503
    assert "gpt-5-high" in catalog.seen_candidates, "the raw alias must reach source selection on the connect path"


def _create_frame_with_input(model: str, input_items: list[dict[str, object]]) -> str:
    return json.dumps(
        {
            "type": "response.create",
            "model": model,
            "instructions": "",
            "input": input_items,
            "stream": True,
        },
        separators=(",", ":"),
    )


def _input_file_frame(model: str, file_id: str) -> str:
    return _create_frame_with_input(
        model,
        [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "summarize the attachment"},
                    {"type": "input_file", "file_id": file_id},
                ],
            }
        ],
    )


def _compaction_trigger_frame(model: str) -> str:
    return _create_frame_with_input(
        model,
        [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {"type": "compaction_trigger"},
        ],
    )


class _TurnSerializedDownstream:
    """A downstream that sends each next frame only after the prior turn ends.

    ``_Downstream`` hands the session loop the next frame as soon as it asks,
    so a second turn can be dispatched while the first turn's events are still
    in flight; the first turn's terminal frame then arrives with no pending
    client frames left and ends the session before the second turn's events
    come back. The rejection tests never notice — the guard fails the second
    turn synchronously inside the message loop — but the forwarding
    regressions below need the second turn's scripted upstream events to reach
    the client, so this downstream serializes turns the way a real Codex
    client does: it waits for a terminal frame before sending the next
    ``response.create``.
    """

    def __init__(self, request_texts: list[str]) -> None:
        self.pending = list(request_texts)
        self.done = asyncio.Event()
        self.sent_text: list[str] = []
        self.turn_completed = asyncio.Event()
        self._dispatched_any = False

    async def receive(self) -> dict[str, object]:
        if self.pending:
            if self._dispatched_any:
                await self.turn_completed.wait()
                self.turn_completed.clear()
            self._dispatched_any = True
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


@pytest.mark.asyncio
async def test_reuse_guard_forwards_a_pinned_input_file_turn(db_setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """A later source-owned turn that references an uploaded file must be forwarded.

    The HTTP route skips source selection whenever the input references an
    ``input_file`` — the upload is account-scoped, so the request is pinned to
    the subscription account that received it. The equivalent WebSocket turn
    must reach that account through the owner-routing path instead of being
    failed by the reuse guard (regression for the source-routing-exclusions
    P2).
    """
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_source_guard_file_pin")
    upstream = _TurnDrivenUpstream([_completed_turn("resp_turn_one"), _completed_turn("resp_turn_two")])

    async def fake_connect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        return account, upstream

    # The second turn's model is source-owned, like the reuse-guard rejection test.
    async def source_owned_for_qwen(model, _api_key, *, raw_model=None):  # noqa: ANN001
        return model == "qwen3.8-max"

    monkeypatch.setattr(ws_mixin, "responses_model_is_source_owned", source_owned_for_qwen)
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect)
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))

    await service._pin_file_account("file_ws_guard_pin", account.id)
    downstream = _TurnSerializedDownstream(
        [_create_frame("gpt-5.6-sol"), _input_file_frame("qwen3.8-max", "file_ws_guard_pin")]
    )

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert not any("model_source_requires_http_transport" in text for text in downstream.sent_text), (
        "HTTP excludes file-referencing requests from source routing, so the "
        "reuse guard must not fail the file-pinned turn"
    )
    assert len(upstream.sent_text) == 2, "the file-pinned turn must be forwarded to the pinned subscription account"
    assert any("resp_turn_two" in text for text in downstream.sent_text), "the file-pinned turn must complete"


@pytest.mark.asyncio
async def test_reuse_guard_forwards_a_terminal_compaction_trigger_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal compaction-trigger turn must stay on the subscription upstream.

    The HTTP route serves terminal compaction triggers through the upstream
    compact flow on the turn's owner account and never source-routes them, so
    the reuse guard must not fail the equivalent WebSocket turn even when its
    model is also exposed by a source (regression for the
    source-routing-exclusions P2).
    """
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_source_guard_compact")
    upstream = _TurnDrivenUpstream([_completed_turn("resp_turn_one"), _completed_turn("resp_turn_two")])

    async def fake_connect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        return account, upstream

    async def source_owned_for_qwen(model, _api_key, *, raw_model=None):  # noqa: ANN001
        return model == "qwen3.8-max"

    monkeypatch.setattr(ws_mixin, "responses_model_is_source_owned", source_owned_for_qwen)
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect)
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))

    downstream = _TurnSerializedDownstream([_create_frame("gpt-5.6-sol"), _compaction_trigger_frame("qwen3.8-max")])

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert not any("model_source_requires_http_transport" in text for text in downstream.sent_text), (
        "HTTP excludes terminal compaction triggers from source routing, so "
        "the reuse guard must not fail the compaction turn"
    )
    assert len(upstream.sent_text) == 2, "the compaction turn must be forwarded to the subscription upstream"
    assert any("resp_turn_two" in text for text in downstream.sent_text), "the compaction turn must complete"


async def _drive_prepared_request_into_connect_guard(
    monkeypatch: pytest.MonkeyPatch,
    frame: str,
):
    """Prepare ``frame`` for real and drive it into the real connect guard.

    Mirrors ``test_connect_guard_sees_the_raw_model_alias``: the alias catalog
    makes ``gpt-5-high`` genuinely source-owned, so before the exclusions fix
    the guard demonstrably fires for these frames — the stubbed account
    selector standing in for the failover loop proves the guard was skipped.
    """
    settings = _make_proxy_settings()
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    catalog = _AliasSourceCatalog({"gpt-5-high"})
    catalog.install(monkeypatch)

    api_key = _alias_allowlist_api_key()
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))

    emitted: dict[str, object] = {}
    selection_calls = 0

    async def fake_refresh(key):  # noqa: ANN001, ANN202
        return api_key

    async def fake_emit(self, websocket, **kwargs):  # noqa: ANN001, ANN003, ANN202
        emitted.update(kwargs)

    async def fake_select(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        nonlocal selection_calls
        selection_calls += 1
        return None

    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", fake_refresh)
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_service.ProxyService, "_emit_websocket_connect_failure", fake_emit)
    monkeypatch.setattr(proxy_service.ProxyService, "_select_websocket_connect_account", fake_select)

    prepared = await service._prepare_websocket_response_create_request(
        json.loads(frame),
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=0,
        api_key=api_key,
    )

    account, upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="capacity_weighted",
        model=prepared.request_state.model,
        request_state=prepared.request_state,
        api_key=api_key,
        client_send_lock=anyio.Lock(),
        websocket=AsyncMock(),
    )
    return prepared, account, upstream, emitted, lambda: selection_calls


@pytest.mark.asyncio
async def test_connect_guard_skips_input_file_requests(db_setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The connect guard must not bounce a file-referencing request to HTTP.

    An ``input_file`` reference excludes the request from source routing on
    the HTTP path — pinned or not, the upload lives on a subscription
    account — so the connect path must proceed to (owner-required) account
    selection instead of emitting the 503 fallback.
    """
    prepared, account, upstream, emitted, selection_calls = await _drive_prepared_request_into_connect_guard(
        monkeypatch,
        _input_file_frame("gpt-5-high", "file_ws_connect_unpinned"),
    )

    assert emitted == {}, "the connect guard must not emit the 503 HTTP-fallback failure"
    assert selection_calls() >= 1, "file-referencing requests must proceed to account selection"
    assert account is None  # the stubbed selector returns no account
    assert upstream is None
    assert prepared.request_state.source_route_excluded is True, (
        "preparation must record the HTTP source-route exclusion on the request state"
    )


@pytest.mark.asyncio
async def test_connect_guard_skips_terminal_compaction_trigger_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connect guard must not bounce a terminal compaction trigger to HTTP.

    HTTP serves these through the compact flow on the owner account and never
    source-routes them; the WebSocket connect path must likewise proceed to
    account selection.
    """
    prepared, account, upstream, emitted, selection_calls = await _drive_prepared_request_into_connect_guard(
        monkeypatch,
        _compaction_trigger_frame("gpt-5-high"),
    )

    assert emitted == {}, "the connect guard must not emit the 503 HTTP-fallback failure"
    assert selection_calls() >= 1, "compaction-trigger requests must proceed to account selection"
    assert account is None  # the stubbed selector returns no account
    assert upstream is None
    assert prepared.request_state.source_route_excluded is True, (
        "preparation must record the HTTP source-route exclusion on the request state"
    )


@pytest.mark.asyncio
async def test_source_ownership_counts_disabled_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model whose only source is disabled is still source-owned.

    The WebSocket transport cannot serve a source-owned model whether the
    source is enabled or not; dispatching it to a subscription account instead
    yields the opaque upstream 400. Recognizing disabled ownership makes the
    guards bounce the turn to HTTP, where the disabled-source denial answers
    503 ``model_source_disabled``.
    """
    catalog = _AliasSourceCatalog(set(), disabled_source_models={"qwen3.8-max"})
    catalog.install(monkeypatch)

    assert await responses_model_is_source_owned("qwen3.8-max", None) is True
    assert await responses_model_is_source_owned("model-nobody-configured", None) is False, (
        "a model no source claims must keep falling through to subscription selection"
    )


@pytest.mark.asyncio
async def test_connect_guard_fails_session_for_disabled_source_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connect guard must bounce a disabled-source model to HTTP.

    The real ``responses_model_is_source_owned`` runs against a catalog where
    the model's only source is disabled: without the ``only_disabled`` probe
    the guard misses, the turn dispatches to a subscription account, and the
    incident's entitlement 400 comes back on exactly this transport.
    """
    settings = _make_proxy_settings()
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    catalog = _AliasSourceCatalog(set(), disabled_source_models={"qwen3.8-max"})
    catalog.install(monkeypatch)

    emitted: dict[str, object] = {}
    selection_calls = 0

    async def fake_emit(self, websocket, **kwargs):  # noqa: ANN001, ANN003, ANN202
        emitted.update(kwargs)

    async def fake_select(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        nonlocal selection_calls
        selection_calls += 1
        return None

    monkeypatch.setattr(proxy_service.ProxyService, "_emit_websocket_connect_failure", fake_emit)
    monkeypatch.setattr(proxy_service.ProxyService, "_select_websocket_connect_account", fake_select)

    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))

    account, upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="capacity_weighted",
        model="qwen3.8-max",
        request_state=_request_state("qwen3.8-max"),
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=AsyncMock(),
    )

    assert account is None
    assert upstream is None
    assert selection_calls == 0, "the guard must short-circuit before subscription account selection"
    assert emitted.get("error_code") == "model_source_requires_http_transport"
    assert emitted.get("status_code") == 503, "a 4xx is terminal client-side and would strand the HTTP fallback"
    assert "qwen3.8-max" in catalog.seen_candidates, "the disabled-source probe must reach the catalog"


@pytest.mark.asyncio
async def test_reuse_guard_rejects_a_later_disabled_source_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later turn that switches to a disabled-source model must not be forwarded.

    Socket reuse bypasses connect-time selection, so without disabled-source
    recognition in the reuse guard the frame goes to the subscription account
    already attached to the open upstream and is rejected by the backend with
    the unsupported-model 400.
    """
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    catalog = _AliasSourceCatalog(set(), disabled_source_models={"qwen3.8-max"})
    catalog.install(monkeypatch)

    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_source_guard_disabled_reuse")
    upstream = _QueuedTestUpstreamWebSocket(_completed_turn("resp_turn_one"))

    async def fake_connect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        return account, upstream

    released = AsyncMock()

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
        "the disabled-source turn must be rejected by the reuse guard"
    )
    assert len(upstream.sent_text) == 1, "the rejected turn must not be forwarded upstream"
    assert released.await_count >= 1, "the rejected turn must release its usage reservation"
