"""Idle HTTP bridge sessions release their account stream lease between turns."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.core.errors import openai_error
from app.db.models import AccountStatus
from app.modules.api_keys.service import ApiKeyRequestUsageBudget
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import request_submit as http_bridge_request_submit_module
from app.modules.proxy.load_balancer import LoadBalancer

pytestmark = pytest.mark.unit


def _make_bridge_session(
    *, queued_request_count: int = 0, api_key_id: str | None = None
) -> proxy_service._HTTPBridgeSession:
    session_key = proxy_service._HTTPBridgeSessionKey("session_header", "idle-lease-test", api_key_id)
    return proxy_service._HTTPBridgeSession(
        key=session_key,
        headers={"x-codex-session-id": "idle-lease-test"},
        affinity=proxy_service._AffinityPolicy(
            key="idle-lease-test",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.2",
        account=cast(Any, SimpleNamespace(id="acc-bridge", status=AccountStatus.ACTIVE, plan_type="plus")),
        upstream=cast(UpstreamWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=queued_request_count,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


def _make_lease(lease_id: str) -> proxy_service.AccountLease:
    return proxy_service.AccountLease(lease_id=lease_id, account_id="acc-bridge", kind="stream", acquired_at=0.0)


@pytest.mark.asyncio
async def test_idle_session_releases_stream_lease() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    lease = _make_lease("l1")
    session.account_lease = lease
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(release_account_lease=AsyncMock()))

    await mixin._maybe_release_idle_http_bridge_session_lease(fake_self, session)

    assert session.account_lease is None
    fake_self._load_balancer.release_account_lease.assert_awaited_once_with(lease)


@pytest.mark.asyncio
async def test_idle_session_release_defers_reader_cancellation() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    lease = _make_lease("l-idle-cancel")
    session.account_lease = lease
    release_started = asyncio.Event()
    finish_release = asyncio.Event()

    async def release_account_lease(released_lease: proxy_service.AccountLease) -> None:
        assert released_lease is lease
        release_started.set()
        await finish_release.wait()

    fake_self = SimpleNamespace(
        _load_balancer=SimpleNamespace(release_account_lease=AsyncMock(side_effect=release_account_lease))
    )
    release_task = asyncio.create_task(mixin._maybe_release_idle_http_bridge_session_lease(fake_self, session))
    await release_started.wait()
    release_task.cancel()
    release_task.cancel()
    await asyncio.sleep(0)
    assert not release_task.done()

    finish_release.set()
    with pytest.raises(asyncio.CancelledError):
        await release_task

    assert session.account_lease is None
    fake_self._load_balancer.release_account_lease.assert_awaited_once_with(lease)


@pytest.mark.asyncio
async def test_busy_or_closed_session_keeps_stream_lease() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    lease = _make_lease("l2")
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(release_account_lease=AsyncMock()))

    busy = _make_bridge_session(queued_request_count=1)
    busy.account_lease = lease
    await mixin._maybe_release_idle_http_bridge_session_lease(fake_self, busy)
    assert busy.account_lease is lease

    closed = _make_bridge_session()
    closed.account_lease = lease
    closed.closed = True
    await mixin._maybe_release_idle_http_bridge_session_lease(fake_self, closed)
    assert closed.account_lease is lease

    fake_self._load_balancer.release_account_lease.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_turn_reacquires_stream_lease() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    assert session.account_lease is None
    lease = _make_lease("l3")
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(acquire_account_lease=AsyncMock(return_value=lease)))

    async with session.pending_lock:
        await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, session)

    assert session.account_lease is lease
    fake_self._load_balancer.acquire_account_lease.assert_awaited_once_with(
        "acc-bridge",
        kind="stream",
        estimated_tokens=0.0,
        api_key_id=None,
        api_key_stream_fair_share_threshold_pct=0,
    )


@pytest.mark.asyncio
async def test_reacquire_carries_turn_usage_budget_estimate() -> None:
    """Reacquisition passes the turn's usage-budget token estimate to the lease.

    Initial selection and reconnect feed the lease's estimated tokens into
    capacity-weighted routing pressure; the idle-reacquire path must do the
    same so large turns on reused warm sessions stay visible to it.
    """
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    lease = _make_lease("l10")
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(acquire_account_lease=AsyncMock(return_value=lease)))
    budget = ApiKeyRequestUsageBudget(input_tokens=1200, output_tokens=300)
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-budget-estimate",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        skip_request_log=True,
        request_usage_budget=budget,
    )
    expected_tokens = proxy_service._estimated_lease_tokens_from_request_usage_budget(budget)
    assert expected_tokens > 0.0

    async with session.pending_lock:
        await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, session, request_state=request_state)

    assert session.account_lease is lease
    fake_self._load_balancer.acquire_account_lease.assert_awaited_once_with(
        "acc-bridge",
        kind="stream",
        estimated_tokens=expected_tokens,
        api_key_id=None,
        api_key_stream_fair_share_threshold_pct=0,
    )


@pytest.mark.asyncio
async def test_keyed_warm_session_reacquire_is_fair_share_gated_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warm bridge session reacquires join per-key accounting and the fair-share gate.

    Regression for the review P2: ``acquire_account_lease`` previously had no
    ``api_key_id`` parameter, so keyed turns on reused bridge sessions took
    uncounted stream capacity and bypassed the congestion gate entirely.
    """
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    balancer = LoadBalancer(cast(Any, None))
    monkeypatch.setattr(
        http_bridge_request_submit_module,
        "_service_get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(proxy_api_key_fair_share_congestion_threshold_pct=50))
        ),
    )
    # The reacquire is pinned to the session's account, so the fair-share
    # pool is that single account: C = 8 (default stream cap). key-hot holds
    # 4 and key-other holds 1 -> T = 5, 500 >= 400 -> congested;
    # share = max(2, 8 // 2 active keys) = 4, so key-hot is at its share.
    async with balancer._runtime_lock:
        for _ in range(4):
            balancer._acquire_account_lease_locked(
                "acc-bridge", kind="stream", estimated_tokens=0.0, api_key_id="key-hot"
            )
        balancer._acquire_account_lease_locked(
            "acc-bridge", kind="stream", estimated_tokens=0.0, api_key_id="key-other"
        )
    fake_self = SimpleNamespace(_load_balancer=balancer)

    hot_session = _make_bridge_session(api_key_id="key-hot")
    with pytest.raises(ProxyResponseError) as exc_info:
        async with hot_session.pending_lock:
            await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, hot_session)

    assert exc_info.value.status_code == 429
    assert exc_info.value.payload["error"]["code"] == "api_key_stream_fair_share"
    assert hot_session.account_lease is None
    runtime = balancer._runtime["acc-bridge"]
    # The denial neither installed a lease nor perturbed the accounting.
    assert runtime.inflight_streams == 5
    assert runtime.stream_key_inflight == {"key-hot": 4, "key-other": 1}

    # A light key on the same congested pool is under the minimum guarantee:
    # its reacquire admits and is counted into the per-key map.
    light_session = _make_bridge_session(api_key_id="key-light")
    async with light_session.pending_lock:
        await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, light_session)

    assert light_session.account_lease is not None
    assert light_session.account_lease.api_key_id == "key-light"
    assert runtime.inflight_streams == 6
    assert runtime.stream_key_inflight == {"key-hot": 4, "key-other": 1, "key-light": 1}


