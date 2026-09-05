"""Streaming retry/stream-once timing sites are owned by the injected scheduler.

``streaming/retry.py`` and ``streaming/mixin.py`` spawn settlement/close
owners, back off between transient retries, chunk capacity-recovery waits and
stamp latencies. Under ``VirtualScheduler`` each of those must park on a
virtual timer or be registered as an owned task; under the real defaults they
are the same ``asyncio``/``time`` calls as before.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, AsyncIterator, Coroutine, cast
from unittest.mock import AsyncMock

import pytest

from app.core.balancer.types import UpstreamError
from app.core.crypto import TokenEncryptor
from app.core.openai.requests import ResponsesRequest
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.streaming import retry as streaming_retry_module
from app.modules.proxy._service.support import _TransientStreamError
from app.modules.proxy.capability_lineage_repository import CapabilityLineageRepository
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


class _RecordingScheduler(VirtualScheduler):
    """Virtual scheduler that remembers which coroutines it was asked to own."""

    def __init__(self, clock: VirtualClock) -> None:
        super().__init__(clock)
        self.spawned: list[str] = []

    def create_task(self, coroutine: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]:
        self.spawned.append(name or getattr(coroutine, "__qualname__", repr(coroutine)))
        return super().create_task(coroutine, name=name)


class _RequestLogsRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def add_log(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


class _RepoContext:
    def __init__(self, request_logs: _RequestLogsRecorder) -> None:
        capability_lineage = AsyncMock(spec=CapabilityLineageRepository)
        capability_lineage.is_required.return_value = False
        capability_lineage.require.return_value = ("test-marker",)
        accounts = AsyncMock()
        accounts.get_by_id_fresh.return_value = None
        self._repos = ProxyRepositories(
            accounts=cast(AccountsRepository, accounts),
            usage=cast(UsageRepository, AsyncMock()),
            request_logs=cast(RequestLogsRepository, request_logs),
            sticky_sessions=cast(StickySessionsRepository, AsyncMock()),
            api_keys=cast(ApiKeysRepository, AsyncMock()),
            additional_usage=cast(AdditionalUsageRepository, AsyncMock()),
            capability_lineage=capability_lineage,
        )

    async def __aenter__(self) -> ProxyRepositories:
        return self._repos

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


def _repo_factory(request_logs: _RequestLogsRecorder) -> proxy_service.ProxyRepoFactory:
    def factory() -> _RepoContext:
        return _RepoContext(request_logs)

    return factory


class _SettingsCache:
    def __init__(self, settings: object) -> None:
        self._settings = settings

    async def get(self) -> object:
        return self._settings


def _make_proxy_settings() -> SimpleNamespace:
    return SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        prefer_earlier_reset_window="secondary",
        sticky_threads_enabled=False,
        sticky_reallocation_budget_threshold_pct=95.0,
        upstream_stream_transport="default",
        openai_cache_affinity_max_age_seconds=300,
        openai_prompt_cache_key_derivation_enabled=True,
        routing_strategy="usage_weighted",
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        http_responses_session_bridge_gateway_safe_mode=False,
        trace_channels=frozenset(),
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_account_response_create_limit=4,
        proxy_account_stream_limit=8,
        proxy_account_stream_recovery_reserve=1,
        proxy_api_key_fair_share_congestion_threshold_pct=0,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
        proxy_admission_wait_timeout_seconds=10.0,
        max_sse_event_bytes=16 * 1024 * 1024,
        http_responses_session_bridge_instance_id="test-instance",
        http_responses_session_bridge_instance_ring=[],
        http_responses_session_bridge_anchor_poison_failure_threshold=7,
        http_downstream_transport_policy="smart",
    )


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    now = utcnow()
    return Account(
        id=account_id,
        chatgpt_account_id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-token"),
        refresh_token_encrypted=encryptor.encrypt("refresh-token"),
        id_token_encrypted=encryptor.encrypt("id-token"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _make_api_key_data(key_id: str) -> ApiKeyData:
    return ApiKeyData(
        id=key_id,
        name=key_id,
        key_prefix=f"sk-{key_id[:8]}",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )


def _virtual_service(
    request_logs: _RequestLogsRecorder,
) -> tuple[proxy_service.ProxyService, VirtualClock, _RecordingScheduler]:
    clock = VirtualClock(monotonic_value=1_000.0)
    scheduler = _RecordingScheduler(clock)
    service = proxy_service.ProxyService(_repo_factory(request_logs), clock=clock, scheduler=scheduler)
    return service, clock, scheduler


def _payload_retry_after_seconds(event: str) -> int:
    return int(json.loads(event.split("data: ", 1)[1])["retry_after_seconds"])


@pytest.mark.asyncio
async def test_account_capacity_recovery_wait_chunks_on_the_injected_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = VirtualClock(monotonic_value=100.0)
    scheduler = VirtualScheduler(clock)
    monkeypatch.setattr(streaming_retry_module, "_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS", 10.0)
    events: list[str] = []

    async def consume() -> None:
        async for event in streaming_retry_module._iter_account_capacity_recovery_wait(
            request_id="req_virtual_capacity_wait",
            model="gpt-5.5",
            account_id="account-virtual-capacity",
            error_message="Account stream concurrency limit reached",
            recovery_sleep_seconds=25.0,
            remaining_budget_seconds=60.0,
            emit_keepalives=True,
            stage="selection",
            scheduler=scheduler,
            clock=clock,
        ):
            events.append(event)

    consumer = scheduler.create_task(consume())
    try:
        await scheduler.drain()
        assert not consumer.done()
        assert [_payload_retry_after_seconds(event) for event in events] == [25]
        # Heartbeat chunks park on virtual timers, never on wall-clock sleeps.
        assert scheduler.pending_timers == 1

        await scheduler.advance(10.0)
        assert [_payload_retry_after_seconds(event) for event in events] == [25, 15]

        await scheduler.advance(10.0)
        assert [_payload_retry_after_seconds(event) for event in events] == [25, 15, 5]
        assert not consumer.done()

        await scheduler.advance(5.0)
        await consumer

        assert clock.monotonic() == pytest.approx(125.0)
        assert scheduler.pending_timers == 0
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_transient_retry_backoff_and_stream_close_owners_use_the_injected_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_proxy_settings()
    request_logs = _RequestLogsRecorder()
    service, clock, scheduler = _virtual_service(request_logs)
    account = _make_account("acc_virtual_backoff")
    attempts = 0

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(streaming_retry_module, "backoff_seconds", lambda _attempt: 2.5)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget_compatible",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(side_effect=lambda account, **_k: account))
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())
    monkeypatch.setattr(service, "_write_request_log", AsyncMock())
    monkeypatch.setattr(service._load_balancer, "record_success", AsyncMock())
    monkeypatch.setattr(service._load_balancer, "record_errors", AsyncMock())

    async def fake_stream_once(_account: Account, *_args: object, **_kwargs: object) -> AsyncIterator[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _TransientStreamError(
                "server_error",
                cast(UpstreamError, {"message": "upstream hiccup", "code": "server_error"}),
            )
        yield 'data: {"type":"response.completed","response":{"id":"resp_virtual_backoff"}}\n\n'

    monkeypatch.setattr(service, "_stream_once", fake_stream_once)
    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in service._stream_with_retry(
                payload,
                {"session_id": "sid-virtual-backoff"},
                codex_session_affinity=False,
                propagate_http_errors=False,
                openai_cache_affinity=False,
                api_key=None,
                api_key_reservation=None,
                suppress_text_done_events=False,
                request_transport="http",
                upstream_stream_transport_override="http",
            )
        ]

    consumer = scheduler.create_task(collect())
    try:
        await scheduler.drain()
        # The first attempt failed and the retry is parked on the backoff timer.
        assert attempts == 1
        assert not consumer.done()
        assert scheduler.pending_timers == 1

        await scheduler.advance(2.0)
        assert attempts == 1
        assert not consumer.done()

        await scheduler.advance(0.5)
        chunks = await consumer

        assert attempts == 2
        assert any("response.completed" in chunk for chunk in chunks)
        assert clock.monotonic() == pytest.approx(1_002.5)
        # Both attempts closed their inner stream through an owned task.
        assert [name for name in scheduler.spawned if name.startswith("stream-inner-close-")] != []
        assert all(task.done() for task in scheduler.owned_tasks)
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_stream_once_api_key_heartbeat_is_scheduler_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    request_logs = _RequestLogsRecorder()
    service, clock, scheduler = _virtual_service(request_logs)
    account = _make_account("acc_virtual_heartbeat")
    api_key = _make_api_key_data("key_virtual_heartbeat")
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_virtual_heartbeat",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    heartbeat_stop_events: list[asyncio.Event] = []

    async def fake_heartbeat(**kwargs: object) -> None:
        stop_event = cast(asyncio.Event, kwargs["stop_event"])
        heartbeat_stop_events.append(stop_event)
        await stop_event.wait()

    async def fake_stream(
        payload: object,
        headers: object,
        access_token: object,
        account_id: object,
        base_url: object = None,
        raise_for_status: bool = False,
        enforce_openai_sdk_contract: bool = True,
    ) -> AsyncIterator[str]:
        del payload, headers, access_token, account_id, base_url, raise_for_status, enforce_openai_sdk_contract
        assert heartbeat_stop_events, "the reservation heartbeat must be running before the upstream stream starts"
        yield 'data: {"type":"response.completed","response":{"id":"resp_virtual_heartbeat"}}\n\n'

    monkeypatch.setattr(service, "_run_api_key_reservation_heartbeat", fake_heartbeat)
    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True})

    chunks = [
        chunk
        async for chunk in service._stream_once(
            account,
            payload,
            {"session_id": "sid-virtual-heartbeat"},
            "req_virtual_heartbeat",
            False,
            request_started_at=clock.monotonic(),
            api_key=api_key,
            api_key_reservation=reservation,
            settlement=proxy_service._StreamSettlement(),
            suppress_text_done_events=False,
            upstream_stream_transport=None,
            request_transport="http",
        )
    ]

    assert any("response.completed" in chunk for chunk in chunks)
    assert [name for name in scheduler.spawned if name.endswith("fake_heartbeat")] != []
    assert len(heartbeat_stop_events) == 1
    assert heartbeat_stop_events[0].is_set()
    await scheduler.drain()
    assert scheduler.owned_tasks == frozenset()
    assert await service.drain_persistence_tasks(timeout_seconds=1.0)
