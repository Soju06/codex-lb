"""Scheduler ownership of the API-key usage lifecycle spawns and waits.

``_ApiKeyUsageMixin`` detaches settlement, fallback release, reservation
heartbeats and cancel-safe cleanups as tasks and backs off between release
retries. Every one of those sites goes through the owner's scheduler so a
simulation owns them; under the real defaults they are the same
``asyncio.create_task`` / ``asyncio.wait_for`` / ``asyncio.sleep`` calls as
before. These tests pin the ownership with a recording virtual scheduler:
each spawn is visible to the scheduler, progress happens only on virtual
time, and ``cancel_owned_tasks`` leaves nothing alive.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.balancer.types import UpstreamError
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service import api_key_usage as api_key_usage_module
from app.modules.proxy._service import support
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


class _RecordingVirtualScheduler(VirtualScheduler):
    def __init__(self, clock: VirtualClock) -> None:
        super().__init__(clock)
        self.task_names: list[str | None] = []
        self.task_coroutines: list[str] = []
        self.sleeps: list[float] = []
        self.wait_for_timeouts: list[float | None] = []

    def create_task(self, coroutine: Any, *, name: str | None = None) -> asyncio.Task[Any]:
        self.task_names.append(name)
        self.task_coroutines.append(getattr(getattr(coroutine, "cr_code", None), "co_name", type(coroutine).__name__))
        return super().create_task(coroutine, name=name)

    async def sleep(self, delay: float, result: Any = None) -> Any:
        self.sleeps.append(delay)
        return await super().sleep(delay, result=result)

    async def wait_for(self, awaitable: Awaitable[Any], timeout: float | None) -> Any:
        self.wait_for_timeouts.append(timeout)
        return await super().wait_for(awaitable, timeout)


class _RepoContext:
    def __init__(self, repos: Any) -> None:
        self._repos = repos

    async def __aenter__(self) -> Any:
        return self._repos

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _api_key() -> ApiKeyData:
    return ApiKeyData(
        id="key-virtual",
        name="key-virtual",
        key_prefix="sk-virtual",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        last_used_at=None,
    )


def _reservation() -> ApiKeyUsageReservationData:
    return ApiKeyUsageReservationData(reservation_id="resv-virtual", key_id="key-virtual", model="gpt-5.5")


def _fake_api_keys_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    release: Callable[[str], Awaitable[None]] | None = None,
    touch: Callable[[str], Awaitable[bool]] | None = None,
) -> None:
    class FakeApiKeysService:
        def __init__(self, api_keys_repository: object) -> None:
            del api_keys_repository

        async def release_usage_reservation(self, reservation_id: str) -> None:
            assert release is not None, "unexpected release"
            await release(reservation_id)

        async def finalize_usage_reservation(self, reservation_id: str, **_kwargs: object) -> None:
            raise AssertionError(f"unexpected finalize for {reservation_id}")

        async def touch_usage_reservation(self, reservation_id: str) -> bool:
            assert touch is not None, "unexpected touch"
            return await touch(reservation_id)

    monkeypatch.setattr(proxy_service, "ApiKeysService", FakeApiKeysService)


def _service(scheduler: _RecordingVirtualScheduler) -> proxy_service.ProxyService:
    repos = SimpleNamespace(api_keys=object())
    return proxy_service.ProxyService(
        cast(Any, lambda: _RepoContext(repos)),
        clock=scheduler.clock,
        scheduler=scheduler,
    )


def _request_state(clock: VirtualClock) -> proxy_service._WebSocketRequestState:
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-virtual",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=_reservation(),
        started_at=clock.monotonic(),
    )
    # The dataclass default stamps the wall clock; a virtual turn stamps the
    # owner clock, as the constructing request paths do with ``started_at``.
    request_state.api_key_reservation_last_touch_at = clock.monotonic()
    return request_state


@pytest.mark.asyncio
async def test_schedule_cancel_safe_cleanup_spawns_scheduler_owned_task() -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock())
    service = _service(scheduler)
    started = asyncio.Event()
    release = asyncio.Event()
    completed: list[str] = []

    async def cleanup() -> None:
        started.set()
        await release.wait()
        completed.append("done")

    service._schedule_cancel_safe_cleanup(cleanup(), action="release_stream_api_key_reservation", request_id="req-1")

    assert scheduler.task_names == ["proxy-release_stream_api_key_reservation-req-1"]
    (task,) = scheduler.owned_tasks
    assert task in service._background_cleanup_tasks
    await scheduler.drain()
    assert started.is_set()
    assert not task.done()

    release.set()
    await scheduler.drain()

    assert completed == ["done"]
    assert task.done()
    assert not service._background_cleanup_tasks
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_cancel_owned_tasks_leaves_no_live_cleanup_task() -> None:
    """The 08-10 failure mode: a fire-and-forget cleanup outliving the simulation."""

    scheduler = _RecordingVirtualScheduler(VirtualClock())
    service = _service(scheduler)
    never = asyncio.Event()

    async def cleanup() -> None:
        await never.wait()

    service._schedule_cancel_safe_cleanup(cleanup(), action="release_after_cancel", request_id="req-2")
    await scheduler.drain()
    (task,) = scheduler.owned_tasks
    assert task in service._background_cleanup_tasks

    await scheduler.cancel_owned_tasks()

    assert task.cancelled()
    assert not service._background_cleanup_tasks
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_detached_stream_settlement_is_scheduler_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock())
    service = _service(scheduler)
    released: list[str] = []

    async def release(reservation_id: str) -> None:
        released.append(reservation_id)

    _fake_api_keys_service(monkeypatch, release=release)
    settlement = support._StreamSettlement(status="failed", model="gpt-5.5")

    settled = await service._settle_stream_api_key_usage(_api_key(), _reservation(), settlement, "req-settle")

    assert settled is True
    assert settlement.usage_settlement_transferred is True
    assert scheduler.task_names == ["proxy-stream-api-key-settle-req-settle"]
    assert scheduler.task_coroutines == ["_settle_once"]
    assert released == []
    await scheduler.drain()
    assert released == ["resv-virtual"]
    assert not service._background_cleanup_tasks
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_ordering_sensitive_settlement_fallback_is_scheduler_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock())
    service = _service(scheduler)
    attempts: list[str] = []

    async def release(reservation_id: str) -> None:
        attempts.append(reservation_id)
        if len(attempts) == 1:
            raise RuntimeError("reservation persistence unavailable")

    _fake_api_keys_service(monkeypatch, release=release)
    settlement = support._StreamSettlement(status="failed", model="gpt-5.5")

    settled = await service._settle_stream_api_key_usage(
        _api_key(), _reservation(), settlement, "req-ordered", wait_for_settlement=True
    )

    assert settled is True
    assert attempts == ["resv-virtual", "resv-virtual"]
    assert scheduler.task_names == [
        "proxy-stream-api-key-settle-req-ordered",
        "proxy-stream-api-key-fallback-req-ordered",
    ]
    await scheduler.drain()
    assert not service._background_cleanup_tasks
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_unsettled_release_backs_off_on_virtual_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry backoff sleeps through the scheduler: no progress without ``advance``."""

    scheduler = _RecordingVirtualScheduler(VirtualClock())
    service = _service(scheduler)
    attempts: list[str] = []

    async def release(reservation_id: str) -> None:
        attempts.append(reservation_id)
        if len(attempts) < 3:
            raise RuntimeError("reservation persistence unavailable")

    _fake_api_keys_service(monkeypatch, release=release)
    task = scheduler.create_task(
        service._release_unsettled_stream_api_key_usage(
            api_key=_api_key(),
            api_key_reservation=_reservation(),
            request_id="req-retry",
            retry_persistence_failures=True,
        )
    )

    await scheduler.drain()
    assert len(attempts) == 1
    assert scheduler.sleeps == [api_key_usage_module._STREAM_API_KEY_RELEASE_RETRY_BASE_SECONDS]
    assert not task.done()

    await scheduler.advance(api_key_usage_module._STREAM_API_KEY_RELEASE_RETRY_BASE_SECONDS)
    assert len(attempts) == 2
    assert scheduler.sleeps == [0.1, 0.2]
    assert not task.done()

    await scheduler.advance(0.2)

    assert len(attempts) == 3
    assert await task is True
    assert service._stream_api_key_release_retry_semaphore._value == (  # noqa: SLF001
        api_key_usage_module._STREAM_API_KEY_RELEASE_RETRY_MAX_CONCURRENCY
    )