@pytest.mark.asyncio
async def test_reacquire_denial_raises_local_cap_envelope() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(acquire_account_lease=AsyncMock(return_value=None)))

    with pytest.raises(ProxyResponseError) as exc_info:
        async with session.pending_lock:
            await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, session)

    assert exc_info.value.status_code == 429
    assert exc_info.value.payload["error"]["code"] == "account_stream_cap"
    assert session.account_lease is None


@pytest.mark.asyncio
async def test_reacquire_racing_close_releases_fresh_lease() -> None:
    """A close during the acquire await must not strand a lease on the closed session.

    _close_http_bridge_session does not take pending_lock before settling the
    session's lease, so it can set session.closed while acquire_account_lease
    is suspended. The freshly acquired lease must be returned and the turn
    must fail with the closed-bridge envelope instead of installing a lease
    that no cleanup path would ever release.
    """
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    lease = _make_lease("l-race")

    async def acquire_and_close(*_args: object, **_kwargs: object) -> proxy_service.AccountLease:
        session.closed = True
        return lease

    release_account_lease = AsyncMock()
    fake_self = SimpleNamespace(
        _load_balancer=SimpleNamespace(
            acquire_account_lease=AsyncMock(side_effect=acquire_and_close),
            release_account_lease=release_account_lease,
        )
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        async with session.pending_lock:
            await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, session)

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    assert session.account_lease is None
    release_account_lease.assert_awaited_once_with(lease)


