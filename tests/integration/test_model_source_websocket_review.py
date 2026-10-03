"""Regressions for routing, terminal settlement, timeout and drain review findings."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from sqlalchemy import select

from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from tests.integration.model_source_helpers import stub_source_upstreams
from tests.integration.test_model_source_websocket import complete, create_key, create_source, websocket_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_source_quota_rejection_retains_error_and_releases_admission(async_client, app_instance):
    from app.db.models import ApiKeyLimit
    from app.modules.proxy.source_admission import get_source_bulkhead

    sid = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, sid)
    response = await async_client.patch(
        f"/api/api-keys/{key['id']}",
        json={"limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1}]},
    )
    assert response.status_code == 200, response.text
    async with SessionLocal() as session:
        limit = (await session.execute(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key["id"]))).scalar_one()
        limit.current_value = limit.max_value
        await session.commit()
    async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
        await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hello " * 100})
        event = await ws.receive()
        assert event["status"] == 429
        assert event["error"]["code"] == "rate_limit_exceeded"
    assert get_source_bulkhead().in_flight(sid) == 0
    async with SessionLocal() as session:
        assert not (await session.execute(select(ApiKeyUsageReservation))).scalars().all()


@pytest.mark.parametrize("ending", ["drain", "disconnect"])
@pytest.mark.parametrize("phase", ["settlement", "acquisition"])
async def test_source_closes_scope_before_stalled_quota(async_client, app_instance, monkeypatch, ending, phase):
    import app.modules.proxy.api as api
    import app.modules.proxy.source_websocket as native
    from app.core import shutdown
    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.source_admission import get_source_bulkhead

    entered, release, provider_closed, scope_closed = (asyncio.Event() for _ in range(4))
    operation = api._settle_source_reservation if phase == "settlement" else api._enforce_request_limits
    calls = 0
    provider_connections = 0

    async def slow_settle(*args, **kwargs):
        nonlocal calls
        calls += 1
        acquired = await operation(*args, **kwargs) if phase == "acquisition" else None
        entered.set()
        await release.wait()
        return acquired if phase == "acquisition" else await operation(*args, **kwargs)

    monkeypatch.setattr(
        api, "_settle_source_reservation" if phase == "settlement" else "_enforce_request_limits", slow_settle
    )
    monkeypatch.setattr(native, "_SESSION_CLEANUP_TIMEOUT_SECONDS", 0.05)

    async def observed_app(scope, receive, send):
        try:
            await app_instance(scope, receive, send)
        finally:
            scope_closed.set()

    async def handler(request):
        nonlocal provider_connections
        provider_connections += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        await complete(ws)
        async for _ in ws:
            pass
        provider_closed.set()
        return ws

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        service = get_proxy_service_for_app(app_instance)
        try:
            async with websocket_client(observed_app, "/v1/responses", key["key"]) as ws:
                await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
                if phase == "settlement":
                    for _ in range(3):
                        await ws.receive()
                await asyncio.wait_for(entered.wait(), 5)
                if ending == "drain":
                    shutdown.begin_drain(0, deadline_monotonic=0)
                else:
                    await ws.disconnect()
                assert await asyncio.wait_for(ws.receive_close(), 1.5) == (1012 if ending == "drain" else 1000)
                if phase == "settlement":
                    await asyncio.wait_for(provider_closed.wait(), 1.5)
                await asyncio.wait_for(scope_closed.wait(), 1.5)
                assert service._background_cleanup_tasks
                assert not release.is_set()
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*service._background_cleanup_tasks), 5)
        assert calls == 1
        assert provider_connections == (1 if phase == "settlement" else 0)
        assert get_source_bulkhead().in_flight(sid) == 0
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            logs = (await session.execute(select(RequestLog).where(RequestLog.model_source_id == sid))).scalars().all()
        assert [row.status for row in reservations] == ["finalized" if phase == "settlement" else "released"]
        assert [row.status for row in logs] == ["success" if phase == "settlement" else "cancelled"]


async def test_source_withheld_warmup_terminal_is_not_charged(async_client, app_instance, monkeypatch):
    from app.modules.proxy.source_ownership import SourceOwnershipRecorder

    blocked = asyncio.Event()
    record = SourceOwnershipRecorder.record_event

    async def withhold(self, event):
        if event.get("type") == "response.completed":
            blocked.set()
            await asyncio.Event().wait()
        await record(self, event)

    monkeypatch.setattr(SourceOwnershipRecorder, "record_event", withhold)

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        await complete(ws, warmup=True, usage=False)
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi", "generate": False})
            assert (await ws.receive())["type"] == "response.created"
            await asyncio.wait_for(blocked.wait(), 5)
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert [row.status for row in reservations] == ["released"]


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("carrier", ["parent", "session", "turn", "previous", "override"])
async def test_source_inherited_capability_prevents_dispatch(async_client, app_instance, path, carrier):
    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.capability_lineage import CapabilityLineageAlias
    from app.modules.proxy.capability_routing import RoutingCapability, RoutingIntent

    calls = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        calls.append(await ws.receive_json())
        await complete(ws)
        async for _ in ws:
            pass
        return ws

    value = "restricted-source-lineage"
    payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
    headers = []
    metadata = None
    if carrier in {"parent", "override"}:
        kind = "codex_task"
        client_metadata = {"x-codex-parent-thread-id": value}
        if carrier == "override":
            metadata = json.dumps({"source_request_overrides": {"client_metadata": client_metadata}})
        else:
            payload["client_metadata"] = client_metadata
    elif carrier == "previous":
        kind = "previous_response"
        payload["previous_response_id"] = value
    else:
        kind = "session_header" if carrier == "session" else "turn_state"
        header = b"session_id" if carrier == "session" else b"x-codex-turn-state"
        headers.append((header, value.encode()))
    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(handler, shutdown_timeout=1), metadata=metadata)
        key = await create_key(async_client, sid)
        service = get_proxy_service_for_app(app_instance)
        await service._capability_router.route(
            RoutingIntent.requiring(RoutingCapability.TRUSTED_CYBER),
            api_key_id=key["id"],
            aliases=(CapabilityLineageAlias(kind=kind, value=value),),
        )
        async with websocket_client(app_instance, path, key["key"], extra_headers=headers) as ws:
            await ws.send(payload)
            event = await ws.receive()
            assert event["type"] in {"error", "response.failed"}
        assert not calls
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            source_logs = (
                (await session.execute(select(RequestLog).where(RequestLog.model_source_id == sid))).scalars().all()
            )
        # Inherited work follows the existing subscription capability path,
        # whose reservation is released when no authorized account exists.
        assert [row.status for row in reservations] == ([] if carrier == "override" else ["released"])
        assert not source_logs


async def test_source_reused_socket_rechecks_capability_lineage(async_client, app_instance):
    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.capability_lineage import CapabilityLineageAlias
    from app.modules.proxy.capability_routing import RoutingCapability, RoutingIntent

    calls = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            calls.append(json.loads(message.data))
            await complete(ws)
        return ws

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send(payload)
            for _ in range(3):
                await ws.receive()
            await get_proxy_service_for_app(app_instance)._capability_router.route(
                RoutingIntent.requiring(RoutingCapability.TRUSTED_CYBER),
                api_key_id=key["id"],
                aliases=(CapabilityLineageAlias(kind="previous_response", value="resp_native"),),
            )
            await ws.send({**payload, "previous_response_id": "resp_native"})
            assert (await ws.receive())["error"]["code"] == "websocket_reconnect_required"
        assert len(calls) == 1
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert [row.status for row in reservations] == ["finalized"]


async def test_source_lineage_lookup_failure_is_closed(async_client, app_instance, monkeypatch):
    from app.dependencies import get_proxy_service_for_app

    sid = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, sid)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("private lineage database failure")

    monkeypatch.setattr(get_proxy_service_for_app(app_instance)._capability_router, "route", unavailable)
    async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
        await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
        event = await ws.receive()
        assert event["error"]["code"] == "model_source_lookup_failed"
        assert "private lineage" not in json.dumps(event)
    async with SessionLocal() as session:
        assert not (await session.execute(select(ApiKeyUsageReservation))).scalars().all()


@pytest.mark.parametrize(
    "terminal,usage",
    [
        ("response.failed", False),
        ("response.failed", True),
        ("response.completed", False),
        ("response.completed", True),
    ],
)
@pytest.mark.parametrize("ending", ["disconnect", "timeout", "write_error"])
async def test_source_terminal_handoff_settlement(async_client, app_instance, monkeypatch, terminal, usage, ending):
    import app.modules.proxy.source_websocket as native

    scope_closed = asyncio.Event()
    sent_events = []
    original_send = native.SourceWebSocketSession.send

    async def bounded_send(self, event, **kwargs):
        if ending == "timeout" and event.get("type") == terminal:
            self.stream_idle_timeout = 0.1
        return await original_send(self, event, **kwargs)

    monkeypatch.setattr(native.SourceWebSocketSession, "send", bounded_send)

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        await ws.send_json(
            {"type": "response.created", "response": {"id": "review_terminal", "status": "in_progress", "output": []}}
        )
        if terminal == "response.failed":
            await ws.send_json({"type": "response.output_text.delta", "delta": "partial"})
        response = {
            "id": "review_terminal",
            "status": "failed" if terminal == "response.failed" else "completed",
            "output": [],
        }
        if usage:
            response["usage"] = {"input_tokens": 7, "output_tokens": 3}
        if terminal == "response.failed":
            response["error"] = {"code": "provider_failure", "message": "no answer"}
        await ws.send_json({"type": terminal, "response": response})
        async for _ in ws:
            pass
        return ws

    async def transport(scope, receive, send):
        async def intercepted_send(message):
            await send(message)
            if message["type"] == "websocket.send":
                sent_events.append(json.loads(message["text"])["type"])
            if message["type"] == "websocket.send" and json.loads(message["text"]).get("type") == terminal:
                if ending == "write_error":
                    raise OSError("downstream transport closed after delivery")
                # The peer sees the terminal while send still has not returned.
                await asyncio.Event().wait()

        try:
            await app_instance(scope, receive, intercepted_send)
        finally:
            scope_closed.set()

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        async with websocket_client(transport, "/v1/responses", key["key"]) as ws:
            payload = {"type": "response.create", "model": "source-ws-model", "input": "hello"}
            if terminal == "response.completed" and not usage:
                payload["generate"] = False
            await ws.send(payload)
            while (await ws.receive())["type"] != terminal:
                pass
            if ending == "write_error":
                await asyncio.wait_for(scope_closed.wait(), 5)
            elif ending == "timeout":
                assert await ws.receive_close() == 1000
        assert sent_events.count(terminal) == 1
        assert "error" not in sent_events
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            logs = (await session.execute(select(RequestLog))).scalars().all()
        expected = "released" if terminal == "response.failed" else "finalized"
        assert len(reservations) == 1 and reservations[0].status == expected
        assert len(logs) == 1
        assert logs[0].status == ("error" if terminal == "response.failed" else "success")


async def test_source_retry_preparation_deadline(async_client, app_instance, monkeypatch):
    import app.modules.proxy.source_websocket as sws
    from app.core.config.settings import get_settings
    from app.modules.model_sources.forwarding import ModelSourceForwardingError
    from app.modules.model_sources.repository import ModelSourcesRepository
    from tests.integration.model_source_helpers import _create_model_source

    entered = asyncio.Event()
    block = asyncio.Event()
    seen_first_failure = False

    async def failed_open(source, *, timeout):
        nonlocal seen_first_failure
        seen_first_failure = True
        raise ModelSourceForwardingError(
            status_code=502, payload={"error": {"code": "connect_failed", "message": "test"}}, connection_failed=True
        )

    original_list = ModelSourcesRepository.list_responses_sources_for_model

    async def delayed_list(self, *args, **kwargs):
        if seen_first_failure:
            entered.set()
            await block.wait()
        return await original_list(self, *args, **kwargs)

    sid = await create_source(async_client, "http://127.0.0.1:9/v1")
    sid2 = await _create_model_source(
        async_client, name="second", model="source-ws-model", base_url="http://127.0.0.1:8/v1", supports_responses=True
    )
    await async_client.patch(f"/api/model-sources/{sid2}", json={"supportsResponsesWebsocket": True})
    key = await create_key(async_client, sid)
    await async_client.patch(f"/api/api-keys/{key['id']}", json={"assignedSourceIds": [sid, sid2]})
    monkeypatch.setattr(get_settings(), "http_responses_stream_request_budget_seconds", 1.5)
    configured = await async_client.put(
        "/api/settings", json={"proxyRequestBudgetSeconds": 10, "upstreamConnectTimeoutSeconds": 0.1}
    )
    assert configured.status_code == 200, configured.text
    monkeypatch.setattr(sws, "open_source_websocket", failed_open)
    monkeypatch.setattr(ModelSourcesRepository, "list_responses_sources_for_model", delayed_list)
    async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
        await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
        await asyncio.wait_for(entered.wait(), 5)
        event = await asyncio.wait_for(ws.receive(), 3)
        assert event["error"]["code"] == "model_source_timeout"


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_source_invalid_subscription_frame_keeps_socket(async_client, app_instance, monkeypatch, path):
    from app.modules.proxy.service import ProxyService
    from tests.integration.test_proxy_websocket_responses import _SequencedUpstreamWebSocket, _websocket_response_batch
    from tests.unit.test_proxy_utils import _make_account

    account = _make_account("review-account")
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()
    upstream = _SequencedUpstreamWebSocket([], deferred_message_batches=[_websocket_response_batch("review-ok")])

    async def connect(self, *args, **kwargs):
        return account, upstream

    monkeypatch.setattr(ProxyService, "_connect_proxy_websocket", connect)
    async with websocket_client(app_instance, path) as ws:
        await ws.send({"type": "response.create", "model": "gpt-5.4", "input": 42})
        first = await ws.receive()
        assert first["type"] == "error"
        await ws.send({"type": "response.create", "model": "gpt-5.4", "input": "valid now"})
        assert (await ws.receive())["type"] == "response.created"


@pytest.mark.parametrize(
    "path,expected",
    [("/backend-api/codex/responses", "previous_response_not_found"), ("/v1/responses", "stream_incomplete")],
)
@pytest.mark.parametrize("envelope", ["nested", "flat", "failed"])
async def test_source_continuity_error_code(async_client, app_instance, path, expected, envelope):

    sockets = 0

    async def handler(request):
        nonlocal sockets
        sockets += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        payload = await ws.receive_json()
        if payload.get("previous_response_id"):
            error = {
                "code": "previous_response_not_found",
                "type": "invalid_request_error",
                "message": "Previous response review_anchor not found on private-provider.example with secret-key",
            }
            if envelope == "failed":
                event = {"type": "response.failed", "response": {"id": "failed_turn", "error": error}}
            elif envelope == "flat":
                event = {**error, "type": "error", "status_code": 400}
            else:
                event = {"type": "error", "status": 400, "error": error}
            await ws.send_json(event)
        else:
            await complete(ws, response_id="review_anchor")
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send(payload)
            for _ in range(3):
                await ws.receive()
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send({**payload, "previous_response_id": "review_anchor"})
            event = await ws.receive()
            error = event["response"]["error"] if envelope == "failed" else event["error"]
            assert error["code"] == expected
            serialized = json.dumps(event)
            assert all(private not in serialized for private in ("review_anchor", "private-provider", "secret-key"))
        assert sockets == 2


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("forced", [False, True])
async def test_source_forced_model_handshake_route(async_client, app_instance, monkeypatch, path, forced):
    from app.modules.proxy._service import support

    source_id = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, source_id)
    if forced:
        response = await async_client.patch(f"/api/api-keys/{key['id']}", json={"enforcedModel": "gpt-5.4"})
        assert response.status_code == 200, response.text
    monkeypatch.setattr(support, "upstream_websocket_transport_recently_failed", lambda: True)
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"localhost"), (b"authorization", f"Bearer {key['key']}".encode())],
        "scheme": "ws",
        "server": ("127.0.0.1", 80),
        "client": ("127.0.0.1", 1234),
        "subprotocols": [],
        "extensions": {"websocket.http.response": {}},
    }
    task = asyncio.create_task(app_instance(scope, incoming.get, outgoing.put))
    try:
        await incoming.put({"type": "websocket.connect"})
        event = await asyncio.wait_for(outgoing.get(), 5)
        if forced:
            assert event["type"] == "websocket.http.response.start" and event["status"] == 426
        else:
            assert event["type"] == "websocket.accept"
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 5)


async def test_source_dashboard_timeouts_apply_to_source_session(async_client, app_instance, monkeypatch):
    import app.modules.proxy.source_websocket as sws
    from app.core.config.settings import get_settings
    from tests.integration.test_model_source_websocket import complete

    received = {}
    original_init = sws.SourceWebSocketSession.__init__

    def capture(self, *args, **kwargs):
        received.update({k: kwargs[k] for k in ("request_timeout", "connect_timeout", "idle_timeout")})
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(sws.SourceWebSocketSession, "__init__", capture)
    configured = await async_client.put(
        "/api/settings",
        json={
            "proxyRequestBudgetSeconds": 900,
            "upstreamConnectTimeoutSeconds": 16,
            "proxyDownstreamWebsocketIdleTimeoutSeconds": 0.25,
        },
    )
    assert configured.status_code == 200, configured.text

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        await complete(ws)
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            for _ in range(3):
                await ws.receive()
            try:
                code = await asyncio.wait_for(ws.receive_close(), 1.25)
            except TimeoutError:
                pytest.fail(
                    "Source socket remains open beyond dashboard idle timeout; captured settings=" + repr(received)
                )
            assert code == 1000
            assert received == {
                "request_timeout": get_settings().http_responses_stream_request_budget_seconds,
                "connect_timeout": 16,
                "idle_timeout": 0.25,
            }


async def test_timed_out_source_closes_before_admission_release(async_client, app_instance, monkeypatch):
    import app.modules.proxy.source_websocket as native

    monkeypatch.setattr(native, "SOURCE_FIRST_FRAME_DEADLINE_SECONDS", 0.08)
    error_send_started, first_closed = asyncio.Event(), asyncio.Event()
    active = 0
    max_active = 0
    received = 0

    async def observed_app(scope, receive, send):
        async def slow_error(message):
            if message["type"] == "websocket.send" and json.loads(message["text"])["type"] == "error":
                error_send_started.set()
                await asyncio.Event().wait()
            await send(message)

        await app_instance(scope, receive, slow_error)

    async def handler(request):
        nonlocal active, max_active, received
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        received += 1
        turn_number = received
        active += 1
        max_active = max(max_active, active)
        if turn_number == 2:
            await complete(ws, response_id="resp_second")
        async for _ in ws:
            pass
        active -= 1
        if turn_number == 1:
            first_closed.set()
        return ws

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(handler, shutdown_timeout=1))
        result = await async_client.patch(f"/api/model-sources/{sid}", json={"maxConcurrency": 1})
        assert result.status_code == 200
        key = await create_key(async_client, sid)
        async with websocket_client(observed_app, "/v1/responses", key["key"]) as first:
            payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
            await first.send(payload)
            await asyncio.wait_for(error_send_started.wait(), 5)
            async with websocket_client(app_instance, "/v1/responses", key["key"]) as second:
                await second.send(payload)
                for _ in range(3):
                    await second.receive()
    assert max_active == 1, f"Configured source maxConcurrency=1, actual outstanding upstream generations={max_active}"