@pytest.mark.asyncio
async def test_reservation_heartbeat_waits_and_touches_on_virtual_time(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = VirtualClock(monotonic_value=1_000.0)
    scheduler = _RecordingVirtualScheduler(clock)
    service = _service(scheduler)
    touches: list[str] = []

    async def touch(reservation_id: str) -> bool:
        touches.append(reservation_id)
        return True

    _fake_api_keys_service(monkeypatch, touch=touch)
    request_state = _request_state(clock)
    heartbeat_seconds = api_key_usage_module._api_key_reservation_heartbeat_seconds()

    service._start_request_state_api_key_reservation_heartbeat(request_state, api_key=_api_key(), surface="websocket")

    heartbeat_task = request_state.api_key_reservation_heartbeat_task
    assert heartbeat_task is not None
    assert heartbeat_task in scheduler.owned_tasks
    assert scheduler.task_coroutines == ["_run_api_key_reservation_heartbeat"]
    await scheduler.drain()
    assert scheduler.wait_for_timeouts == [heartbeat_seconds]

    await scheduler.advance(heartbeat_seconds - 1.0)
    assert touches == []
    await scheduler.advance(1.0)

    assert touches == ["resv-virtual"]
    # The next heartbeat wait is re-armed through the scheduler as well.
    assert scheduler.wait_for_timeouts == [heartbeat_seconds, heartbeat_seconds]
    assert not heartbeat_task.done()

    service._cancel_request_state_api_key_reservation_heartbeat(request_state)
    await scheduler.drain()

    assert heartbeat_task.done()
    assert request_state.api_key_reservation_heartbeat_task is None
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_maybe_touch_api_key_reservation_reads_owner_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = VirtualClock(monotonic_value=1_000.0)
    scheduler = _RecordingVirtualScheduler(clock)
    service = _service(scheduler)
    touches: list[str] = []

    async def touch(reservation_id: str) -> bool:
        touches.append(reservation_id)
        return True

    _fake_api_keys_service(monkeypatch, touch=touch)
    # A wall clock far ahead of the virtual one: any leak to ``time.monotonic``
    # would make the "too recent" touch below fire and return the wrong stamp.
    monkeypatch.setattr(time, "monotonic", lambda: 1_000_000.0)
    heartbeat_seconds = api_key_usage_module._api_key_reservation_heartbeat_seconds()

    recent = await service._maybe_touch_api_key_reservation(
        api_key=_api_key(),
        reservation=_reservation(),
        last_touch_at=1_000.0 - heartbeat_seconds + 1.0,
        request_id="req-touch",
        surface="websocket",
    )
    due = await service._maybe_touch_api_key_reservation(
        api_key=_api_key(),
        reservation=_reservation(),
        last_touch_at=1_000.0 - heartbeat_seconds,
        request_id="req-touch",
        surface="websocket",
    )

    assert recent == 1_000.0 - heartbeat_seconds + 1.0
    assert due == 1_000.0
    assert touches == ["resv-virtual"]


@pytest.mark.asyncio
async def test_deferred_keyed_stream_health_apply_task_is_scheduler_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock())
    service = _service(scheduler)
    applied: list[tuple[str, str]] = []

    async def handle_stream_error(account: Any, error: Any, code: str, http_status: int | None = None) -> None:
        del error, http_status
        applied.append((account.id, code))

    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)
    request_state = _request_state(scheduler.clock)
    request_state.deferred_keyed_stream_health.append(
        support._DeferredKeyedStreamHealthPenalty(
            account=cast(Any, SimpleNamespace(id="acc-deferred")),
            error=UpstreamError(message="overloaded"),
            code="upstream_unavailable",
        )
    )

    await service._drain_deferred_keyed_stream_health(request_state)

    assert applied == [("acc-deferred", "upstream_unavailable")]
    assert len(scheduler.task_names) == 1
    assert request_state.deferred_keyed_stream_health == []
    assert not scheduler.owned_tasks