@pytest.mark.asyncio
async def test_reacquire_racing_close_defers_cancellation_until_release() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    lease = _make_lease("l-close-cancel")
    release_started = asyncio.Event()
    finish_release = asyncio.Event()

    async def acquire_and_close(*_args: object, **_kwargs: object) -> proxy_service.AccountLease:
        session.closed = True
        return lease

    async def release_account_lease(released_lease: proxy_service.AccountLease) -> None:
        assert released_lease is lease
        release_started.set()
        await finish_release.wait()

    fake_self = SimpleNamespace(
        _load_balancer=SimpleNamespace(
            acquire_account_lease=AsyncMock(side_effect=acquire_and_close),
            release_account_lease=AsyncMock(side_effect=release_account_lease),
        )
    )

    async def reacquire() -> None:
        async with session.pending_lock:
            await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, session)

    reacquire_task = asyncio.create_task(reacquire())
    await release_started.wait()
    reacquire_task.cancel()
    reacquire_task.cancel()
    await asyncio.sleep(0)
    assert not reacquire_task.done()

    finish_release.set()
    with pytest.raises(asyncio.CancelledError):
        await reacquire_task

    assert session.account_lease is None
    fake_self._load_balancer.release_account_lease.assert_awaited_once_with(lease)


@pytest.mark.asyncio
async def test_reacquire_noop_when_lease_already_held() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    lease = _make_lease("l4")
    session.account_lease = lease
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(acquire_account_lease=AsyncMock()))

    async with session.pending_lock:
        await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, session)

    assert session.account_lease is lease
    fake_self._load_balancer.acquire_account_lease.assert_not_awaited()


@pytest.mark.asyncio
async def test_response_create_admission_failure_releases_reacquired_stream_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session()
    lease = _make_lease("l5")
    acquire_account_lease = AsyncMock(return_value=lease)
    release_account_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "acquire_account_lease", acquire_account_lease)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)

    async def assert_prewarm_has_stream_lease(*_args: object, **_kwargs: object) -> None:
        assert session.account_lease is lease

    prewarm = AsyncMock(side_effect=assert_prewarm_has_stream_lease)
    monkeypatch.setattr(service, "_maybe_prewarm_http_bridge_session", prewarm)
    monkeypatch.setattr(
        service,
        "_inline_http_bridge_image_urls",
        AsyncMock(return_value='{"type":"response.create","model":"gpt-5.2","input":"hi"}'),
    )
    monkeypatch.setattr(
        service,
        "_acquire_request_state_response_create_admission",
        AsyncMock(
            side_effect=ProxyResponseError(
                429,
                openai_error(
                    "account_response_create_cap",
                    "Account response-create capacity is exhausted.",
                    error_type="rate_limit_error",
                ),
            )
        ),
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-admission-failure",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.2","input":"hi"}',
        transport="http",
        skip_request_log=True,
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=8,
        )

    assert exc_info.value.payload["error"]["code"] == "account_response_create_cap"
    acquire_account_lease.assert_awaited_once_with(
        "acc-bridge",
        kind="stream",
        estimated_tokens=0.0,
        api_key_id=None,
        api_key_stream_fair_share_threshold_pct=0,
    )
    prewarm.assert_awaited_once()
    release_account_lease.assert_awaited_once_with(lease)
    assert session.account_lease is None
    assert session.queued_request_count == 0
    assert session.admission_waiter_count == 0


@pytest.mark.asyncio
async def test_final_lease_check_failure_removes_admission_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session()
    lease = _make_lease("l-final-check")
    release_account_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)
    ensure_calls = 0

    async def ensure_then_deny(*_args: object, **_kwargs: object) -> None:
        nonlocal ensure_calls
        ensure_calls += 1
        if ensure_calls == 1:
            session.account_lease = lease
            return
        raise ProxyResponseError(
            429,
            openai_error(
                "account_stream_cap",
                "Account stream capacity is exhausted; wait for active streams to finish.",
                error_type="rate_limit_error",
            ),
        )

    monkeypatch.setattr(service, "_ensure_http_bridge_session_stream_lease_locked", ensure_then_deny)
    monkeypatch.setattr(service, "_maybe_prewarm_http_bridge_session", AsyncMock())
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-final-lease-check",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.2","input":"hi"}',
        transport="http",
        skip_request_log=True,
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=8,
        )

    assert exc_info.value.payload["error"]["code"] == "account_stream_cap"
    assert ensure_calls == 2
    assert session.admission_waiter_count == 0
    assert session.queued_request_count == 0
    assert session.account_lease is None
    release_account_lease.assert_awaited_once_with(lease)


