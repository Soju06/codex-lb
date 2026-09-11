from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core import shutdown as shutdown_state
from app.core.utils.time import utcnow
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.health.api import internal_drain_status
from app.modules.proxy.service import ProxyService

pytestmark = pytest.mark.unit


def _request(service: object) -> Request:
    return Request(
        {
            "type": "http",
            "client": ("127.0.0.1", 50000),
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(proxy_service=service)),
        }
    )


def _observe_now(request: Request) -> dict[str, str]:
    observation = internal_drain_status(request)
    try:
        observation.send(None)
    except StopIteration as completed:
        return completed.value.checks
    finally:
        observation.close()
    pytest.fail("Persistence observation must not yield")


@pytest.mark.parametrize("result", ["success", "failure", "cancel", "exception"])
async def test_drain_retains_done_settlement_until_callback_handoff(monkeypatch, result):
    service = ProxyService(AsyncMock())
    release_entered = asyncio.Event()
    release_allowed = asyncio.Event()
    callback_seen = asyncio.Event()
    observations = []
    release_calls = 0

    async def release(**kwargs):
        nonlocal release_calls
        release_calls += 1
        release_entered.set()
        await release_allowed.wait()
        return True

    monkeypatch.setattr(service, "_release_unsettled_stream_api_key_usage", release)
    key = ApiKeyData(
        id="key",
        name="fixture",
        key_prefix="fixture",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    reservation = ApiKeyUsageReservationData(reservation_id="reservation", key_id="key", model="fixture")

    async def settle():
        if result == "cancel":
            raise asyncio.CancelledError
        if result == "exception":
            raise RuntimeError("unexpected settlement failure")
        return result == "success"

    task = asyncio.create_task(settle(), name="proxy-stream-api-key-settle-fixture")
    request = _request(service)

    def before_handoff(done):
        assert done.done()
        assert sum(not owner.done() for owner in service._background_cleanup_tasks) == 0
        assert len(service._background_cleanup_tasks) == 1
        observations.append(_observe_now(request))
        callback_seen.set()

    task.add_done_callback(before_handoff)
    service._track_stream_usage_settlement_task(
        task,
        api_key=key,
        api_key_reservation=reservation,
        request_id="fixture",
    )
    try:
        await callback_seen.wait()
        assert observations[0].get("request_persistence_state") == "pending"
        assert observations[0]["request_persistence_pending"] == "1"
        if result in {"failure", "cancel"}:
            await release_entered.wait()
            assert _observe_now(request)["request_persistence_state"] == "pending"
            assert release_calls == 1
        release_allowed.set()
        assert await service.drain_persistence_tasks(timeout_seconds=1)
        assert _observe_now(request)["request_persistence_state"] == "drained"
        assert _observe_now(request)["request_persistence_pending"] == "0"
        assert release_calls == (1 if result in {"failure", "cancel"} else 0)
    finally:
        release_allowed.set()
        await asyncio.gather(task, return_exceptions=True)
        await service.drain_persistence_tasks(timeout_seconds=1)


@pytest.mark.parametrize("value", [None, True, -1, 1.5, "0"])
async def test_drain_invalid_persistence_observation_is_unknown(value):
    service = SimpleNamespace(request_persistence_pending_nowait=lambda: value)
    checks = _observe_now(_request(service))
    assert checks["request_persistence_state"] == "unknown"
    assert "request_persistence_pending" not in checks


@pytest.mark.parametrize("failure", ["missing_service", "missing_method", "noncallable", "exception"])
async def test_drain_unavailable_persistence_observation_is_unknown(failure):
    def broken():
        raise RuntimeError("private details")

    service = {
        "missing_service": None,
        "missing_method": SimpleNamespace(),
        "noncallable": SimpleNamespace(request_persistence_pending_nowait=0),
        "exception": SimpleNamespace(request_persistence_pending_nowait=broken),
    }[failure]
    checks = _observe_now(_request(service))
    assert checks["request_persistence_state"] == "unknown"
    assert "request_persistence_pending" not in checks
    assert "private details" not in str(checks)


async def test_drain_observation_deduplicates_owned_work_without_waiting_or_mutation(caplog):
    service = ProxyService(AsyncMock())
    event = asyncio.Event()
    task = asyncio.create_task(event.wait(), name="proxy-request-log-fixture")
    unrelated = asyncio.create_task(event.wait(), name="http-bridge-close-fixture")
    service._request_log_tasks.add(cast(asyncio.Task[None], task))
    service._background_cleanup_tasks.update({cast(asyncio.Task[None], task), cast(asyncio.Task[None], unrelated)})
    before = shutdown_state.is_draining()
    try:
        for _ in range(3):
            checks = _observe_now(_request(service))
            assert checks["request_persistence_state"] == "pending"
            assert checks["request_persistence_pending"] == "1"
        assert not task.done() and not unrelated.done()
        assert shutdown_state.is_draining() is before
        assert not caplog.records
    finally:
        event.set()
        await asyncio.gather(task, unrelated)
        service._request_log_tasks.clear()
        service._background_cleanup_tasks.clear()
