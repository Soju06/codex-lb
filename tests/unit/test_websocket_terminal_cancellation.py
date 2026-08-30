from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi import WebSocket

from app.core import shutdown as shutdown_state
from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.core.utils.time import utcnow
from app.db.models import Account
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.websocket import mixin as websocket_mixin

pytestmark = pytest.mark.unit


class _RequestLogsRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def add_log(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


class _TerminalThenBlockingUpstream:
    def __init__(self, terminal_text: str) -> None:
        self._terminal_text = terminal_text
        self._terminal_sent = False
        self._closed = asyncio.Event()

    async def receive(self) -> SimpleNamespace:
        if not self._terminal_sent:
            self._terminal_sent = True
            return SimpleNamespace(
                kind="text",
                text=self._terminal_text,
                data=None,
                close_code=None,
                error=None,
            )
        await self._closed.wait()
        return SimpleNamespace(kind="close", text=None, data=None, close_code=1000, error=None)

    async def close(self) -> None:
        self._closed.set()


class _DownstreamWebSocket:
    def __init__(self) -> None:
        self.sent_text: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_bytes(self, _data: bytes) -> None:
        return None

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        del code, reason


def _request_state(
    request_id: str,
    *,
    response_create_sent_at: float | None = None,
) -> proxy_service._WebSocketRequestState:
    return proxy_service._WebSocketRequestState(
        request_id=request_id,
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=asyncio.get_running_loop().time(),
        response_create_sent_at=response_create_sent_at,
    )


@pytest.mark.asyncio
async def test_empty_transport_end_partition_leaves_late_unsent_append_sender_owned() -> None:
    pending_requests: deque[proxy_service._WebSocketRequestState] = deque()
    pending_lock = anyio.Lock()

    reader_owned = await websocket_mixin._claim_sent_websocket_requests_for_reader(
        pending_requests,
        pending_lock=pending_lock,
    )
    late_unsent = _request_state("request_late_unsent")
    async with pending_lock:
        pending_requests.append(late_unsent)

    assert reader_owned == deque()
    assert pending_requests == deque([late_unsent])


@pytest.mark.asyncio
async def test_transport_end_partition_claims_only_sent_states_from_mixed_queue() -> None:
    sent_first = _request_state("request_sent_first", response_create_sent_at=1.0)
    unsent_first = _request_state("request_unsent_first")
    sent_second = _request_state("request_sent_second", response_create_sent_at=2.0)
    unsent_second = _request_state("request_unsent_second")
    pending_requests = deque([sent_first, unsent_first, sent_second, unsent_second])

    reader_owned = await websocket_mixin._claim_sent_websocket_requests_for_reader(
        pending_requests,
        pending_lock=anyio.Lock(),
    )

    assert reader_owned == deque([sent_first, sent_second])
    assert pending_requests == deque([unsent_first, unsent_second])


@pytest.mark.asyncio
async def test_transport_end_replay_requires_send_boundary_only_for_direct_websocket() -> None:
    request_text = '{"type":"response.create","input":"test"}'
    direct_websocket = _request_state("request_direct_unsent")
    direct_websocket.request_text = request_text
    direct_websocket.awaiting_response_created = True
    direct_pending = deque([direct_websocket])

    direct_replay = await proxy_service._pop_replayable_precreated_websocket_request_state(
        direct_pending,
        pending_lock=anyio.Lock(),
    )

    http_bridge = _request_state("request_http_bridge")
    http_bridge.transport = "http"
    http_bridge.request_text = request_text
    http_bridge.awaiting_response_created = True
    http_pending = deque([http_bridge])

    http_replay = await proxy_service._pop_replayable_precreated_websocket_request_state(
        http_pending,
        pending_lock=anyio.Lock(),
    )

    assert direct_replay is None
    assert direct_pending == deque([direct_websocket])
    assert http_replay is http_bridge
    assert http_pending == deque()


@pytest.mark.asyncio
async def test_cancelled_websocket_scope_cleanup_is_deadline_bounded_and_remains_drain_owned(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=0,
        prohibit_fast_mode=False,
        proxy_downstream_websocket_idle_timeout_seconds=30.0,
        proxy_request_budget_seconds=30.0,
        stream_idle_timeout_seconds=30.0,
        sse_keepalive_interval_seconds=0.0,
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return settings

    request_text = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-5.6-sol",
            "input": "pending cleanup",
        },
        separators=(",", ":"),
    )
    request_state = _request_state("request_pending_cleanup")
    request_state.request_text = request_text
    request_sent = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_request_ids: list[str] = []

    class _BlockingDownstreamWebSocket:
        def __init__(self) -> None:
            self._received = False

        async def receive(self) -> dict[str, object]:
            if not self._received:
                self._received = True
                return {"type": "websocket.receive", "text": request_text}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send_text(self, _text: str) -> None:
            return None

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason

    class _PendingUpstream:
        async def send_text(self, _text: str) -> None:
            request_sent.set()

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("binary send is not expected")

        async def close(self) -> None:
            return None

    upstream = _PendingUpstream()

    async def prepare_request(*_args: object, **_kwargs: object) -> proxy_service._PreparedWebSocketRequest:
        return proxy_service._PreparedWebSocketRequest(
            text_data=request_text,
            request_state=request_state,
            affinity_policy=proxy_service._AffinityPolicy(),
        )

    async def acquire_admission(
        state: proxy_service._WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        state.response_create_gate = response_create_gate
        await response_create_gate.acquire()
        state.response_create_gate_acquired = True
        state.awaiting_response_created = True

    async def connect_upstream(*_args: object, **_kwargs: object) -> tuple[Account, UpstreamWebSocket]:
        account = cast(Account, SimpleNamespace(id="account_pending_cleanup", codex_installation_id=None))
        return account, cast(UpstreamWebSocket, upstream)

    async def relay_until_cancelled(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    async def block_cleanup(
        *_args: object,
        pending_requests: deque[proxy_service._WebSocketRequestState],
        **_kwargs: object,
    ) -> None:
        cleanup_request_ids.extend(state.request_id for state in pending_requests)
        cleanup_started.set()
        try:
            await release_cleanup.wait()
        except asyncio.CancelledError:
            cleanup_cancelled.set()
            raise

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_routing_strategy", lambda _settings: "usage_weighted")
    monkeypatch.setattr(proxy_service, "_enforce_response_create_size_limit", lambda _request_state: None)
    monkeypatch.setattr(websocket_mixin, "effective_account_concurrency_caps", lambda _settings: object())
    monkeypatch.setattr(service, "_websocket_continuity_state_for_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_prepare_websocket_response_create_request", prepare_request)
    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", acquire_admission)
    monkeypatch.setattr(service, "_connect_proxy_websocket", connect_upstream)
    monkeypatch.setattr(service, "_relay_upstream_websocket_messages", relay_until_cancelled)
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", AsyncMock(return_value=object()))
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", block_cleanup)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", AsyncMock())

    scope_task = asyncio.create_task(
        service.proxy_responses_websocket(
            cast(WebSocket, _BlockingDownstreamWebSocket()),
            {},
            codex_session_affinity=False,
            openai_cache_affinity=False,
            api_key=None,
        )
    )
    await asyncio.wait_for(request_sent.wait(), timeout=1)

    caplog.set_level(logging.WARNING)
    shutdown_state.commit_shutdown(timeout_seconds=0.1)
    started_at = asyncio.get_running_loop().time()
    scope_task.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    await asyncio.sleep(0.01)
    assert scope_task.done() is False

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(scope_task, timeout=1)
    elapsed = asyncio.get_running_loop().time() - started_at

    assert cleanup_cancelled.is_set() is False
    assert cleanup_request_ids == [request_state.request_id]
    assert 0.05 <= elapsed < 0.3
    assert any(
        task.get_name() == "proxy-websocket-finalization-scope-cleanup"
        for task in service._background_cleanup_tasks
        if not task.done()
    )
    assert any(
        "Websocket scope cleanup exceeded its cleanup budget" in message and "cleanup_phase=pending_requests" in message
        for message in caplog.messages
    )

    persistence_drain = asyncio.create_task(service.drain_persistence_tasks(timeout_seconds=1))
    await asyncio.sleep(0)
    assert persistence_drain.done() is False
    release_cleanup.set()
    assert await asyncio.wait_for(persistence_drain, timeout=1)
    assert service._background_cleanup_tasks == set()


@pytest.mark.asyncio
async def test_cancelled_websocket_scope_cleanup_draws_on_published_post_drain_reserve(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exhausted drain must not abandon terminal scope settlement.

    Once the server publishes its post-drain cleanup reserve, the scope
    cleanup wait draws on the shared drain-plus-reserve remainder, so the
    settlement completes inside the reserve instead of being left pending
    with a zero budget at the drain deadline.
    """

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=0,
        prohibit_fast_mode=False,
        proxy_downstream_websocket_idle_timeout_seconds=30.0,
        proxy_request_budget_seconds=30.0,
        stream_idle_timeout_seconds=30.0,
        sse_keepalive_interval_seconds=0.0,
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return settings

    request_text = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-5.6-sol",
            "input": "reserve settlement",
        },
        separators=(",", ":"),
    )
    request_state = _request_state("request_reserve_settlement")
    request_state.request_text = request_text
    request_sent = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_request_ids: list[str] = []

    class _BlockingDownstreamWebSocket:
        def __init__(self) -> None:
            self._received = False

        async def receive(self) -> dict[str, object]:
            if not self._received:
                self._received = True
                return {"type": "websocket.receive", "text": request_text}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send_text(self, _text: str) -> None:
            return None

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason

    class _PendingUpstream:
        async def send_text(self, _text: str) -> None:
            request_sent.set()

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("binary send is not expected")

        async def close(self) -> None:
            return None

    upstream = _PendingUpstream()

    async def prepare_request(*_args: object, **_kwargs: object) -> proxy_service._PreparedWebSocketRequest:
        return proxy_service._PreparedWebSocketRequest(
            text_data=request_text,
            request_state=request_state,
            affinity_policy=proxy_service._AffinityPolicy(),
        )

    async def acquire_admission(
        state: proxy_service._WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        state.response_create_gate = response_create_gate
        await response_create_gate.acquire()
        state.response_create_gate_acquired = True
        state.awaiting_response_created = True

    async def connect_upstream(*_args: object, **_kwargs: object) -> tuple[Account, UpstreamWebSocket]:
        account = cast(Account, SimpleNamespace(id="account_reserve_settlement", codex_installation_id=None))
        return account, cast(UpstreamWebSocket, upstream)

    async def relay_until_cancelled(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    async def block_cleanup(
        *_args: object,
        pending_requests: deque[proxy_service._WebSocketRequestState],
        **_kwargs: object,
    ) -> None:
        cleanup_request_ids.extend(state.request_id for state in pending_requests)
        cleanup_started.set()
        try:
            await release_cleanup.wait()
        except asyncio.CancelledError:
            cleanup_cancelled.set()
            raise

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_routing_strategy", lambda _settings: "usage_weighted")
    monkeypatch.setattr(proxy_service, "_enforce_response_create_size_limit", lambda _request_state: None)
    monkeypatch.setattr(websocket_mixin, "effective_account_concurrency_caps", lambda _settings: object())
    monkeypatch.setattr(service, "_websocket_continuity_state_for_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_prepare_websocket_response_create_request", prepare_request)
    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", acquire_admission)
    monkeypatch.setattr(service, "_connect_proxy_websocket", connect_upstream)
    monkeypatch.setattr(service, "_relay_upstream_websocket_messages", relay_until_cancelled)
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", AsyncMock(return_value=object()))
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", block_cleanup)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", AsyncMock())

    scope_task = asyncio.create_task(
        service.proxy_responses_websocket(
            cast(WebSocket, _BlockingDownstreamWebSocket()),
            {},
            codex_session_affinity=False,
            openai_cache_affinity=False,
            api_key=None,
        )
    )
    await asyncio.wait_for(request_sent.wait(), timeout=1)

    caplog.set_level(logging.WARNING)
    shutdown_state.commit_shutdown(timeout_seconds=0.05)
    shutdown_state.set_post_drain_cleanup_timeout_seconds(5.0)
    scope_task.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    # Let the drain deadline expire while terminal settlement is still blocked;
    # the scope wait must keep waiting on the published reserve.
    await asyncio.sleep(0.15)
    assert scope_task.done() is False
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(scope_task, timeout=1)
    await asyncio.sleep(0)

    assert cleanup_cancelled.is_set() is False
    assert cleanup_request_ids == [request_state.request_id]
    assert not any("Websocket scope cleanup exceeded its cleanup budget" in message for message in caplog.messages)
    assert service._background_cleanup_tasks == set()


@pytest.mark.asyncio
async def test_normal_websocket_scope_cleanup_uses_separate_scope_budget(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=0,
        prohibit_fast_mode=False,
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return settings

    receive_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    class _BlockingDownstreamWebSocket:
        async def receive(self) -> dict[str, object]:
            receive_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason

    async def block_cleanup(*_args: object, **_kwargs: object) -> None:
        cleanup_started.set()
        await release_cleanup.wait()

    monkeypatch.setattr(proxy_service, "_TASK_CANCEL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(websocket_mixin, "_WEBSOCKET_SCOPE_CLEANUP_TIMEOUT_SECONDS", 0.08)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(proxy_downstream_websocket_idle_timeout_seconds=30.0),
    )
    monkeypatch.setattr(proxy_service, "_routing_strategy", lambda _settings: "usage_weighted")
    monkeypatch.setattr(service, "_websocket_continuity_state_for_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", block_cleanup)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", AsyncMock())
    caplog.set_level(logging.WARNING)

    scope_task = asyncio.create_task(
        service.proxy_responses_websocket(
            cast(WebSocket, _BlockingDownstreamWebSocket()),
            {},
            codex_session_affinity=False,
            openai_cache_affinity=False,
            api_key=None,
        )
    )
    await asyncio.wait_for(receive_started.wait(), timeout=1)

    scope_task.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    await asyncio.sleep(0.03)
    assert scope_task.done() is False
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(scope_task, timeout=0.5)
    await asyncio.sleep(0)

    assert not any(
        message.startswith("Websocket scope cleanup exceeded its cleanup budget") for message in caplog.messages
    )
    assert service._background_cleanup_tasks == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failing_child",
    ["retired_create_lease_release", "unsent_request_finalization"],
)
async def test_cancelled_scope_isolates_owned_reconnect_child_failure_and_finishes_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failing_child: str,
) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=0,
        prohibit_fast_mode=False,
        proxy_downstream_websocket_idle_timeout_seconds=30.0,
        proxy_request_budget_seconds=30.0,
        stream_idle_timeout_seconds=30.0,
        sse_keepalive_interval_seconds=0.0,
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return settings

    request_text = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-5.6-sol",
            "input": "current",
        },
        separators=(",", ":"),
    )
    current = _request_state("request_current")
    current.request_text = request_text
    older_replay = _request_state("request_older_replay")
    older_replay.request_text = request_text
    leftover_pending = _request_state("request_leftover_pending", response_create_sent_at=1.0)
    leftover_pending.request_text = request_text

    class _SingleRequestDownstream:
        def __init__(self) -> None:
            self._received = False

        async def receive(self) -> dict[str, object]:
            if not self._received:
                self._received = True
                return {"type": "websocket.receive", "text": request_text}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send_text(self, _text: str) -> None:
            return None

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason

    class _RetiredUpstream:
        def __init__(self) -> None:
            self.sent_text: list[str] = []

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("binary send is not expected")

        async def close(self) -> None:
            return None

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    upstream = _RetiredUpstream()
    reader_ready = asyncio.Event()
    child_started = asyncio.Event()
    release_child = asyncio.Event()
    cleanup_attempts: list[list[str]] = []

    async def prepare_request(*_args: object, **_kwargs: object) -> proxy_service._PreparedWebSocketRequest:
        return proxy_service._PreparedWebSocketRequest(
            text_data=request_text,
            request_state=current,
            affinity_policy=proxy_service._AffinityPolicy(),
        )

    async def acquire_admission(
        request_state: proxy_service._WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        request_state.response_create_gate = response_create_gate
        await response_create_gate.acquire()
        request_state.response_create_gate_acquired = True
        request_state.awaiting_response_created = True

    async def connect_upstream(*_args: object, **_kwargs: object) -> tuple[Account, UpstreamWebSocket]:
        account = cast(Account, SimpleNamespace(id="account_retired", codex_installation_id=None))
        return account, cast(UpstreamWebSocket, upstream)

    async def relay_transport_end(
        *_args: object,
        pending_requests: deque[proxy_service._WebSocketRequestState],
        pending_lock: anyio.Lock,
        upstream_control: proxy_service._WebSocketUpstreamControl,
        **_kwargs: object,
    ) -> None:
        async with pending_lock:
            pending_requests.append(leftover_pending)
        upstream_control.replay_request_state = older_replay
        upstream_control.reconnect_requested = True
        reader_ready.set()

    async def acquire_create_lease(**_kwargs: object) -> object:
        await reader_ready.wait()
        return object()

    async def release_retired_create_lease(
        request_state: proxy_service._WebSocketRequestState,
    ) -> None:
        request_state.account_response_create_lease = None
        request_state.account_response_create_release = None
        if failing_child == "retired_create_lease_release":
            child_started.set()
            await release_child.wait()
            raise RuntimeError("retired create lease release failed")

    async def fail_pending(
        *_args: object,
        pending_requests: deque[proxy_service._WebSocketRequestState],
        error_message: str,
        **_kwargs: object,
    ) -> None:
        request_ids = [request_state.request_id for request_state in pending_requests]
        cleanup_attempts.append(request_ids)
        if (
            failing_child == "unsent_request_finalization"
            and request_ids == [current.request_id]
            and error_message == "Upstream websocket closed before request could be sent"
        ):
            child_started.set()
            await release_child.wait()
            raise RuntimeError("unsent request finalization failed")
        pending_requests.clear()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_routing_strategy", lambda _settings: "usage_weighted")
    monkeypatch.setattr(proxy_service, "_enforce_response_create_size_limit", lambda _request_state: None)
    monkeypatch.setattr(websocket_mixin, "effective_account_concurrency_caps", lambda _settings: object())
    monkeypatch.setattr(service, "_websocket_continuity_state_for_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_prepare_websocket_response_create_request", prepare_request)
    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", acquire_admission)
    monkeypatch.setattr(service, "_connect_proxy_websocket", connect_upstream)
    monkeypatch.setattr(service, "_relay_upstream_websocket_messages", relay_transport_end)
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", acquire_create_lease)
    monkeypatch.setattr(service, "_release_request_state_account_response_create_lease", release_retired_create_lease)
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", fail_pending)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", AsyncMock())

    caplog.set_level(logging.WARNING)
    scope_task = asyncio.create_task(
        service.proxy_responses_websocket(
            cast(WebSocket, _SingleRequestDownstream()),
            {},
            codex_session_affinity=False,
            openai_cache_affinity=False,
            api_key=None,
        )
    )
    await asyncio.wait_for(child_started.wait(), timeout=1)

    scope_task.cancel()
    await asyncio.sleep(0)
    release_child.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(scope_task, timeout=1)
    await asyncio.sleep(0)

    assert upstream.sent_text == []
    assert [request_id for attempt in cleanup_attempts for request_id in attempt] == [
        current.request_id,
        older_replay.request_id,
        leftover_pending.request_id,
    ]
    assert service._background_cleanup_tasks == set()
    expected_warning = (
        "Retired websocket create lease release failed during scope cleanup"
        if failing_child == "retired_create_lease_release"
        else "Unsent websocket request finalization failed during scope cleanup"
    )
    assert expected_warning in caplog.messages


@pytest.mark.parametrize(
    "release_failure",
    [None, "admission", "account_create_lease"],
    ids=["release_success", "admission_release_failure", "account_create_lease_release_failure"],
)
@pytest.mark.asyncio
async def test_drain_start_during_admission_rejects_turn_before_connect_or_send(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    release_failure: str | None,
) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=0,
        prohibit_fast_mode=False,
        proxy_downstream_websocket_idle_timeout_seconds=30.0,
        proxy_request_budget_seconds=30.0,
        stream_idle_timeout_seconds=30.0,
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return settings

    request_text = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-5.6-sol",
            "input": "late drain",
        },
        separators=(",", ":"),
    )
    request_state = _request_state("request_late_drain")
    request_state.request_text = request_text
    request_state.api_key_reservation = cast(ApiKeyUsageReservationData, object())
    admission_started = asyncio.Event()
    release_admission = asyncio.Event()
    admission_release_calls = 0
    account_lease_release_calls: list[object | None] = []
    response_create_gates: list[asyncio.Semaphore] = []
    pending_snapshots: list[list[str]] = []

    class _Downstream:
        def __init__(self) -> None:
            self._received = False
            self.closed = False
            self.sent_text: list[str] = []
            self.lifecycle: list[str] = []

        async def receive(self) -> dict[str, object]:
            if not self._received:
                self._received = True
                return {"type": "websocket.receive", "text": request_text}
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            if self.closed:
                raise AssertionError("terminal event sent after downstream close")
            self.sent_text.append(text)
            self.lifecycle.append(f"send:{json.loads(text)['type']}")

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("binary send is not expected")

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del reason
            self.closed = True
            self.lifecycle.append(f"close:{code}")

    downstream = _Downstream()
    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    connect_upstream = AsyncMock()
    release_reservation = AsyncMock()

    async def prepare_request(*_args: object, **_kwargs: object) -> proxy_service._PreparedWebSocketRequest:
        return proxy_service._PreparedWebSocketRequest(
            text_data=request_text,
            request_state=request_state,
            affinity_policy=proxy_service._AffinityPolicy(),
        )

    def release_admission_lease() -> None:
        nonlocal admission_release_calls
        admission_release_calls += 1
        if release_failure == "admission":
            raise RuntimeError("work admission release failed")

    async def release_account_create_lease(lease: object | None) -> None:
        account_lease_release_calls.append(lease)
        if release_failure == "account_create_lease":
            raise RuntimeError("account create lease release failed")

    account_create_lease = object()

    async def acquire_admission(
        owned_request_state: proxy_service._WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        admission_started.set()
        await release_admission.wait()
        response_create_gates.append(response_create_gate)
        await response_create_gate.acquire()
        owned_request_state.response_create_gate = response_create_gate
        owned_request_state.response_create_gate_acquired = True
        owned_request_state.awaiting_response_created = True
        owned_request_state.response_create_admission = cast(
            Any,
            SimpleNamespace(release=release_admission_lease),
        )
        owned_request_state.account_response_create_lease = cast(Any, account_create_lease)
        owned_request_state.account_response_create_release = cast(Any, release_account_create_lease)

    original_has_active_drain_work = websocket_mixin._websocket_has_active_drain_work

    async def observe_pending_after_rejection(
        pending_requests: deque[proxy_service._WebSocketRequestState],
        *,
        pending_lock: anyio.Lock,
        upstream_control: proxy_service._WebSocketUpstreamControl | None,
    ) -> bool:
        async with pending_lock:
            pending_snapshots.append([state.request_id for state in pending_requests])
        return await original_has_active_drain_work(
            pending_requests,
            pending_lock=pending_lock,
            upstream_control=upstream_control,
        )

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_routing_strategy", lambda _settings: "usage_weighted")
    monkeypatch.setattr(service, "_websocket_continuity_state_for_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_prepare_websocket_response_create_request", prepare_request)
    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", acquire_admission)
    monkeypatch.setattr(service, "_release_websocket_request_state_reservation", release_reservation)
    monkeypatch.setattr(service, "_connect_proxy_websocket", connect_upstream)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", AsyncMock())
    monkeypatch.setattr(websocket_mixin, "_websocket_has_active_drain_work", observe_pending_after_rejection)
    caplog.set_level(logging.WARNING)

    scope_task = asyncio.create_task(
        service.proxy_responses_websocket(
            cast(WebSocket, downstream),
            {},
            codex_session_affinity=False,
            openai_cache_affinity=False,
            api_key=None,
        )
    )
    await asyncio.wait_for(admission_started.wait(), timeout=1)
    shutdown_state.commit_shutdown(timeout_seconds=1.0)
    release_admission.set()
    await asyncio.wait_for(scope_task, timeout=1)

    connect_upstream.assert_not_awaited()
    release_reservation.assert_awaited_once_with(request_state)
    assert pending_snapshots and all(snapshot == [] for snapshot in pending_snapshots)
    assert response_create_gates and response_create_gates[0].locked() is False
    assert admission_release_calls == 1
    assert account_lease_release_calls == [account_create_lease]
    assert request_state.response_create_gate is None
    assert request_state.response_create_admission is None
    assert request_state.account_response_create_lease is None
    assert request_state.account_response_create_release is None
    assert [json.loads(text)["type"] for text in downstream.sent_text] == ["response.failed"]
    assert json.loads(downstream.sent_text[0])["response"]["error"]["code"] == "service_unavailable"
    assert downstream.lifecycle == ["send:response.failed", "close:1012"]
    if release_failure == "admission":
        assert (
            "Failed to release websocket work admission during terminal cleanup request_id=request_late_drain"
            in caplog.messages
        )
    elif release_failure == "account_create_lease":
        assert (
            "Failed to release websocket account create lease during terminal cleanup request_id=request_late_drain"
            in caplog.messages
        )


@pytest.mark.asyncio
async def test_terminal_send_failure_does_not_skip_mandatory_cleanup_or_next_state(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    response_create_gate = asyncio.BoundedSemaphore(2)
    await response_create_gate.acquire()
    await response_create_gate.acquire()
    admission_release_calls: list[str] = []
    account_create_releases: list[object | None] = []
    oversized_dump_calls: list[str] = []
    stream_release = AsyncMock()
    reservation_release = AsyncMock()
    request_log = AsyncMock()
    emit_terminal = AsyncMock(side_effect=[RuntimeError("downstream send failed"), None])
    states = [_request_state("request_partial_failure"), _request_state("request_after_partial_failure")]
    account_create_leases = [object(), object()]
    stream_leases = [object(), object()]

    async def release_account_create(lease: object | None) -> None:
        account_create_releases.append(lease)
        if lease is account_create_leases[0]:
            raise RuntimeError("account create lease release failed")

    def dump_oversized_request(
        state: proxy_service._WebSocketRequestState,
        **_kwargs: object,
    ) -> None:
        oversized_dump_calls.append(state.request_id)
        raise RuntimeError("oversized request dump failed")

    for index, state in enumerate(states):

        def release_admission(request_id: str = state.request_id) -> None:
            admission_release_calls.append(request_id)
            if request_id == states[0].request_id:
                raise RuntimeError("work admission release failed")

        state.response_create_gate = response_create_gate
        state.response_create_gate_acquired = True
        state.awaiting_response_created = True
        state.response_create_admission = cast(
            Any,
            SimpleNamespace(release=release_admission),
        )
        state.account_response_create_lease = cast(Any, account_create_leases[index])
        state.account_response_create_release = cast(Any, release_account_create)
        state.websocket_stream_lease = cast(Any, stream_leases[index])

    monkeypatch.setattr(service, "_cancel_request_state_api_key_reservation_heartbeat", lambda _state: None)
    monkeypatch.setattr(service, "_emit_websocket_terminal_error", emit_terminal)
    monkeypatch.setattr(service, "_release_websocket_request_state_reservation", reservation_release)
    monkeypatch.setattr(service, "_write_request_log", request_log)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", stream_release)
    monkeypatch.setattr(proxy_service, "_maybe_dump_oversized_response_create_request", dump_oversized_request)
    caplog.set_level(logging.WARNING)
    pending_requests = deque(states)

    await service._fail_pending_websocket_requests(
        account=None,
        account_id_value="account_partial_failure",
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        error_code="stream_incomplete",
        error_message="Upstream websocket closed before response.completed",
        api_key=None,
        websocket=cast(WebSocket, _DownstreamWebSocket()),
        client_send_lock=anyio.Lock(),
        response_create_gate=response_create_gate,
        downstream_activity=proxy_service._DownstreamWebSocketActivity(),
        penalize_account=False,
    )

    assert pending_requests == deque()
    assert response_create_gate.locked() is False
    assert admission_release_calls == [state.request_id for state in states]
    assert account_create_releases == account_create_leases
    assert stream_release.await_count == 2
    assert [await_call.args[0] for await_call in stream_release.await_args_list] == stream_leases
    assert reservation_release.await_count == 2
    assert [await_call.args[0] for await_call in reservation_release.await_args_list] == states
    assert request_log.await_count == 2
    assert [await_call.kwargs["request_id"] for await_call in request_log.await_args_list] == [
        state.request_id for state in states
    ]
    assert emit_terminal.await_count == 2
    assert oversized_dump_calls == [states[-1].request_id]
    assert all(state.response_create_gate is None for state in states)
    assert all(state.response_create_admission is None for state in states)
    assert all(state.account_response_create_lease is None for state in states)
    assert all(state.websocket_stream_lease is None for state in states)
    await asyncio.wait_for(response_create_gate.acquire(), timeout=0.1)
    await asyncio.wait_for(response_create_gate.acquire(), timeout=0.1)
    assert response_create_gate.locked() is True
    assert (
        "Failed to emit websocket terminal event during cleanup request_id=request_partial_failure" in caplog.messages
    )
    assert (
        "Failed to release websocket work admission during terminal cleanup request_id=request_partial_failure"
        in caplog.messages
    )
    assert (
        "Failed to release websocket account create lease during terminal cleanup "
        "request_id=request_partial_failure" in caplog.messages
    )
    assert (
        "Failed to dump oversized websocket request during terminal cleanup "
        "request_id=request_after_partial_failure" in caplog.messages
    )


@pytest.mark.asyncio
async def test_pending_failure_cancellation_after_claim_remains_drain_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_logs = _RequestLogsRecorder()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=request_logs, api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    lease_release_started = asyncio.Event()
    release_lease = asyncio.Event()
    admission_releases: list[str] = []
    account_lease_releases: list[object | None] = []
    reservation_releases: list[str] = []
    account_create_lease = object()

    async def release_account_create_lease(lease: object | None) -> None:
        account_lease_releases.append(lease)
        lease_release_started.set()
        await release_lease.wait()

    async def release_reservation(state: proxy_service._WebSocketRequestState) -> None:
        reservation = state.api_key_reservation
        assert reservation is not None
        reservation_releases.append(reservation.reservation_id)
        state.api_key_reservation = None

    api_key = ApiKeyData(
        id="key_pending_claim_cancel",
        name="pending claim cancel",
        key_prefix="sk-pending",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="reservation_pending_claim_cancel",
        key_id=api_key.id,
        model="gpt-5.6-sol",
    )
    request_state = _request_state("request_pending_claim_cancel")
    request_state.api_key_reservation = reservation
    request_state.response_create_gate = gate
    request_state.response_create_gate_acquired = True
    request_state.awaiting_response_created = True
    request_state.response_create_admission = cast(
        Any,
        SimpleNamespace(release=lambda: admission_releases.append(request_state.request_id)),
    )
    request_state.account_response_create_lease = cast(Any, account_create_lease)
    request_state.account_response_create_release = cast(Any, release_account_create_lease)
    pending_requests = deque([request_state])

    monkeypatch.setattr(service, "_cancel_request_state_api_key_reservation_heartbeat", lambda _state: None)
    monkeypatch.setattr(service, "_release_websocket_request_state_reservation", release_reservation)

    failure = asyncio.create_task(
        service._fail_pending_websocket_requests(
            account=None,
            account_id_value="account_pending_claim_cancel",
            pending_requests=pending_requests,
            pending_lock=anyio.Lock(),
            error_code="stream_incomplete",
            error_message="Upstream websocket closed before response.completed",
            api_key=api_key,
            response_create_gate=gate,
            status="cancelled",
            penalize_account=False,
        ),
        name="test-pending-failure-caller",
    )
    await asyncio.wait_for(lease_release_started.wait(), timeout=1)

    assert pending_requests == deque()
    shutdown_state.commit_shutdown(timeout_seconds=0.01)
    shutdown_state.set_post_drain_cleanup_timeout_seconds(0.05)
    failure.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(failure, timeout=0.2)

    owned_names = {task.get_name() for task in service._background_cleanup_tasks if not task.done()}
    assert "proxy-websocket-finalization-pending-requests" in owned_names
    persistence_drain = asyncio.create_task(service.drain_persistence_tasks(timeout_seconds=1))
    await asyncio.sleep(0)
    assert persistence_drain.done() is False

    release_lease.set()
    assert await asyncio.wait_for(persistence_drain, timeout=1)
    await asyncio.sleep(0)

    assert admission_releases == [request_state.request_id]
    assert account_lease_releases == [account_create_lease]
    assert reservation_releases == [reservation.reservation_id]
    assert gate.locked() is False
    assert request_state.response_create_gate is None
    assert request_state.response_create_admission is None
    assert request_state.account_response_create_lease is None
    assert request_state.api_key_reservation is None
    assert len(request_logs.calls) == 1
    assert request_logs.calls[0]["request_id"] == request_state.request_id
    assert request_logs.calls[0]["status"] == "cancelled"
    assert service._background_cleanup_tasks == set()


@pytest.mark.asyncio
async def test_cancelled_task_wait_is_strictly_bounded_by_shared_deadline() -> None:
    release_task = asyncio.Event()
    cancellation_seen = asyncio.Event()

    async def cancellation_resistant_task() -> None:
        while not release_task.is_set():
            try:
                await release_task.wait()
            except asyncio.CancelledError:
                cancellation_seen.set()

    task = asyncio.create_task(cancellation_resistant_task())
    await asyncio.sleep(0)
    shutdown_state.commit_shutdown(timeout_seconds=0.05)
    release_later = asyncio.create_task(_release_after(release_task, 0.25))

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    cancelled = await proxy_service._await_cancelled_task(
        task,
        timeout_seconds=1.0,
        label="cancellation-resistant task",
    )
    elapsed = loop.time() - started_at

    release_task.set()
    await asyncio.wait_for(task, timeout=1)
    release_later.cancel()
    with suppress(asyncio.CancelledError):
        await release_later

    assert cancelled is False
    assert elapsed < 0.15
    assert cancellation_seen.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancel_before_archive_attribution",
    [False, True],
    ids=["after-pending-pop", "before-archive-attribution"],
)
async def test_terminal_message_ownership_survives_relay_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    cancel_before_archive_attribution: bool,
) -> None:
    """A received terminal message remains drain-owned through settlement and logging."""

    request_logs = _RequestLogsRecorder()
    api_keys_repo = object()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=request_logs, api_keys=api_keys_repo)

    finalized: list[dict[str, object]] = []
    released: list[str] = []

    class _FakeApiKeysService:
        def __init__(self, repository: object) -> None:
            assert repository is api_keys_repo

        async def finalize_usage_reservation(self, reservation_id: str, **kwargs: object) -> None:
            finalized.append({"reservation_id": reservation_id, **kwargs})

        async def release_usage_reservation(self, reservation_id: str) -> None:
            released.append(reservation_id)

    monkeypatch.setattr(proxy_service, "ApiKeysService", _FakeApiKeysService)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(sse_keepalive_interval_seconds=10.0),
    )

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    monkeypatch.setattr(service._load_balancer, "record_success", AsyncMock())

    archive_attribution_started = asyncio.Event()
    gate_entered = asyncio.Event()
    release_gate = asyncio.Event()
    original_archive_attribution = websocket_mixin._websocket_archive_request_id_for_message
    original_release_gate = websocket_mixin._release_websocket_response_create_gate

    async def _observed_archive_attribution(
        message: object,
        *,
        pending_requests: deque[proxy_service._WebSocketRequestState],
        pending_lock: anyio.Lock,
        parsed_frame: object | None = None,
    ) -> str | None:
        archive_attribution_started.set()
        return await original_archive_attribution(
            message,
            pending_requests=pending_requests,
            pending_lock=pending_lock,
            parsed_frame=cast("websocket_mixin._ParsedUpstreamWebSocketFrame | None", parsed_frame),
        )

    async def _blocking_release_gate(
        request_state: proxy_service._WebSocketRequestState,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        gate_entered.set()
        await release_gate.wait()
        await original_release_gate(request_state, response_create_gate)

    monkeypatch.setattr(
        websocket_mixin,
        "_websocket_archive_request_id_for_message",
        _observed_archive_attribution,
    )
    monkeypatch.setattr(
        websocket_mixin,
        "_release_websocket_response_create_gate",
        _blocking_release_gate,
    )

    api_key = ApiKeyData(
        id="key_terminal_cancel",
        name="terminal cancel",
        key_prefix="sk-terminal",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="reservation_terminal_cancel",
        key_id=api_key.id,
        model="gpt-5.6-sol",
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="request_terminal_cancel",
        response_id="response_terminal_cancel",
        model="gpt-5.6-sol",
        service_tier="priority",
        reasoning_effort="high",
        api_key_reservation=reservation,
        started_at=asyncio.get_running_loop().time(),
    )
    pending_requests = deque([request_state])
    terminal_payload = {
        "type": "response.completed",
        "response": {
            "id": request_state.response_id,
            "status": "completed",
            "usage": {
                "input_tokens": 12,
                "output_tokens": 7,
                "total_tokens": 19,
                "input_tokens_details": {"cached_tokens": 3},
            },
        },
    }
    terminal_receive_started = asyncio.Event()
    release_terminal_receive = asyncio.Event()

    class _ControlledTerminalUpstream(_TerminalThenBlockingUpstream):
        async def receive(self) -> SimpleNamespace:
            if not self._terminal_sent:
                terminal_receive_started.set()
                await release_terminal_receive.wait()
            return await super().receive()

    upstream = _ControlledTerminalUpstream(json.dumps(terminal_payload, separators=(",", ":")))
    downstream = _DownstreamWebSocket()
    pending_lock = anyio.Lock()
    if not cancel_before_archive_attribution:
        release_terminal_receive.set()
    upstream_control = proxy_service._WebSocketUpstreamControl()

    relay = asyncio.create_task(
        service._relay_upstream_websocket_messages(
            cast(WebSocket, downstream),
            cast(UpstreamWebSocket, upstream),
            account=cast(Account, SimpleNamespace(id="account_terminal_cancel")),
            account_id_value="account_terminal_cancel",
            pending_requests=pending_requests,
            pending_lock=pending_lock,
            client_send_lock=anyio.Lock(),
            api_key=api_key,
            upstream_control=upstream_control,
            response_create_gate=asyncio.Semaphore(1),
            proxy_request_budget_seconds=30.0,
            stream_idle_timeout_seconds=30.0,
            downstream_activity=proxy_service._DownstreamWebSocketActivity(),
        ),
        name="test-terminal-relay",
    )

    if cancel_before_archive_attribution:
        await asyncio.wait_for(terminal_receive_started.wait(), timeout=1.0)
        await pending_lock.acquire()
        release_terminal_receive.set()
        await asyncio.wait_for(archive_attribution_started.wait(), timeout=1.0)
        assert upstream_control.terminal_message_task is not None
        assert pending_requests == deque([request_state])
    else:
        await asyncio.wait_for(gate_entered.wait(), timeout=1.0)
        assert pending_requests == deque()

    relay.cancel()
    await asyncio.sleep(0)
    persistence_drain = asyncio.create_task(
        service.drain_persistence_tasks(timeout_seconds=1.0),
        name="test-terminal-persistence-drain",
    )
    await asyncio.sleep(0.01)
    terminal_work_was_drain_owned = not persistence_drain.done()

    if cancel_before_archive_attribution:
        pending_lock.release()
        await asyncio.wait_for(gate_entered.wait(), timeout=1.0)
        assert pending_requests == deque()
    release_gate.set()
    with suppress(asyncio.CancelledError):
        await relay

    assert terminal_work_was_drain_owned is True
    assert await asyncio.wait_for(persistence_drain, timeout=1.0)
    assert await service.drain_persistence_tasks(timeout_seconds=0)
    assert finalized == [
        {
            "reservation_id": reservation.reservation_id,
            "model": request_state.model,
            "input_tokens": 12,
            "output_tokens": 7,
            "cached_input_tokens": 3,
            "service_tier": request_state.service_tier,
        }
    ]
    assert released == []
    assert [json.loads(text)["type"] for text in downstream.sent_text] == ["response.completed"]
    assert len(request_logs.calls) == 1
    assert request_logs.calls[0]["request_id"] == request_state.response_id
    assert request_logs.calls[0]["status"] == "success"
    assert request_logs.calls[0]["input_tokens"] == 12
    assert request_logs.calls[0]["output_tokens"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_name",
    [
        "proxy-websocket-terminal-test",
        "proxy-websocket-transport-end-test",
        "proxy-websocket-finalization-test",
    ],
)
async def test_websocket_owned_child_kinds_are_persistence_drained(task_name: str) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    release_child = asyncio.Event()

    async def owned_child() -> None:
        await release_child.wait()

    child = asyncio.create_task(owned_child(), name=task_name)
    service._background_cleanup_tasks.add(child)
    persistence_drain = asyncio.create_task(service.drain_persistence_tasks(timeout_seconds=1))
    await asyncio.sleep(0)

    assert persistence_drain.done() is False
    release_child.set()
    await child
    assert await asyncio.wait_for(persistence_drain, timeout=1)


@pytest.mark.asyncio
async def test_terminal_message_cancellation_is_bounded_by_shared_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(sse_keepalive_interval_seconds=10.0),
    )

    child_started = asyncio.Event()
    child_cancelled = asyncio.Event()
    release_child = asyncio.Event()

    async def blocking_terminal_work(*_args: object, **_kwargs: object) -> bool:
        child_started.set()
        try:
            await release_child.wait()
        except asyncio.CancelledError:
            child_cancelled.set()
            raise
        return False

    monkeypatch.setattr(
        websocket_mixin,
        "_process_and_forward_upstream_websocket_text",
        blocking_terminal_work,
    )

    upstream = _TerminalThenBlockingUpstream("{}")
    downstream = _DownstreamWebSocket()
    relay = asyncio.create_task(
        service._relay_upstream_websocket_messages(
            cast(WebSocket, downstream),
            cast(UpstreamWebSocket, upstream),
            account=cast(Account, SimpleNamespace(id="account_terminal_timeout")),
            account_id_value="account_terminal_timeout",
            pending_requests=deque(),
            pending_lock=anyio.Lock(),
            client_send_lock=anyio.Lock(),
            api_key=None,
            upstream_control=proxy_service._WebSocketUpstreamControl(),
            response_create_gate=asyncio.Semaphore(1),
            proxy_request_budget_seconds=30.0,
            stream_idle_timeout_seconds=30.0,
            downstream_activity=proxy_service._DownstreamWebSocketActivity(),
        ),
        name="test-terminal-relay-timeout",
    )
    await asyncio.wait_for(child_started.wait(), timeout=1)

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    shutdown_state.commit_shutdown(timeout_seconds=0.05)
    release_later = asyncio.create_task(_release_after(release_child, 0.25))
    cancelled = await proxy_service._await_cancelled_task(
        relay,
        timeout_seconds=0.05,
        label="test terminal relay timeout",
    )
    elapsed = loop.time() - started_at

    release_later.cancel()
    with suppress(asyncio.CancelledError):
        await release_later
    assert cancelled is False
    assert elapsed < 0.15
    assert child_cancelled.is_set() is False
    assert any(
        task.get_name() == "proxy-websocket-terminal-account_terminal_timeout"
        for task in service._background_cleanup_tasks
        if not task.done()
    )
    release_child.set()
    assert await service.drain_persistence_tasks(timeout_seconds=0.1)


@pytest.mark.asyncio
async def test_terminal_message_cancellation_without_drain_leaves_owned_task_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_cancelled = asyncio.Event()
    release_child = asyncio.Event()

    async def owned_child() -> None:
        try:
            await release_child.wait()
        except asyncio.CancelledError:
            child_cancelled.set()
            raise

    monkeypatch.setattr(proxy_service, "_TASK_CANCEL_TIMEOUT_SECONDS", 0.01)
    child = asyncio.create_task(owned_child())

    await websocket_mixin._await_owned_websocket_task_after_reader_cancellation(
        child,
        failure_message="test child failure",
    )

    assert child_cancelled.is_set() is False
    assert child.done() is False
    release_child.set()
    await asyncio.wait_for(child, timeout=1)


@pytest.mark.asyncio
async def test_stuck_upstream_close_is_cancelled_after_scope_cleanup_timeout() -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    close_cancelled = False

    async def close() -> None:
        nonlocal close_cancelled
        close_started.set()
        try:
            await release_close.wait()
        except asyncio.CancelledError:
            close_cancelled = True
            raise

    upstream = cast(UpstreamWebSocket, SimpleNamespace(close=close))

    cleanup = asyncio.create_task(
        websocket_mixin._close_websocket_upstream_for_cleanup(
            service,
            upstream,
            timeout_seconds=1.0,
        )
    )
    await asyncio.wait_for(close_started.wait(), timeout=1)
    await asyncio.wait_for(cleanup, timeout=1)

    assert close_cancelled is True
    assert service._background_cleanup_tasks == set()
    release_close.set()


@pytest.mark.asyncio
async def test_upstream_close_is_cancelled_when_cleanup_budget_is_exhausted() -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    close_started = asyncio.Event()
    close_cancelled = False

    async def close() -> None:
        nonlocal close_cancelled
        close_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            close_cancelled = True
            raise

    upstream = cast(UpstreamWebSocket, SimpleNamespace(close=close))

    await websocket_mixin._close_websocket_upstream_for_cleanup(
        service,
        upstream,
        timeout_seconds=0.0,
    )

    await asyncio.wait_for(close_started.wait(), timeout=1)
    for _ in range(20):
        if close_cancelled and not service._background_cleanup_tasks:
            break
        await asyncio.sleep(0)
    assert close_cancelled is True
    assert service._background_cleanup_tasks == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("message_kind", ["text", "transport_end"])
async def test_reader_cancellation_remains_cancelled_when_owned_child_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    message_kind: str,
) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(sse_keepalive_interval_seconds=0.0),
    )
    monkeypatch.setattr(service, "_next_websocket_receive_timeout", AsyncMock(return_value=None))
    child_started = asyncio.Event()
    release_child = asyncio.Event()

    async def failing_owned_child(*_args: object, **_kwargs: object) -> bool:
        child_started.set()
        await release_child.wait()
        raise RuntimeError(f"{message_kind} child failed")

    target_name = (
        "_process_and_forward_upstream_websocket_text"
        if message_kind == "text"
        else "_process_upstream_websocket_transport_end"
    )
    monkeypatch.setattr(websocket_mixin, target_name, failing_owned_child)

    class _SingleMessageUpstream:
        async def receive(self) -> SimpleNamespace:
            return SimpleNamespace(
                kind="text" if message_kind == "text" else "close",
                text="{}" if message_kind == "text" else None,
                data=None,
                close_code=None if message_kind == "text" else 1000,
                error=None,
                error_code=None,
            )

        async def close(self) -> None:
            return None

    relay = asyncio.create_task(
        service._relay_upstream_websocket_messages(
            cast(WebSocket, _DownstreamWebSocket()),
            cast(UpstreamWebSocket, _SingleMessageUpstream()),
            account=cast(Account, SimpleNamespace(id=f"account_{message_kind}")),
            account_id_value=f"account_{message_kind}",
            pending_requests=deque(),
            pending_lock=anyio.Lock(),
            client_send_lock=anyio.Lock(),
            api_key=None,
            upstream_control=proxy_service._WebSocketUpstreamControl(),
            response_create_gate=asyncio.Semaphore(1),
            proxy_request_budget_seconds=30.0,
            stream_idle_timeout_seconds=30.0,
            downstream_activity=proxy_service._DownstreamWebSocketActivity(),
        )
    )
    await asyncio.wait_for(child_started.wait(), timeout=1)

    caplog.set_level(logging.WARNING)
    relay.cancel()
    await asyncio.sleep(0)
    release_child.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(relay, timeout=1)
    await asyncio.sleep(0)

    expected_warning = (
        "Websocket terminal task failed during reader cancellation"
        if message_kind == "text"
        else "Websocket transport-end task failed during reader cancellation"
    )
    assert expected_warning in caplog.messages
    assert service._background_cleanup_tasks == set()


@pytest.mark.asyncio
async def test_reader_cancellation_after_transport_end_claim_waits_for_child_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(sse_keepalive_interval_seconds=0.0),
    )
    monkeypatch.setattr(service, "_next_websocket_receive_timeout", AsyncMock(return_value=None))
    cleanup_claimed = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleaned_request_ids: list[str] = []
    request_state = _request_state("request_reader_cancelled_after_claim", response_create_sent_at=1.0)
    request_state.downstream_visible = True
    pending_requests = deque([request_state])

    async def blocking_cleanup(
        *_args: object,
        pending_requests: deque[proxy_service._WebSocketRequestState],
        **_kwargs: object,
    ) -> None:
        cleaned_request_ids.extend(state.request_id for state in pending_requests)
        cleanup_claimed.set()
        await release_cleanup.wait()
        pending_requests.clear()

    monkeypatch.setattr(service, "_fail_pending_websocket_requests", blocking_cleanup)

    class _CloseUpstream:
        async def receive(self) -> SimpleNamespace:
            return SimpleNamespace(
                kind="close",
                text=None,
                data=None,
                close_code=1000,
                error=None,
                error_code=None,
            )

        async def close(self) -> None:
            return None

    upstream_control = proxy_service._WebSocketUpstreamControl()
    relay = asyncio.create_task(
        service._relay_upstream_websocket_messages(
            cast(WebSocket, _DownstreamWebSocket()),
            cast(UpstreamWebSocket, _CloseUpstream()),
            account=cast(Account, SimpleNamespace(id="account_reader_claim")),
            account_id_value="account_reader_claim",
            pending_requests=pending_requests,
            pending_lock=anyio.Lock(),
            client_send_lock=anyio.Lock(),
            api_key=None,
            upstream_control=upstream_control,
            response_create_gate=asyncio.Semaphore(1),
            proxy_request_budget_seconds=30.0,
            stream_idle_timeout_seconds=30.0,
            downstream_activity=proxy_service._DownstreamWebSocketActivity(),
        )
    )
    await asyncio.wait_for(cleanup_claimed.wait(), timeout=1)
    assert pending_requests == deque()

    relay.cancel()
    await asyncio.sleep(0)
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(relay, timeout=1)
    await asyncio.sleep(0)

    assert cleaned_request_ids == [request_state.request_id]
    assert upstream_control.terminal_message_task is None
    assert service._background_cleanup_tasks == set()


@pytest.mark.asyncio
async def test_scope_cancellation_finalizes_turn_when_connection_lease_release_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_logs = _RequestLogsRecorder()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=request_logs, api_keys=object())

    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=0,
        prohibit_fast_mode=False,
        proxy_downstream_websocket_idle_timeout_seconds=30.0,
        proxy_request_budget_seconds=30.0,
        stream_idle_timeout_seconds=30.0,
        sse_keepalive_interval_seconds=0.0,
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return settings

    request_text = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-5.6-sol",
            "input": "cancel after send",
        },
        separators=(",", ":"),
    )
    api_key = ApiKeyData(
        id="key_scope_connection_release",
        name="scope connection release",
        key_prefix="sk-scope",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="reservation_scope_connection_release",
        key_id=api_key.id,
        model="gpt-5.6-sol",
    )
    request_state = _request_state("request_scope_connection_release")
    request_state.request_text = request_text
    request_state.api_key = api_key
    request_state.api_key_reservation = reservation
    connection_lease = object()
    account_create_lease = object()
    release_calls: list[object | None] = []
    admission_releases: list[str] = []
    reservation_releases: list[str] = []
    captured_response_create_gate: asyncio.Semaphore | None = None
    upstream_send_completed = asyncio.Event()
    upstream_closed = asyncio.Event()

    class _Downstream:
        def __init__(self) -> None:
            self._request_sent = False
            self.close_calls: list[tuple[int, str | None]] = []

        async def receive(self) -> dict[str, object]:
            if not self._request_sent:
                self._request_sent = True
                return {"type": "websocket.receive", "text": request_text}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send_text(self, _text: str) -> None:
            return None

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("binary send is not expected")

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.close_calls.append((code, reason))

    class _Upstream:
        async def send_text(self, _text: str) -> None:
            upstream_send_completed.set()

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("binary send is not expected")

        async def receive(self) -> SimpleNamespace:
            await upstream_closed.wait()
            return SimpleNamespace(
                kind="close",
                text=None,
                data=None,
                close_code=1000,
                error=None,
                error_code=None,
            )

        async def close(self) -> None:
            upstream_closed.set()

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    downstream = _Downstream()
    upstream = _Upstream()

    async def prepare_request(*_args: object, **_kwargs: object) -> proxy_service._PreparedWebSocketRequest:
        return proxy_service._PreparedWebSocketRequest(
            text_data=request_text,
            request_state=request_state,
            affinity_policy=proxy_service._AffinityPolicy(),
        )

    async def acquire_admission(
        state: proxy_service._WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        nonlocal captured_response_create_gate
        captured_response_create_gate = response_create_gate
        await response_create_gate.acquire()
        state.response_create_gate = response_create_gate
        state.response_create_gate_acquired = True
        state.awaiting_response_created = True
        state.response_create_admission = cast(
            Any,
            SimpleNamespace(release=lambda: admission_releases.append(state.request_id)),
        )

    async def connect_upstream(
        *_args: object,
        request_state: proxy_service._WebSocketRequestState,
        **_kwargs: object,
    ) -> tuple[Account, UpstreamWebSocket]:
        request_state.websocket_stream_lease = cast(Any, connection_lease)
        return (
            cast(Account, SimpleNamespace(id="account_scope_connection_release", codex_installation_id=None)),
            cast(UpstreamWebSocket, upstream),
        )

    async def acquire_create_lease(**_kwargs: object) -> object:
        return account_create_lease

    async def release_lease(lease: object | None) -> None:
        release_calls.append(lease)
        if lease is connection_lease:
            raise RuntimeError("connection lease release failed")

    async def release_reservation(
        reservation_to_release: ApiKeyUsageReservationData | None,
    ) -> None:
        if reservation_to_release is not None:
            reservation_releases.append(reservation_to_release.reservation_id)

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_routing_strategy", lambda _settings: "usage_weighted")
    monkeypatch.setattr(proxy_service, "_enforce_response_create_size_limit", lambda _request_state: None)
    monkeypatch.setattr(websocket_mixin, "effective_account_concurrency_caps", lambda _settings: object())
    monkeypatch.setattr(service, "_websocket_continuity_state_for_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_prepare_websocket_response_create_request", prepare_request)
    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", acquire_admission)
    monkeypatch.setattr(service, "_connect_proxy_websocket", connect_upstream)
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", acquire_create_lease)
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)
    monkeypatch.setattr(service, "_next_websocket_receive_timeout", AsyncMock(return_value=None))
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_lease)
    caplog.set_level(logging.WARNING)

    scope_task = asyncio.create_task(
        service.proxy_responses_websocket(
            cast(WebSocket, downstream),
            {},
            codex_session_affinity=False,
            openai_cache_affinity=False,
            api_key=api_key,
        )
    )
    await asyncio.wait_for(upstream_send_completed.wait(), timeout=1)

    scope_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(scope_task, timeout=1)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    await asyncio.sleep(0)

    assert release_calls.count(account_create_lease) == 1
    assert release_calls.count(connection_lease) == 1
    assert admission_releases == [request_state.request_id]
    assert reservation_releases == [reservation.reservation_id]
    assert captured_response_create_gate is not None and captured_response_create_gate.locked() is False
    assert request_state.response_create_gate is None
    assert request_state.response_create_admission is None
    assert request_state.account_response_create_lease is None
    assert len(request_logs.calls) == 1
    assert request_logs.calls[0]["request_id"] == request_state.request_id
    assert request_logs.calls[0]["status"] == "cancelled"
    assert "Failed to release websocket connection lease during scope cleanup" in caplog.messages
    assert service._background_cleanup_tasks == set()


@pytest.mark.asyncio
async def test_scope_cancellation_while_waiting_for_reconnect_reader_preserves_all_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())

    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=0,
        prohibit_fast_mode=False,
        proxy_downstream_websocket_idle_timeout_seconds=30.0,
        proxy_request_budget_seconds=30.0,
        stream_idle_timeout_seconds=30.0,
        sse_keepalive_interval_seconds=0.0,
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return settings

    request_text = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-5.6-sol",
            "input": "cancel reconnect reader",
        },
        separators=(",", ":"),
    )
    current = _request_state("request_reconnect_current")
    current.request_text = request_text
    older_replay = _request_state("request_reconnect_older")
    older_replay.request_text = request_text
    reader_started = asyncio.Event()
    reader_cancelled = asyncio.Event()
    create_lease_acquired = asyncio.Event()
    cleanup_attempts: list[list[str]] = []
    released_leases: list[object | None] = []

    class _Downstream:
        def __init__(self) -> None:
            self._received = False

        async def receive(self) -> dict[str, object]:
            if not self._received:
                self._received = True
                return {"type": "websocket.receive", "text": request_text}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send_text(self, _text: str) -> None:
            return None

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("binary send is not expected")

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason

    class _Upstream:
        def __init__(self) -> None:
            self.sent_text: list[str] = []

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("binary send is not expected")

        async def close(self) -> None:
            return None

    service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, repo_factory))
    upstream = _Upstream()
    captured_response_create_gate: asyncio.Semaphore | None = None

    async def prepare_request(*_args: object, **_kwargs: object) -> proxy_service._PreparedWebSocketRequest:
        return proxy_service._PreparedWebSocketRequest(
            text_data=request_text,
            request_state=current,
            affinity_policy=proxy_service._AffinityPolicy(),
        )

    async def acquire_admission(
        request_state: proxy_service._WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        nonlocal captured_response_create_gate
        captured_response_create_gate = response_create_gate
        await response_create_gate.acquire()
        request_state.response_create_gate = response_create_gate
        request_state.response_create_gate_acquired = True
        request_state.awaiting_response_created = True

    async def connect_upstream(*_args: object, **_kwargs: object) -> tuple[Account, UpstreamWebSocket]:
        account = cast(Account, SimpleNamespace(id="account_reconnect_reader", codex_installation_id=None))
        return account, cast(UpstreamWebSocket, upstream)

    async def blocking_reconnect_reader(
        *_args: object,
        upstream_control: proxy_service._WebSocketUpstreamControl,
        **_kwargs: object,
    ) -> None:
        upstream_control.reconnect_requested = True
        upstream_control.replay_request_state = older_replay
        reader_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            reader_cancelled.set()
            raise

    account_create_lease = object()

    async def acquire_create_lease(**_kwargs: object) -> object:
        await reader_started.wait()
        create_lease_acquired.set()
        return account_create_lease

    async def release_lease(lease: object | None) -> None:
        released_leases.append(lease)

    async def fail_pending(
        *_args: object,
        pending_requests: deque[proxy_service._WebSocketRequestState],
        **_kwargs: object,
    ) -> None:
        cleanup_attempts.append([state.request_id for state in pending_requests])
        for state in list(pending_requests):
            gate = state.response_create_gate
            if gate is not None:
                await websocket_mixin._release_websocket_response_create_gate(state, gate)
        pending_requests.clear()

    claim_unsent = AsyncMock(wraps=websocket_mixin._claim_unsent_websocket_request_for_reconnect)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_routing_strategy", lambda _settings: "usage_weighted")
    monkeypatch.setattr(proxy_service, "_enforce_response_create_size_limit", lambda _request_state: None)
    monkeypatch.setattr(websocket_mixin, "effective_account_concurrency_caps", lambda _settings: object())
    monkeypatch.setattr(websocket_mixin, "_claim_unsent_websocket_request_for_reconnect", claim_unsent)
    monkeypatch.setattr(service, "_websocket_continuity_state_for_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_prepare_websocket_response_create_request", prepare_request)
    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", acquire_admission)
    monkeypatch.setattr(service, "_connect_proxy_websocket", connect_upstream)
    monkeypatch.setattr(service, "_relay_upstream_websocket_messages", blocking_reconnect_reader)
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", acquire_create_lease)
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", fail_pending)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_lease)

    scope_task = asyncio.create_task(
        service.proxy_responses_websocket(
            cast(WebSocket, _Downstream()),
            {},
            codex_session_affinity=False,
            openai_cache_affinity=False,
            api_key=None,
        )
    )
    await asyncio.wait_for(create_lease_acquired.wait(), timeout=1)
    await asyncio.sleep(0)

    scope_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(scope_task, timeout=1)

    assert reader_cancelled.is_set()
    claim_unsent.assert_not_awaited()
    assert upstream.sent_text == []
    assert [request_id for attempt in cleanup_attempts for request_id in attempt] == [
        older_replay.request_id,
        current.request_id,
    ]
    assert captured_response_create_gate is not None and captured_response_create_gate.locked() is False
    assert current.account_response_create_lease is None
    assert released_leases.count(account_create_lease) == 1


async def _release_after(event: asyncio.Event, delay_seconds: float) -> None:
    await asyncio.sleep(delay_seconds)
    event.set()