@pytest.mark.asyncio
async def test_stale_finalizer_cannot_release_lease_reacquired_for_new_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A previous turn's finalizer must not steal the lease reacquired for a new turn.

    The submit registers as an admission waiter atomically with the first
    reacquire, so a stale finalizer running during prewarm sees a non-idle
    session and leaves the fresh lease alone. Without that registration the
    finalizer releases the lease and the second ensure has to acquire again
    (observable as a second acquire_account_lease call).
    """
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session()
    lease = _make_lease("l8")
    acquire_account_lease = AsyncMock(return_value=lease)
    release_account_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "acquire_account_lease", acquire_account_lease)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)

    async def stale_finalizer_during_prewarm(*_args: object, **_kwargs: object) -> None:
        # Simulates the previous turn's terminal path / stream finalizer
        # unwinding between the first reacquire and queue admission.
        await service._maybe_release_idle_http_bridge_session_lease(session)
        assert session.account_lease is lease

    monkeypatch.setattr(
        service, "_maybe_prewarm_http_bridge_session", AsyncMock(side_effect=stale_finalizer_during_prewarm)
    )
    monkeypatch.setattr(
        service,
        "_inline_http_bridge_image_urls",
        AsyncMock(return_value='{"type":"response.create","model":"gpt-5.2","input":"hi"}'),
    )
    monkeypatch.setattr(
        service,
        "_acquire_request_state_response_create_admission",
        AsyncMock(
            side_effect=ProxyResponseError(
                429,
                openai_error(
                    "account_response_create_cap",
                    "Account response-create capacity is exhausted.",
                    error_type="rate_limit_error",
                ),
            )
        ),
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-stale-finalizer-race",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.2","input":"hi"}',
        transport="http",
        skip_request_log=True,
    )

    with pytest.raises(ProxyResponseError):
        await service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=8,
        )

    # One acquisition only: the stale finalizer never released the lease, so
    # the ensure at queue admission did not have to acquire a second time.
    acquire_account_lease.assert_awaited_once_with(
        "acc-bridge",
        kind="stream",
        estimated_tokens=0.0,
        api_key_id=None,
        api_key_stream_fair_share_threshold_pct=0,
    )
    # The admission-failure cleanup settles the lease exactly once.
    release_account_lease.assert_awaited_once_with(lease)
    assert session.account_lease is None
    assert session.admission_waiter_count == 0
    assert session.queued_request_count == 0


@pytest.mark.asyncio
async def test_prewarm_failure_retires_closed_session_after_last_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session()
    service._http_bridge_sessions[session.key] = session
    lease = _make_lease("l-prewarm-failure")
    acquire_account_lease = AsyncMock(return_value=lease)
    release_account_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "acquire_account_lease", acquire_account_lease)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", AsyncMock())

    async def fail_reader_during_prewarm(*_args: object, **_kwargs: object) -> None:
        retired = await service._fail_http_bridge_reader_and_maybe_retire(
            session,
            error_code="stream_incomplete",
            error_message="prewarm upstream closed",
        )
        assert retired is False
        assert service._http_bridge_sessions[session.key] is session
        raise RuntimeError("prewarm failed")

    monkeypatch.setattr(
        service,
        "_maybe_prewarm_http_bridge_session",
        AsyncMock(side_effect=fail_reader_during_prewarm),
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-prewarm-failure",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.2","input":"hi"}',
        transport="http",
        skip_request_log=True,
    )

    with pytest.raises(RuntimeError, match="prewarm failed"):
        await service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=8,
        )

    acquire_account_lease.assert_awaited_once_with(
        "acc-bridge",
        kind="stream",
        estimated_tokens=0.0,
        api_key_id=None,
        api_key_stream_fair_share_threshold_pct=0,
    )
    release_account_lease.assert_awaited_once_with(lease)
    assert session.admission_waiter_count == 0
    assert session.account_lease is None
    assert session.key not in service._http_bridge_sessions


@pytest.mark.asyncio
async def test_prewarm_cancellation_cannot_interrupt_waiter_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session()
    lease = _make_lease("l-prewarm-cancel")
    acquire_account_lease = AsyncMock(return_value=lease)
    release_account_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "acquire_account_lease", acquire_account_lease)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)

    prewarm_started = asyncio.Event()
    hold_prewarm = asyncio.Event()

    async def wait_in_prewarm(*_args: object, **_kwargs: object) -> None:
        prewarm_started.set()
        await hold_prewarm.wait()

    monkeypatch.setattr(service, "_maybe_prewarm_http_bridge_session", AsyncMock(side_effect=wait_in_prewarm))
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    original_cleanup = service._cleanup_http_bridge_submit_interruption

    async def delayed_cleanup(
        cleanup_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        gate_acquired: bool,
        request_enqueued: bool,
        counted_in_queue: bool,
        admission_waiter_registered: bool = False,
    ) -> None:
        cleanup_started.set()
        await finish_cleanup.wait()
        await original_cleanup(
            cleanup_session,
            request_state=request_state,
            gate_acquired=gate_acquired,
            request_enqueued=request_enqueued,
            counted_in_queue=counted_in_queue,
            admission_waiter_registered=admission_waiter_registered,
        )

    monkeypatch.setattr(service, "_cleanup_http_bridge_submit_interruption", delayed_cleanup)
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-prewarm-cancel",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.2","input":"hi"}',
        transport="http",
        skip_request_log=True,
    )
    submit_task = asyncio.create_task(
        service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=8,
        )
    )

    await prewarm_started.wait()
    submit_task.cancel()
    await cleanup_started.wait()
    submit_task.cancel()
    finish_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await submit_task

    acquire_account_lease.assert_awaited_once_with(
        "acc-bridge",
        kind="stream",
        estimated_tokens=0.0,
        api_key_id=None,
        api_key_stream_fair_share_threshold_pct=0,
    )
    release_account_lease.assert_awaited_once_with(lease)
    assert session.admission_waiter_count == 0
    assert session.account_lease is None


@pytest.mark.asyncio
async def test_queue_full_submit_unregisters_admission_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session(queued_request_count=1)
    lease = _make_lease("l9")
    session.account_lease = lease
    release_account_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)
    monkeypatch.setattr(service, "_maybe_prewarm_http_bridge_session", AsyncMock())
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-queue-full",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.2","input":"hi"}',
        transport="http",
        skip_request_log=True,
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=request_state.request_text or "{}",
            queue_limit=1,
        )

    assert exc_info.value.payload["error"]["code"] == "bridge_queue_full"
    assert session.admission_waiter_count == 0
    # The session still has queued work, so its lease is retained.
    assert session.account_lease is lease
    release_account_lease.assert_not_awaited()


@pytest.mark.asyncio
async def test_upstream_terminal_drain_releases_abandoned_session_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    lease = _make_lease("l6")
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-upstream-terminal-drain",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        response_id="resp-upstream-terminal-drain",
        awaiting_response_created=False,
        event_queue=None,
        transport="http",
        skip_request_log=True,
        draining_until_terminal=True,
    )
    session = _make_bridge_session()
    session.pending_requests.append(request_state)
    session.account_lease = lease
    release_account_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)
    monkeypatch.setattr(service, "_finalize_websocket_request_state", AsyncMock())
    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", AsyncMock())

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": request_state.response_id,
                    "object": "response",
                    "status": "completed",
                    "output": [],
                },
            }
        ),
    )

    assert not session.pending_requests
    assert session.account_lease is None
    release_account_lease.assert_awaited_once_with(lease)


@pytest.mark.asyncio
async def test_grouped_terminal_error_releases_abandoned_session_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grouped terminal errors on detached requests must release the idle lease.

    Two detached follow-ups (event_queue=None, draining) sharing the same
    previous_response_id are settled together by the grouped
    previous_response_not_found branch, which returns before the single
    terminal path's release hook; no downstream stream finalizer remains,
    so the branch itself must release the now-idle session's stream lease.
    """
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    lease = _make_lease("l7")
    session = _make_bridge_session()
    for index in range(2):
        session.pending_requests.append(
            proxy_service._WebSocketRequestState(
                request_id=f"req-grouped-{index}",
                model="gpt-5.2",
                service_tier=None,
                reasoning_effort=None,
                api_key_reservation=None,
                started_at=1.0,
                previous_response_id="resp-grouped-prev",
                awaiting_response_created=False,
                event_queue=None,
                transport="http",
                skip_request_log=True,
                draining_until_terminal=True,
            )
        )
    session.account_lease = lease
    release_account_lease = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_account_lease)
    finalize = AsyncMock()
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "code": "previous_response_not_found",
                "message": "Previous response with id 'resp-grouped-prev' not found.",
                "param": "previous_response_id",
            }
        ),
    )

    assert not session.pending_requests
    assert finalize.await_count == 2
    assert session.upstream_control.reconnect_requested is True
    assert session.account_lease is None
    release_account_lease.assert_awaited_once_with(lease)
