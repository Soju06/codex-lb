"""Route regressions for preparation ownership, active deadlines and quota refresh."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from aiohttp import web
from sqlalchemy import select

from app.core import shutdown
from app.core.clients.proxy import ProxyResponseError
from app.core.clock import RealClock
from app.core.errors import openai_error
from app.core.utils.time import utcnow
from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService
from app.modules.model_sources.forwarding import ModelSourceForwardingError
from app.modules.model_sources.repository import ModelSourcesRepository
from app.modules.proxy import api
from app.modules.proxy import source_websocket as native
from app.modules.proxy.service import ProxyService
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.proxy.source_ownership import SourceOwnershipRecorder
from app.modules.proxy.source_pool import SourcePool
from tests.integration.model_source_helpers import _create_model_source, stub_source_upstreams
from tests.integration.test_model_source_websocket import complete, create_key, create_source, websocket_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROUTES = ["/v1/responses", "/backend-api/codex/responses"]


class _AdvanceableClock(RealClock):
    offset = 0.0

    def monotonic(self):
        return super().monotonic() + self.offset


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("reference", [{"file_id": "file_missing"}, {"image_url": "sediment://file_missing"}])
async def test_source_can_follow_invalid_subscription_only_input(async_client, app_instance, path, reference):
    calls = []

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        calls.append(await ws.receive_json())
        await complete(ws)
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send(
                {
                    "type": "response.create",
                    "model": "gpt-5.4",
                    "input": [{"role": "user", "content": [{"type": "input_image", **reference}]}],
                }
            )
            assert (await ws.receive())["error"]["code"] == "unsupported_input_image_format"
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "valid"})
            assert [(await ws.receive())["type"] for _ in range(3)] == [
                "response.created",
                "response.output_text.delta",
                "response.completed",
            ]
        assert len(calls) == 1
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert [row.status for row in reservations] == ["finalized"]
        assert get_source_bulkhead().in_flight(sid) == 0


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("profile", ["generation", "warmup", "partial"])
async def test_source_withheld_reported_usage_requires_delivery(async_client, app_instance, monkeypatch, path, profile):
    blocked, release, scope_closed = (asyncio.Event() for _ in range(3))
    frames = []
    commit = SourceOwnershipRecorder._commit
    monkeypatch.setattr(native, "_SESSION_CLEANUP_TIMEOUT_SECONDS", 0.05)

    async def paused(self, keys):
        blocked.set()
        await release.wait()
        return await commit(self, keys)

    monkeypatch.setattr(SourceOwnershipRecorder, "_commit", paused)

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        if profile == "partial":
            await ws.send_json({"type": "response.output_text.delta", "delta": "delivered"})
        await ws.send_json(
            {
                "type": "response.completed",
                "response": {
                    "id": "withheld-usage",
                    "status": "completed",
                    "output": [],
                    "usage": {"input_tokens": 7, "output_tokens": 0 if profile == "warmup" else 3},
                },
            }
        )
        async for _ in ws:
            pass
        return ws

    async def transport(scope, receive, send):
        async def observed(message):
            if message["type"] == "websocket.send":
                frames.append(json.loads(message["text"]))
            await send(message)

        try:
            await app_instance(scope, receive, observed)
        finally:
            scope_closed.set()

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        service = get_proxy_service_for_app(app_instance)
        try:
            async with websocket_client(transport, path, key["key"]) as ws:
                await ws.send(
                    {
                        "type": "response.create",
                        "model": "source-ws-model",
                        "input": "hi",
                        "generate": profile != "warmup",
                    }
                )
                await asyncio.wait_for(blocked.wait(), 15)
                if profile == "partial":
                    assert (await ws.receive())["type"] == "response.output_text.delta"
                await ws.disconnect()
                await asyncio.wait_for(scope_closed.wait(), 3)
                assert service._background_cleanup_tasks
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*service._background_cleanup_tasks), 5)
        assert [event["type"] for event in frames] == (["response.output_text.delta"] if profile == "partial" else [])
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            logs = (await session.execute(select(RequestLog).where(RequestLog.model_source_id == sid))).scalars().all()
        assert [row.status for row in reservations] == ["finalized" if profile == "partial" else "released"]
        assert [row.status for row in logs] == ["cancelled"]
        assert get_source_bulkhead().in_flight(sid) == 0


@pytest.mark.parametrize("path", ROUTES)
async def test_source_connection_retry_preserves_earliest_deadline(async_client, app_instance, monkeypatch, path):
    received = asyncio.Event()
    clock = _AdvanceableClock()
    opened = []

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        received.set()
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        first = await create_source(async_client, "http://127.0.0.1:9/v1")
        second = await _create_model_source(
            async_client,
            name="longer-timeout-native",
            model="source-ws-model",
            base_url=await start(provider, shutdown_timeout=1),
            supports_responses=True,
        )
        for sid, timeout in ((first, 60), (second, 120)):
            configured = await async_client.patch(
                f"/api/model-sources/{sid}", json={"supportsResponsesWebsocket": True, "timeoutSeconds": timeout}
            )
            assert configured.status_code == 200, configured.text
        key = await create_key(async_client, first)
        assert (
            await async_client.patch(f"/api/api-keys/{key['id']}", json={"assignedSourceIds": [first, second]})
        ).status_code == 200
        service = get_proxy_service_for_app(app_instance)
        monkeypatch.setattr(service, "_clock", clock)
        choose, opener = SourcePool.choose, native.open_source_websocket

        def prefer_first(self, sources, *, excluded):
            candidates = [source for source in sources if source.id == first] if first not in excluded else sources
            return choose(self, candidates, excluded=excluded)

        async def fail_first(source, **kwargs):
            opened.append(source.id)
            if source.id == first:
                raise ModelSourceForwardingError(
                    status_code=502,
                    payload={"error": {"code": "connect_failed", "message": "Local connection failure"}},
                    connection_failed=True,
                )
            return await opener(source, **kwargs)

        monkeypatch.setattr(SourcePool, "choose", prefer_first)
        monkeypatch.setattr(native, "open_source_websocket", fail_first)
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            await asyncio.wait_for(received.wait(), 15)
            clock.offset = 61
            event = await ws.receive()
            assert event["type"] == "error" and event["error"]["code"] == "model_source_timeout"
            assert await ws.receive_close() == 1000
        assert opened == [first, second]
        assert all(get_source_bulkhead().in_flight(sid) == 0 for sid in (first, second))
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert [row.status for row in reservations] == ["released", "released"]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("phase", ["acquisition", "ownership", "settlement"])
async def test_source_deadline_closes_before_deferred_persistence(async_client, app_instance, monkeypatch, path, phase):
    entered, release, scope_closed, provider_closed = (asyncio.Event() for _ in range(4))
    frames, calls = [], []

    clock = _AdvanceableClock()
    monkeypatch.setattr(native, "_SESSION_CLEANUP_TIMEOUT_SECONDS", 0.05)
    if phase == "acquisition":
        operation = api._enforce_request_limits

        async def paused(*args, **kwargs):
            result = await operation(*args, **kwargs)
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(api, "_enforce_request_limits", paused)
    elif phase == "settlement":
        operation = api._settle_source_reservation

        async def paused(*args, **kwargs):
            entered.set()
            await release.wait()
            return await operation(*args, **kwargs)

        monkeypatch.setattr(api, "_settle_source_reservation", paused)
    else:
        operation = SourceOwnershipRecorder._commit

        async def paused(self, keys):
            entered.set()
            await release.wait()
            return await operation(self, keys)

        monkeypatch.setattr(SourceOwnershipRecorder, "_commit", paused)

    async def transport(scope, receive, send):
        async def observed_send(message):
            if message["type"] == "websocket.send":
                frames.append(json.loads(message["text"]))
            await send(message)

        try:
            await app_instance(scope, receive, observed_send)
        finally:
            scope_closed.set()

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        calls.append(await ws.receive_json())
        await complete(ws)
        async for _ in ws:
            pass
        provider_closed.set()
        return ws

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(provider, shutdown_timeout=1))
        assert (await async_client.patch(f"/api/model-sources/{sid}", json={"timeoutSeconds": 60})).status_code == 200
        key = await create_key(async_client, sid)
        service = get_proxy_service_for_app(app_instance)
        monkeypatch.setattr(service, "_clock", clock)
        try:
            async with websocket_client(transport, path, key["key"]) as ws:
                await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
                await asyncio.wait_for(entered.wait(), 5)
                clock.offset = 61
                await asyncio.wait_for(scope_closed.wait(), 3)
                assert not release.is_set()
                assert service._background_cleanup_tasks
                assert get_source_bulkhead().in_flight(sid) == 1
                if phase != "acquisition":
                    await asyncio.wait_for(provider_closed.wait(), 1)
                assert len(calls) == (0 if phase == "acquisition" else 1)
                event_types = [event["type"] for event in frames]
                if phase == "settlement":
                    assert event_types == ["response.created", "response.output_text.delta", "response.completed"]
                else:
                    assert event_types == ["error"]
                    assert frames[0]["error"]["code"] == "model_source_timeout"
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*service._background_cleanup_tasks), 5)
        assert get_source_bulkhead().in_flight(sid) == 0
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            logs = (await session.execute(select(RequestLog).where(RequestLog.model_source_id == sid))).scalars().all()
        assert [row.status for row in reservations] == ["finalized" if phase == "settlement" else "released"]
        assert [row.status for row in logs] == ["success" if phase == "settlement" else "error"]
        assert not service._background_cleanup_tasks


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("ending", ["drain", "disconnect", "cancel", "deadline"])
async def test_source_initial_lookup_observes_session_end(async_client, app_instance, monkeypatch, path, ending):
    entered, release, scope_closed = (asyncio.Event() for _ in range(3))
    lookup = ModelSourcesRepository.list_responses_sources_for_model
    close_codes = []

    async def blocked_lookup(self, *args, **kwargs):
        entered.set()
        await release.wait()
        return await lookup(self, *args, **kwargs)

    monkeypatch.setattr(ModelSourcesRepository, "list_responses_sources_for_model", blocked_lookup)

    async def transport(scope, receive, send):
        async def observed_send(message):
            if message["type"] == "websocket.close":
                close_codes.append(message["code"])
            await send(message)

        try:
            await app_instance(scope, receive, observed_send)
        finally:
            scope_closed.set()

    sid = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, sid)
    service = get_proxy_service_for_app(app_instance)
    clock = _AdvanceableClock()
    if ending == "deadline":
        monkeypatch.setattr(service, "_clock", clock)
        monkeypatch.setattr(api.get_settings(), "http_responses_stream_request_budget_seconds", 60)
    try:
        try:
            async with websocket_client(transport, path, key["key"]) as ws:
                await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
                await asyncio.wait_for(entered.wait(), 5)
                if ending == "drain":
                    shutdown.begin_drain(0, deadline_monotonic=0)
                elif ending == "cancel":
                    ws.cancel_transport()
                elif ending == "disconnect":
                    await ws.disconnect()
                else:
                    clock.offset = 61
                await asyncio.wait_for(scope_closed.wait(), 2.5)
                assert not release.is_set()
                assert close_codes == [1012 if ending == "drain" else 1000]
        except asyncio.CancelledError:
            assert ending == "cancel"
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*service._background_cleanup_tasks), 5)
    async with SessionLocal() as session:
        assert not (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
    assert get_source_bulkhead().in_flight(sid) == 0


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("stream_budget,proxy_budget", [(7200, 60), (60, 900), (None, 60)])
async def test_source_websocket_uses_stream_budget_for_active_turn(
    async_client, app_instance, monkeypatch, path, stream_budget, proxy_budget
):
    release, deadline_checked = asyncio.Event(), asyncio.Event()

    class ObservedClock(_AdvanceableClock):
        def monotonic(self):
            task = asyncio.current_task()
            if self.offset > 60 and task is not None and task.get_name() == "source-ws-lifecycle":
                deadline_checked.set()
            return super().monotonic()

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        await ws.send_json(
            {
                "type": "response.created",
                "response": {"id": "stream-budget", "model": "source-ws-model", "status": "in_progress", "output": []},
            }
        )
        closed = asyncio.create_task(ws.receive())
        released = asyncio.create_task(release.wait())
        try:
            done, _ = await asyncio.wait({closed, released}, return_when=asyncio.FIRST_COMPLETED)
            if closed in done:
                return ws
        finally:
            closed.cancel()
            released.cancel()
            await asyncio.gather(closed, released, return_exceptions=True)
        await ws.send_json({"type": "response.completed", "response": {"id": "stream-budget", "status": "completed"}})
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(provider, shutdown_timeout=1))
        assert (
            await async_client.patch(f"/api/model-sources/{source_id}", json={"timeoutSeconds": 3600})
        ).status_code == 200
        assert (
            await async_client.put("/api/settings", json={"proxyRequestBudgetSeconds": proxy_budget})
        ).status_code == 200
        key = await create_key(async_client, source_id)
        if stream_budget is None:
            # Compatibility path for a settings provider without a dedicated
            # stream budget; the dashboard generic override must still apply.
            monkeypatch.delattr(api.get_settings(), "http_responses_stream_request_budget_seconds")
        else:
            monkeypatch.setattr(api.get_settings(), "http_responses_stream_request_budget_seconds", stream_budget)
        clock = ObservedClock()
        monkeypatch.setattr(get_proxy_service_for_app(app_instance), "_clock", clock)
        try:
            async with websocket_client(app_instance, path, key["key"]) as ws:
                await ws.send({"type": "response.create", "model": "source-ws-model", "input": "wait"})
                assert (await ws.receive())["type"] == "response.created"
                clock.offset = 61
                await asyncio.wait_for(deadline_checked.wait(), 3)
                if stream_budget == 7200:
                    release.set()
                    assert (await ws.receive())["type"] == "response.completed"
                else:
                    assert (await ws.receive())["error"]["code"] == "model_source_timeout"
                    assert await ws.receive_close() == 1000
        finally:
            release.set()
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert [row.status for row in reservations] == ["finalized" if stream_budget == 7200 else "released"]
        assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.parametrize("fail_first", [True, False])
async def test_source_heartbeat_retries_before_stale_reaping(async_client, app_instance, monkeypatch, fail_first):
    first_touch, later_touch, release = (asyncio.Event() for _ in range(3))
    touch_calls = 0
    original_touch = ApiKeysService.touch_usage_reservation
    monkeypatch.setattr(native, "_api_key_reservation_heartbeat_seconds", lambda: 0.05)
    monkeypatch.setattr(api.get_settings(), "http_responses_stream_request_budget_seconds", 8 * 3600)

    async def transient_failure(self, reservation_id):
        nonlocal touch_calls
        touch_calls += 1
        if touch_calls == 1 and fail_first:
            first_touch.set()
            raise OSError("temporary test database failure")
        result = await original_touch(self, reservation_id)
        # The control must finish its first committed touch before aging the
        # row; observing entry could mistake that same touch for a retry.
        first_touch.set()
        later_touch.set()
        return result

    monkeypatch.setattr(ApiKeysService, "touch_usage_reservation", transient_failure)

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        await ws.send_json({"type": "response.created", "response": {"id": "heartbeat", "output": []}})
        await release.wait()
        await complete(ws, response_id="heartbeat")
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(provider, shutdown_timeout=1))
        configured = await async_client.put(
            "/api/settings", json={"proxyRequestBudgetSeconds": 8 * 3600, "proxyAccountLeaseTtlSeconds": 8 * 3600}
        )
        assert configured.status_code == 200, configured.text
        assert (
            await async_client.patch(f"/api/model-sources/{sid}", json={"timeoutSeconds": 8 * 3600})
        ).status_code == 200
        key = await create_key(async_client, sid)
        try:
            async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
                await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
                await ws.receive()
                await asyncio.wait_for(first_touch.wait(), 5)
                async with SessionLocal() as session:
                    reservation = (await session.execute(select(ApiKeyUsageReservation))).scalar_one()
                    reservation.updated_at = utcnow() - timedelta(hours=7)
                    await session.commit()
                later_touch.clear()
                await asyncio.wait_for(later_touch.wait(), 2)
                async with SessionLocal() as session:
                    reaped = await ApiKeysRepository(session).release_stale_usage_reservations(
                        cutoff=utcnow() - timedelta(hours=6)
                    )
                release.set()
                while (await ws.receive())["type"] != "response.completed":
                    pass
        finally:
            release.set()
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            logs = (await session.execute(select(RequestLog).where(RequestLog.model_source_id == sid))).scalars().all()
        assert touch_calls >= 2 and reaped == 0
        assert [row.status for row in reservations] == ["finalized"]
        assert [row.status for row in logs] == ["success"]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("invalid_first", [True, False])
async def test_source_preparation_preserves_subscription_frames(
    async_client, app_instance, monkeypatch, path, invalid_first
):
    entered, release, buffered = (asyncio.Event() for _ in range(3))
    refresh = ProxyService._refresh_websocket_api_key_policy
    subscription_inputs = []
    sid = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, sid)

    async def paused_refresh(self, *args, **kwargs):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await refresh(self, *args, **kwargs)

    async def subscription(self, payload, **kwargs):
        subscription_inputs.append(payload["input"])
        raise ProxyResponseError(409, openai_error("subscription_probe", "Local subscription stub"))

    monkeypatch.setattr(ProxyService, "_refresh_websocket_api_key_policy", paused_refresh)
    monkeypatch.setattr(ProxyService, "_prepare_websocket_response_create_request", subscription)

    async def transport(scope, receive, send):
        seen = 0

        async def observed_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "websocket.receive":
                seen += 1
                if seen == 3:
                    buffered.set()
            return message

        await app_instance(scope, observed_receive, send)

    try:
        async with websocket_client(transport, path, key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "gpt-5.4", "input": 42 if invalid_first else "first"})
            await asyncio.wait_for(entered.wait(), 5)
            for text in ("second", "third"):
                await ws.send({"type": "response.create", "model": "gpt-5.4", "input": text})
            await asyncio.wait_for(buffered.wait(), 2)
            release.set()
            errors = [await ws.receive() for _ in range(3)]
            assert all(event["type"] == "error" for event in errors)
            assert [event["error"]["code"] for event in errors[1:]] == ["subscription_probe"] * 2
            assert subscription_inputs == (["second", "third"] if invalid_first else ["first", "second", "third"])
    finally:
        release.set()


@pytest.mark.parametrize("limit", ["frames", "bytes"])
async def test_source_lookup_read_ahead_is_bounded(async_client, app_instance, monkeypatch, limit):
    from app.modules.proxy import websocket_input

    entered, release, scope_closed = (asyncio.Event() for _ in range(3))
    refresh = ProxyService._refresh_websocket_api_key_policy
    monkeypatch.setattr(websocket_input, "_PREPARATION_BUFFER_FRAMES", 1 if limit == "frames" else 16)
    monkeypatch.setattr(websocket_input, "_PREPARATION_BUFFER_BYTES", 100 if limit == "bytes" else 16384)

    async def paused(self, *args, **kwargs):
        entered.set()
        await release.wait()
        return await refresh(self, *args, **kwargs)

    monkeypatch.setattr(ProxyService, "_refresh_websocket_api_key_policy", paused)

    async def transport(scope, receive, send):
        try:
            await app_instance(scope, receive, send)
        finally:
            scope_closed.set()

    sid = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, sid)
    try:
        async with websocket_client(transport, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "first"})
            await asyncio.wait_for(entered.wait(), 5)
            for _ in range(2 if limit == "frames" else 1):
                await ws.send({"type": "response.create", "model": "source-ws-model", "input": "x" * 100})
            await asyncio.wait_for(scope_closed.wait(), 2)
            assert not release.is_set()
    finally:
        release.set()
        await asyncio.gather(*get_proxy_service_for_app(app_instance)._background_cleanup_tasks)
    async with SessionLocal() as session:
        assert not (await session.execute(select(ApiKeyUsageReservation))).scalars().all()


@pytest.mark.parametrize("path", ROUTES)
async def test_source_prepare_preserves_one_waiter_and_rejects_excess(async_client, app_instance, monkeypatch, path):
    entered, resume, buffered, finish = (asyncio.Event() for _ in range(4))
    refresh = ProxyService._refresh_websocket_api_key_policy
    calls, errors = [], []

    async def paused(self, *args, **kwargs):
        if not entered.is_set():
            entered.set()
            await resume.wait()
        return await refresh(self, *args, **kwargs)

    monkeypatch.setattr(ProxyService, "_refresh_websocket_api_key_policy", paused)

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            payload = json.loads(message.data)
            calls.append(payload["instructions"])
            if len(calls) == 1:
                await finish.wait()
            await complete(ws, response_id=f"resp_buffered_{len(calls)}")
        return ws

    async def transport(scope, receive, send):
        seen = 0

        async def observed_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "websocket.receive":
                seen += 1
                if seen == 3:
                    buffered.set()
            return message

        await app_instance(scope, observed_receive, send)

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        try:
            async with websocket_client(transport, path, key["key"]) as ws:
                await ws.send(
                    {"type": "response.create", "model": "source-ws-model", "input": "hi", "instructions": "first"}
                )
                await asyncio.wait_for(entered.wait(), 5)
                for label in ("second", "third"):
                    await ws.send(
                        {"type": "response.create", "model": "source-ws-model", "input": "hi", "instructions": label}
                    )
                await asyncio.wait_for(buffered.wait(), 2)
                resume.set()
                event = await ws.receive()
                assert event["error"]["code"] == "websocket_queue_full"
                errors.append(event)
                finish.set()
                terminals = 0
                while terminals < 2:
                    event = await ws.receive()
                    if event["type"] == "error":
                        errors.append(event)
                    terminals += event["type"] == "response.completed"
        finally:
            resume.set()
            finish.set()
        assert calls == ["first", "second"] and len(errors) == 1
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert [row.status for row in reservations] == ["finalized", "finalized"]
