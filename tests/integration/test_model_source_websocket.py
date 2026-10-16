"""Exercise public ASGI WebSocket routes against a real local native upstream."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import WSMsgType, web
from sqlalchemy import select

from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from tests.integration.model_source_helpers import _create_model_source, _enable_api_key_auth, stub_source_upstreams

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@asynccontextmanager
async def websocket_client(app, path, key=None, *, extra_headers=()):
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    headers = [(b"host", b"localhost")]
    if key:
        headers.append((b"authorization", f"Bearer {key}".encode()))
    headers.extend(extra_headers)
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "scheme": "ws",
        "server": ("127.0.0.1", 80),
        "client": ("127.0.0.1", 1234),
        "subprotocols": [],
        "extensions": {"websocket.http.response": {}},
    }
    task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
    await incoming.put({"type": "websocket.connect"})
    first = await asyncio.wait_for(outgoing.get(), 5)
    assert first["type"] == "websocket.accept", first

    class Client:
        def cancel_transport(self):
            task.cancel()

        async def send(self, value):
            await self.send_text(json.dumps(value))

        async def send_text(self, value):
            await incoming.put({"type": "websocket.receive", "text": value})

        async def disconnect(self):
            await incoming.put({"type": "websocket.disconnect", "code": 1000})

        async def receive(self):
            event = await asyncio.wait_for(outgoing.get(), 5)
            assert event["type"] == "websocket.send", event
            return json.loads(event["text"])

        async def receive_close(self):
            event = await asyncio.wait_for(outgoing.get(), 5)
            assert event["type"] == "websocket.close", event
            return event["code"]

    try:
        yield Client()
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def create_source(client, url, *, enabled=True, metadata=None):
    source_id = await _create_model_source(
        client,
        name="native",
        model="source-ws-model",
        base_url=url,
        supports_responses=True,
        raw_metadata_json=metadata,
    )
    response = await client.patch(f"/api/model-sources/{source_id}", json={"supportsResponsesWebsocket": enabled})
    assert response.status_code == 200, response.text
    return source_id


async def create_key(client, source_id):
    await _enable_api_key_auth(client)
    response = await client.post(
        "/api/api-keys/",
        json={
            "name": "native-key",
            "assignedSourceIds": [source_id],
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100000}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def complete(ws, *, response_id="resp_native", model="source-ws-model", usage=True, warmup=False):
    envelope = {"id": response_id, "object": "response", "model": model, "status": "in_progress", "output": []}
    await ws.send_json({"type": "response.created", "response": envelope})
    if not warmup:
        await ws.send_json({"type": "response.output_text.delta", "delta": "hello"})
    envelope = {**envelope, "status": "completed"}
    if usage:
        envelope["usage"] = {
            "input_tokens": 7,
            "output_tokens": 0 if warmup else 3,
            "total_tokens": 7 if warmup else 10,
        }
    await ws.send_json({"type": "response.completed", "response": envelope})


@pytest.mark.parametrize(
    "path", ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses", "/backend-api/codex/responses/"]
)
async def test_source_native_two_turn_connection_and_accounting(async_client, app_instance, path):
    requests, handshakes = [], []

    async def handler(request):
        handshakes.append(dict(request.headers))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                payload = json.loads(message.data)
                requests.append(payload)
                await complete(ws, response_id=f"resp_native_{len(requests)}")
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, path, key["key"]) as ws:
            for i in range(2):
                payload = {
                    "type": "response.create",
                    "model": "source-ws-model",
                    "input": "hi",
                    "instructions": "Be concise",
                }
                if i:
                    payload["previous_response_id"] = "resp_native_1"
                await ws.send(payload)
                assert (await ws.receive())["type"] == "response.created"
                assert (await ws.receive())["delta"] == "hello"
                assert (await ws.receive())["response"]["model"] == "source-ws-model"
        assert len(handshakes) == 1
        assert len(requests) == 2
        assert requests[1]["previous_response_id"] == "resp_native_1"
        assert all(p["type"] == "response.create" and "stream" not in p for p in requests)
        assert handshakes[0]["Authorization"] == "Bearer token-native"
        assert "Chatgpt-Account-Id" not in handshakes[0]
        async with SessionLocal() as session:
            rows = (await session.execute(select(RequestLog).where(RequestLog.api_key_id == key["id"]))).scalars().all()
            reservations = (
                (
                    await session.execute(
                        select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == key["id"])
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == len(reservations) == 2
        assert all(
            row.transport == "websocket" and row.upstream_transport == "openai_compatible_websocket" for row in rows
        )
        assert all(
            row.account_id is None and row.model_source_id == source_id and row.status == "success" for row in rows
        )
        assert all(row.input_tokens == 7 and row.output_tokens == 3 for row in rows)
        assert all(res.status == "finalized" for res in reservations)


async def test_source_native_opt_in_required_before_reservation(async_client, app_instance):
    source_id = await create_source(async_client, "http://127.0.0.1:9/v1", enabled=False)
    key = await create_key(async_client, source_id)
    async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
        await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
        assert (await ws.receive())["error"]["code"] == "model_source_requires_http_transport"
    async with SessionLocal() as session:
        assert not (await session.execute(select(ApiKeyUsageReservation))).scalars().all()


@pytest.mark.parametrize(
    "mode,expected_status",
    [
        ("missing_usage", "finalized"),
        ("incomplete", "finalized"),
        ("failed", "released"),
        ("truncated", "released"),
        ("binary", "released"),
        ("malformed", "released"),
        ("warmup", "finalized"),
        ("warmup_missing_usage", "finalized"),
        ("warmup_output", "released"),
    ],
)
async def test_source_websocket_terminal_accounting(async_client, app_instance, mode, expected_status):
    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        message = await ws.receive_json()
        assert message["generate"] is (not mode.startswith("warmup"))
        if mode == "binary":
            await ws.send_bytes(b"binary")
        elif mode == "malformed":
            await ws.send_str("[]")
        elif mode == "truncated":
            await ws.close()
        elif mode == "failed":
            await ws.send_json({"type": "error", "error": {"message": "token-native https://secret.invalid"}})
        elif mode == "incomplete":
            await ws.send_json(
                {
                    "type": "response.incomplete",
                    "response": {"id": "resp_partial", "output": [], "usage": {"input_tokens": 7, "output_tokens": 3}},
                }
            )
        else:
            await complete(
                ws,
                usage=mode not in {"missing_usage", "warmup_missing_usage"},
                warmup=mode in {"warmup", "warmup_missing_usage"},
            )
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        events = []
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send(
                {
                    "type": "response.create",
                    "model": "source-ws-model",
                    "input": "hi",
                    "generate": not mode.startswith("warmup"),
                }
            )
            while True:
                event = await ws.receive()
                events.append(event)
                if event["type"] in {"response.completed", "response.incomplete", "response.failed", "error"}:
                    break
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            rows = (
                (await session.execute(select(RequestLog).where(RequestLog.model_source_id == source_id)))
                .scalars()
                .all()
            )
        assert len(reservations) == len(rows) == 1
        assert reservations[0].status == expected_status
        assert "token-native" not in json.dumps(events)
        if mode == "warmup_output":
            assert not any(event["type"] == "response.output_text.delta" for event in events)
        if mode == "warmup":
            assert rows[0].output_tokens == 0


@pytest.mark.parametrize("mutation", ["disable", "capability", "credential", "assignment", "revocation", "model"])
async def test_source_websocket_revalidates_before_second_turn(async_client, app_instance, mutation):
    calls = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                calls.append(json.loads(message.data))
                await complete(ws)
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
            await ws.send(payload)
            for _ in range(3):
                await ws.receive()
            if mutation in {"disable", "capability", "credential"}:
                patch = {
                    "disable": {"isEnabled": False},
                    "capability": {"supportsResponsesWebsocket": False},
                    "credential": {"apiKey": "new-secret"},
                }[mutation]
                result = await async_client.patch(f"/api/model-sources/{source_id}", json=patch)
            elif mutation in {"assignment", "revocation"}:
                replacement = await _create_model_source(
                    async_client, name="other", model="other-model", base_url="http://127.0.0.1:9/v1"
                )
                patch = {"assignedSourceIds": [replacement]} if mutation == "assignment" else {"isActive": False}
                result = await async_client.patch(f"/api/api-keys/{key['id']}", json=patch)
            else:
                payload["model"] = "different-model"
                result = None
            if result is not None:
                assert result.status_code == 200, result.text
            await ws.send(payload)
            assert (await ws.receive())["type"] == "error"
        assert len(calls) == 1
        async with SessionLocal() as session:
            assert len((await session.execute(select(ApiKeyUsageReservation))).scalars().all()) == 1


async def test_source_websocket_one_queued_turn_and_controls(async_client, app_instance):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                calls.append(json.loads(message.data))
                entered.set()
                await release.wait()
                await complete(ws, response_id=f"resp_queue_{len(calls)}")
        return ws

    async with stub_source_upstreams() as start:
        await create_source(async_client, await start(handler, shutdown_timeout=1))
        async with websocket_client(app_instance, "/v1/responses") as ws:
            payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
            await ws.send(payload)
            await asyncio.wait_for(entered.wait(), 3)
            await ws.send({"type": "response.cancel"})
            assert (await ws.receive())["error"]["code"] == "unsupported_operation"
            await ws.send(payload)
            await ws.send(payload)
            assert (await ws.receive())["error"]["code"] == "websocket_queue_full"
            release.set()
            assert [(await ws.receive())["type"] for _ in range(6)] == [
                "response.created",
                "response.output_text.delta",
                "response.completed",
            ] * 2
        assert len(calls) == 2


async def test_source_websocket_capability_validation(async_client):
    source_id = await create_source(async_client, "http://127.0.0.1:9/v1")
    invalid = await async_client.patch(f"/api/model-sources/{source_id}", json={"supportsResponses": False})
    assert invalid.status_code == 400
    renamed = await async_client.patch(f"/api/model-sources/{source_id}", json={"name": "renamed"})
    assert renamed.json()["supportsResponsesWebsocket"] is True
    disabled = await async_client.patch(
        f"/api/model-sources/{source_id}", json={"supportsResponses": False, "supportsResponsesWebsocket": False}
    )
    assert disabled.status_code == 200


@pytest.mark.parametrize("failure", ["lookup", "publication"])
async def test_source_websocket_fails_closed_before_delivery(async_client, app_instance, monkeypatch, failure):
    from app.modules.model_sources.ownership_repository import SourceOwnershipRepository
    from app.modules.model_sources.repository import ModelSourcesRepository

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

        async def fail(*args, **kwargs):
            raise RuntimeError("private database detail")

        if failure == "lookup":
            monkeypatch.setattr(ModelSourcesRepository, "list_responses_sources_for_model", fail)
        else:
            monkeypatch.setattr(SourceOwnershipRepository, "claim", fail)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            event = await ws.receive()
            assert event["type"] == "error"
            assert event["status"] == 502
            assert "private database" not in json.dumps(event)
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert [row.status for row in reservations] == ([] if failure == "lookup" else ["released"])


async def test_source_websocket_http_continuity_and_alias(async_client, app_instance):
    calls = []

    async def handler(request):
        if request.method == "POST":
            calls.append(await request.json())
            return web.json_response(
                {
                    "id": "resp_http",
                    "object": "response",
                    "status": "completed",
                    "model": "private-upstream",
                    "output": [],
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                }
            )
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        payload = await ws.receive_json()
        calls.append(payload)
        assert payload["previous_response_id"] == "resp_http"
        await complete(ws, response_id="resp_ws", model="private-upstream")
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(
            async_client,
            await start(handler, shutdown_timeout=1),
            metadata=json.dumps({"upstream_model": "private-upstream"}),
        )
        key = await create_key(async_client, source_id)
        headers = {"Authorization": f"Bearer {key['key']}"}
        payload = {"model": "source-ws-model", "input": "hi", "stream": False}
        response = await async_client.post("/v1/responses", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["model"] == "source-ws-model"
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", **payload, "previous_response_id": "resp_http"})
            events = [await ws.receive() for _ in range(3)]
            assert events[-1]["response"]["model"] == "source-ws-model"
        response = await async_client.post(
            "/v1/responses", headers=headers, json={**payload, "previous_response_id": "resp_ws"}
        )
        assert response.status_code == 200, response.text
        assert all(call["model"] == "private-upstream" for call in calls)
        assert calls[-1]["previous_response_id"] == "resp_ws"


@pytest.mark.parametrize("enabled", [False, True])
async def test_source_websocket_catalog(async_client, enabled):
    source_id = await create_source(async_client, "http://127.0.0.1:9/v1", enabled=enabled)
    key = await create_key(async_client, source_id)
    headers = {"Authorization": f"Bearer {key['key']}"}
    models = await async_client.get("/backend-api/codex/models", headers=headers)
    assert models.status_code == 200
    model = next(model for model in models.json()["models"] if model["slug"] == "source-ws-model")
    assert model["prefer_websockets"] is enabled


async def test_source_websocket_mixed_pool_catalog_is_conservative(async_client):
    from app.modules.model_sources.catalog import source_websocket_models
    from app.modules.model_sources.repository import ModelSourcesRepository

    source_id = await create_source(async_client, "http://127.0.0.1:9/v1")
    second = await _create_model_source(
        async_client,
        name="http-only",
        model="source-ws-model",
        base_url="http://127.0.0.1:9/v1",
        supports_responses=True,
        supports_streaming=False,
    )
    key = await create_key(async_client, source_id)
    updated = await async_client.patch(f"/api/api-keys/{key['id']}", json={"assignedSourceIds": [source_id, second]})
    assert updated.status_code == 200
    headers = {"Authorization": f"Bearer {key['key']}"}
    models = await async_client.get("/backend-api/codex/models", headers=headers)
    model = next(model for model in models.json()["models"] if model["slug"] == "source-ws-model")
    assert model["prefer_websockets"] is False
    async with SessionLocal() as session:
        assert source_websocket_models(await ModelSourcesRepository(session).list_enabled_sources()) == {
            "source-ws-model": False
        }


@pytest.mark.parametrize(
    "status,release_failure", [(401, False), (403, False), (429, False), (500, False), (302, False), (429, True)]
)
async def test_source_websocket_retry_only_handshake_failure(
    async_client, app_instance, monkeypatch, status, release_failure
):
    import app.modules.proxy.api as api
    from app.modules.proxy.source_pool import SourcePool

    sent, redirected = [], []

    async def fail(request):
        return web.Response(status=status, headers={"Location": "/redirected", "Retry-After": "1"}, text="token-native")

    async def success(request):
        redirected.append(request.path)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        sent.append(await ws.receive_json())
        await complete(ws)
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(fail, shutdown_timeout=1))
        second = await _create_model_source(
            async_client,
            name="z-good",
            model="source-ws-model",
            base_url=await start(success, shutdown_timeout=1),
            supports_responses=True,
        )
        await async_client.patch(f"/api/model-sources/{second}", json={"supportsResponsesWebsocket": True})
        key = await create_key(async_client, source_id)
        await async_client.patch(f"/api/api-keys/{key['id']}", json={"assignedSourceIds": [source_id, second]})
        if release_failure:
            release = api._release_reservation
            release_calls = 0

            async def fail_first_release(reservation):
                nonlocal release_calls
                release_calls += 1
                await release(reservation)
                if release_calls == 1:
                    raise RuntimeError("release acknowledgement lost")

            def forbid_cooldown(*args):
                raise AssertionError("Failed cleanup must stop before cooldown or retry")

            monkeypatch.setattr(api, "_release_reservation", fail_first_release)
            monkeypatch.setattr(SourcePool, "failed", forbid_cooldown)
        monkeypatch.setattr(
            SourcePool,
            "choose",
            lambda self, sources, *, excluded: next(
                (source for source in sorted(sources, key=lambda s: s.name) if source.id not in excluded), None
            ),
        )
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            event = await ws.receive()
            if status == 302 or release_failure:
                assert event["type"] == "error"
                if release_failure:
                    assert event["error"]["code"] == "usage_settlement_failed"
            else:
                assert event["type"] == "response.created", event
                await ws.receive()
                await ws.receive()
        async with SessionLocal() as session:
            reservations = (
                (await session.execute(select(ApiKeyUsageReservation).order_by(ApiKeyUsageReservation.created_at)))
                .scalars()
                .all()
            )
        assert [row.status for row in reservations] == (
            ["released"] if status == 302 or release_failure else ["released", "finalized"]
        )
        assert len(sent) == (0 if status == 302 or release_failure else 1)
        assert "/redirected" not in redirected


@pytest.mark.parametrize("content", [False, True])
async def test_source_websocket_disconnect_settles_once(async_client, app_instance, content):
    entered = asyncio.Event()

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        entered.set()
        if content:
            await ws.send_json({"type": "response.output_text.delta", "delta": "partial response"})
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            await asyncio.wait_for(entered.wait(), 3)
            if content:
                assert (await ws.receive())["type"] == "response.output_text.delta"
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            rows = (
                (await session.execute(select(RequestLog).where(RequestLog.model_source_id == source_id)))
                .scalars()
                .all()
            )
        assert len(rows) == len(reservations) == 1
        assert reservations[0].status == ("finalized" if content else "released")


async def test_source_websocket_no_retry_after_send(async_client, app_instance, monkeypatch):
    sent = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        sent.append(await ws.receive_json())
        await ws.close()
        return ws

    async with stub_source_upstreams() as start:
        url = await start(handler, shutdown_timeout=1)
        source_id = await create_source(async_client, url)
        second = await _create_model_source(
            async_client, name="z-other", model="source-ws-model", base_url=url, supports_responses=True
        )
        await async_client.patch(f"/api/model-sources/{second}", json={"supportsResponsesWebsocket": True})
        key = await create_key(async_client, source_id)
        await async_client.patch(f"/api/api-keys/{key['id']}", json={"assignedSourceIds": [source_id, second]})
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            assert (await ws.receive())["error"]["code"] == "model_source_stream_truncated"
        assert len(sent) == 1


async def test_source_websocket_queue_expiry_no_second_reservation(async_client, app_instance):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                calls.append(json.loads(message.data))
                entered.set()
                await release.wait()
                await complete(ws)
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
            await ws.send(payload)
            await asyncio.wait_for(entered.wait(), 5)
            await ws.send(payload)
            await async_client.patch(f"/api/model-sources/{source_id}", json={"timeoutSeconds": 1})
            await asyncio.sleep(1.05)
            release.set()
            for _ in range(3):
                await ws.receive()
            assert (await ws.receive())["error"]["code"] == "model_source_timeout"
        assert len(calls) == 1
        async with SessionLocal() as session:
            assert len((await session.execute(select(ApiKeyUsageReservation))).scalars().all()) == 1


@pytest.mark.parametrize("active", [False, True])
async def test_source_websocket_drain_closes_and_releases(async_client, app_instance, active):
    from app.core import shutdown
    from app.dependencies import get_proxy_service_for_app

    entered = asyncio.Event()

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        if not active:
            await complete(ws)
        entered.set()
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            await asyncio.wait_for(entered.wait(), 5)
            if not active:
                for _ in range(3):
                    await ws.receive()
            shutdown.begin_drain(0, deadline_monotonic=0)
            assert await ws.receive_close() == 1012
        # Zero-grace drain transfers settlement to service-owned cleanup.
        service = get_proxy_service_for_app(app_instance)
        await asyncio.wait_for(asyncio.gather(*service._background_cleanup_tasks), 5)
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert [reservation.status for reservation in reservations] == (["released"] if active else ["finalized"])


async def test_source_websocket_subscription_failure_does_not_deny_source(async_client, app_instance, monkeypatch):
    import app.modules.proxy._service.support as support

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
        monkeypatch.setattr(support, "upstream_websocket_transport_recently_failed", lambda: True)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            assert [(await ws.receive())["type"] for _ in range(3)] == [
                "response.created",
                "response.output_text.delta",
                "response.completed",
            ]


async def test_source_websocket_explicit_http_denial_and_catalog(async_client, app_instance):
    import app.modules.proxy.api as api
    from app.modules.api_keys.repository import ApiKeysRepository
    from app.modules.api_keys.service import ApiKeysService

    source_id = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, source_id)
    response = await async_client.put("/api/settings", json={"upstreamStreamTransport": "http"})
    assert response.status_code == 200, response.text
    async with SessionLocal() as session:
        api_key = await ApiKeysService(ApiKeysRepository(session)).get_key_by_id(key["id"])
    denial = await api._websocket_upstream_transport_denial(api_key=api_key)
    assert denial is not None
    assert denial.status_code == 426
    headers = {"Authorization": f"Bearer {key['key']}"}
    catalog = await async_client.get("/backend-api/codex/models", headers=headers)
    assert (
        next(model for model in catalog.json()["models"] if model["slug"] == "source-ws-model")["prefer_websockets"]
        is False
    )


async def test_source_websocket_tool_result_reuses_socket(async_client, app_instance):
    received = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        received.append(await ws.receive_json())
        call = {
            "id": "fc_source",
            "type": "function_call",
            "call_id": "call_source",
            "name": "lookup",
            "arguments": "{}",
            "status": "completed",
        }
        await ws.send_json({"type": "response.output_item.added", "output_index": 0, "item": call})
        await ws.send_json(
            {
                "type": "response.completed",
                "response": {"id": "resp_tool", "model": "source-ws-model", "status": "completed", "output": [call]},
            }
        )
        received.append(await ws.receive_json())
        assert received[1]["previous_response_id"] == "resp_tool"
        assert received[1]["input"] == [{"type": "function_call_output", "call_id": "call_source", "output": "42"}]
        await complete(ws, response_id="resp_tool_result")
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        await create_source(async_client, await start(handler, shutdown_timeout=1))
        async with websocket_client(app_instance, "/v1/responses") as ws:
            await ws.send(
                {
                    "type": "response.create",
                    "model": "source-ws-model",
                    "input": "run lookup",
                    "tools": [
                        {"type": "function", "name": "lookup", "parameters": {"type": "object", "properties": {}}}
                    ],
                }
            )
            assert (await ws.receive())["item"]["call_id"] == "call_source"
            await ws.receive()
            await ws.send(
                {
                    "type": "response.create",
                    "model": "source-ws-model",
                    "previous_response_id": "resp_tool",
                    "input": [{"type": "function_call_output", "call_id": "call_source", "output": "42"}],
                }
            )
            assert [(await ws.receive())["type"] for _ in range(3)] == [
                "response.created",
                "response.output_text.delta",
                "response.completed",
            ]
        assert len(received) == 2


async def test_source_websocket_override_file_denied_before_reservation(async_client, app_instance):
    metadata = {
        "source_request_overrides": {
            "input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file_owned_by_account"}]}]
        }
    }
    source_id = await create_source(async_client, "http://127.0.0.1:9/v1", metadata=json.dumps(metadata))
    key = await create_key(async_client, source_id)
    async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
        await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
        assert (await ws.receive())["error"]["code"] == "model_source_owner_unavailable"
    async with SessionLocal() as session:
        assert not (await session.execute(select(ApiKeyUsageReservation))).scalars().all()


async def test_source_websocket_settlement_failure_stops_reuse(async_client, app_instance, monkeypatch):
    import app.modules.proxy.api as api

    calls = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                calls.append(json.loads(message.data))
                await complete(ws)
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, source_id)

        async def failed_settlement(reservation, **kwargs):
            await api._release_reservation(reservation)
            return False

        monkeypatch.setattr(api, "_settle_source_reservation", failed_settlement)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
            await ws.send(payload)
            for _ in range(3):
                await ws.receive()
            await ws.send(payload)
            assert (await ws.receive())["error"]["code"] == "usage_settlement_failed"
        assert len(calls) == 1
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert len(reservations) == 1 and reservations[0].status == "released"


async def test_subscription_socket_rejects_source_switch_before_quota(async_client, app_instance, monkeypatch):
    from app.modules.proxy.service import ProxyService
    from tests.integration.test_proxy_websocket_responses import _SequencedUpstreamWebSocket, _websocket_response_batch
    from tests.unit.test_proxy_utils import _make_account

    account = _make_account("source-switch-account")
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()
    upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[
            _websocket_response_batch("resp_subscription_1"),
            _websocket_response_batch("resp_subscription_2"),
        ],
    )
    shared_lock = None
    errors = []

    async def connect(self, _headers, *, client_send_lock, **kwargs):
        nonlocal shared_lock
        shared_lock = client_send_lock
        return account, upstream

    async def observed_app(scope, receive, send):
        async def observed_send(message):
            if message["type"] == "websocket.send":
                payload = json.loads(message["text"])
                if payload.get("type") == "error":
                    # Even while the subscription reader owns this socket,
                    # routing errors use its serialized downstream writer.
                    assert shared_lock is not None and shared_lock.locked()
                    errors.append(payload)
            await send(message)

        await app_instance(scope, receive, observed_send)

    monkeypatch.setattr(ProxyService, "_connect_proxy_websocket", connect)
    source_id = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, source_id)
    response = await async_client.patch(f"/api/api-keys/{key['id']}", json={"assignedAccountIds": [account.id]})
    assert response.status_code == 200
    async with websocket_client(observed_app, "/v1/responses", key["key"]) as ws:
        subscription = {"type": "response.create", "model": "gpt-5.4", "input": "hi"}
        await ws.send(subscription)
        event = await ws.receive()
        assert event["type"] == "response.created", event
        assert (await ws.receive())["type"] == "response.completed"
        await ws.send({"type": "response.create", "model": "source-ws-model", "input": "switch"})
        assert (await ws.receive())["error"]["code"] == "websocket_reconnect_required"
        await ws.send(subscription)
        assert (await ws.receive())["type"] == "response.created"
        assert (await ws.receive())["type"] == "response.completed"
    assert len(errors) == 1 and len(upstream.sent_text) == 2
    async with SessionLocal() as session:
        reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        source_logs = (
            (await session.execute(select(RequestLog).where(RequestLog.model_source_id == source_id))).scalars().all()
        )
    assert len(reservations) == 2
    assert all(reservation.status != "reserved" for reservation in reservations)
    assert not source_logs


async def test_source_websocket_cancellation_during_quota_acquisition(async_client, app_instance, monkeypatch):
    import app.modules.proxy.api as api
    from app.modules.proxy.source_admission import get_source_bulkhead

    admitted, resume = asyncio.Event(), asyncio.Event()
    enforce_limits = api._enforce_request_limits

    async def delayed_admission(*args, **kwargs):
        reservation = await enforce_limits(*args, **kwargs)
        admitted.set()
        await resume.wait()
        return reservation

    monkeypatch.setattr(api, "_enforce_request_limits", delayed_admission)
    source_id = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, source_id)
    with pytest.raises(asyncio.CancelledError):
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            await asyncio.wait_for(admitted.wait(), 5)
            ws.cancel_transport()
            resume.set()
    async with SessionLocal() as session:
        reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
    assert len(reservations) == 1 and reservations[0].status == "released"
    assert get_source_bulkhead().in_flight(source_id) == 0


async def test_source_websocket_first_frame_deadline_restarts_per_turn(async_client, app_instance, monkeypatch):
    import app.modules.proxy.source_websocket as source_websocket

    received = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        received.append(await ws.receive_json())
        await complete(ws)
        received.append(await ws.receive_json())
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
            await ws.send(payload)
            for _ in range(3):
                await ws.receive()
            monkeypatch.setattr(source_websocket, "SOURCE_FIRST_FRAME_DEADLINE_SECONDS", 0.05)
            await ws.send(payload)
            assert (await ws.receive())["error"]["code"] == "model_source_timeout"
        assert len(received) == 2
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert sorted(reservation.status for reservation in reservations) == ["finalized", "released"]


@pytest.mark.parametrize("slow_consumer", [False, True])
async def test_source_websocket_idle_and_slow_consumer_cleanup(async_client, app_instance, monkeypatch, slow_consumer):
    import app.modules.proxy.api as api
    from app.modules.proxy.source_admission import get_source_bulkhead

    blocked_send_cancelled = asyncio.Event()
    if slow_consumer:
        monkeypatch.setattr(api, "source_stream_idle_seconds", lambda: 0.1)
    else:
        monkeypatch.setattr(api.get_settings(), "proxy_downstream_websocket_idle_timeout_seconds", 0.1)

    async def observed_app(scope, receive, send):
        async def bounded_send(message):
            if slow_consumer and message["type"] == "websocket.send" and "delta" in json.loads(message["text"]):
                try:
                    await asyncio.Event().wait()
                finally:
                    blocked_send_cancelled.set()
            await send(message)

        await app_instance(scope, receive, bounded_send)

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
        async with websocket_client(observed_app, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            assert (await ws.receive())["type"] == "response.created"
            if slow_consumer:
                assert (await ws.receive())["error"]["code"] == "model_source_timeout"
                assert blocked_send_cancelled.is_set()
            else:
                await ws.receive()
                await ws.receive()
            assert await ws.receive_close() == 1000
        assert get_source_bulkhead().in_flight(source_id) == 0
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert len(reservations) == 1
        assert reservations[0].status == ("released" if slow_consumer else "finalized")


async def test_source_websocket_concurrent_session_rejected_before_quota(async_client, app_instance):
    entered, release = asyncio.Event(), asyncio.Event()
    connections = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        connections.append(await ws.receive_json())
        entered.set()
        await release.wait()
        await complete(ws)
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(handler, shutdown_timeout=1))
        response = await async_client.patch(f"/api/model-sources/{source_id}", json={"maxConcurrency": 1})
        assert response.status_code == 200
        key = await create_key(async_client, source_id)
        payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as first:
            await first.send(payload)
            await asyncio.wait_for(entered.wait(), 5)
            async with websocket_client(app_instance, "/v1/responses", key["key"]) as second:
                await second.send(payload)
                assert (await second.receive())["error"]["code"] == "model_source_busy"
            release.set()
            for _ in range(3):
                await first.receive()
        assert len(connections) == 1
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert len(reservations) == 1 and reservations[0].status == "finalized"
