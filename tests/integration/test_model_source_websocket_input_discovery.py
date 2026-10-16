"""Route coverage for native input bounds and effective discovery policy."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import WSMsgType, web
from sqlalchemy import select

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.db.models import ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.modules.model_sources.websocket import MAX_MESSAGE_BYTES
from app.modules.proxy.service import ProxyService
from app.modules.proxy.source_admission import get_source_bulkhead
from tests.integration.model_source_helpers import _create_model_source, stub_source_upstreams
from tests.integration.test_model_source_websocket import complete, create_key, create_source, websocket_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROUTES = ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses", "/backend-api/codex/responses/"]


async def _reservations():
    async with SessionLocal() as session:
        return (await session.execute(select(ApiKeyUsageReservation))).scalars().all()


async def _catalog(client, headers):
    response = await client.get("/backend-api/codex/models", headers=headers)
    # The upstream public catalog reserves and releases a request, including
    # the key's enforced model when present. Discovery must leave none live.
    assert all(row.status == "released" for row in await _reservations())
    return response


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("encoding", ["whitespace", "utf8", "escaped"])
async def test_native_first_and_reused_creates_share_wire_byte_limit(async_client, app_instance, path, encoding):
    calls = []

    async def provider(request):
        ws = web.WebSocketResponse(max_msg_size=64 * 1024 * 1024)
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                calls.append(json.loads(message.data))
                await complete(ws, response_id=f"resp_bound_{len(calls)}")
        return ws

    payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
    text = json.dumps(payload)
    boundary = text + " " * (MAX_MESSAGE_BYTES - len(text.encode()))
    if encoding == "whitespace":
        oversized = boundary + " "
    elif encoding == "utf8":
        oversized = json.dumps({**payload, "extra_padding": "é" * (MAX_MESSAGE_BYTES // 2)}, ensure_ascii=False)
        assert len(oversized) < MAX_MESSAGE_BYTES
    else:
        oversized = text[:-1] + ',"extra_padding":"' + "\\u0061" * (MAX_MESSAGE_BYTES // 6 + 1) + '"}'
        assert len(json.loads(oversized)["extra_padding"]) < MAX_MESSAGE_BYTES
    assert len(oversized.encode()) > MAX_MESSAGE_BYTES

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send_text(oversized)
            error = await ws.receive()
            assert error["type"] == "error" and error["error"]["code"] == "invalid_request_error"
            assert not calls and not await _reservations()
            assert get_source_bulkhead().in_flight(sid) == 0
            # The exact byte boundary is accepted after rejection and on reuse.
            for _ in range(2):
                await ws.send_text(boundary)
                assert [(await ws.receive())["type"] for _ in range(3)] == [
                    "response.created",
                    "response.output_text.delta",
                    "response.completed",
                ]
                await ws.send_text(oversized)
                error = await ws.receive()
                assert error["type"] == "error" and error["error"]["code"] == "invalid_request_error"
        assert len(calls) == 2
        assert [reservation.status for reservation in await _reservations()] == ["finalized", "finalized"]
        assert get_source_bulkhead().in_flight(sid) == 0


@pytest.mark.parametrize("path", ROUTES)
async def test_source_input_limit_preserves_subscription_ingress(async_client, app_instance, monkeypatch, path):
    subscription_inputs = []

    async def subscription(self, payload, **kwargs):
        subscription_inputs.append(payload["input"])
        raise ProxyResponseError(409, openai_error("subscription_probe", "Local subscription stub"))

    monkeypatch.setattr(ProxyService, "_prepare_websocket_response_create_request", subscription)
    sid = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, sid)
    async with websocket_client(app_instance, path, key["key"]) as ws:
        text = json.dumps({"type": "response.create", "model": "gpt-5.4", "input": "subscription"})
        await ws.send_text(text + " " * MAX_MESSAGE_BYTES)
        assert (await ws.receive())["error"]["code"] == "subscription_probe"
    assert subscription_inputs == ["subscription"]
    assert not await _reservations()


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_oversized_first_create_preserves_buffered_correction_size(async_client, app_instance, monkeypatch, path):
    entered, release, buffered = (asyncio.Event() for _ in range(3))
    refresh = ProxyService._refresh_websocket_api_key_policy
    calls = []

    async def paused_refresh(self, *args, **kwargs):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await refresh(self, *args, **kwargs)

    async def transport(scope, receive, send):
        count = 0

        async def observed_receive():
            nonlocal count
            message = await receive()
            if message["type"] == "websocket.receive":
                count += 1
                if count == 2:
                    buffered.set()
            return message

        await app_instance(scope, observed_receive, send)

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        calls.append(await ws.receive_json())
        await complete(ws)
        async for _ in ws:
            pass
        return ws

    monkeypatch.setattr(ProxyService, "_refresh_websocket_api_key_policy", paused_refresh)
    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, sid)
        payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
        try:
            async with websocket_client(transport, path, key["key"]) as ws:
                await ws.send_text(json.dumps(payload) + " " * MAX_MESSAGE_BYTES)
                await asyncio.wait_for(entered.wait(), 5)
                await ws.send(payload)
                await asyncio.wait_for(buffered.wait(), 5)
                release.set()
                assert (await ws.receive())["error"]["code"] == "invalid_request_error"
                assert [(await ws.receive())["type"] for _ in range(3)] == [
                    "response.created",
                    "response.output_text.delta",
                    "response.completed",
                ]
        finally:
            release.set()
        assert len(calls) == 1
        assert [reservation.status for reservation in await _reservations()] == ["finalized"]
        assert get_source_bulkhead().in_flight(sid) == 0


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize(
    "alias,alias_capable,canonical_capable,prohibit,expected,selected",
    [
        ("gpt-5.4-fast", True, False, False, True, "gpt-5.4-fast"),
        ("gpt-5.4-fast", True, False, True, False, "gpt-5.4"),
        ("gpt-5.4-fast", False, True, True, True, "gpt-5.4"),
        ("gpt-5.4-high", False, True, False, False, "gpt-5.4-high"),
        ("gpt-5.4-high", True, False, False, True, "gpt-5.4-high"),
        ("gpt-5.4-high", None, True, False, True, "gpt-5.4"),
    ],
)
async def test_discovery_matches_enforced_alias_routing(
    async_client, app_instance, path, alias, alias_capable, canonical_capable, prohibit, expected, selected
):
    ws_calls, http_calls = [], []

    async def provider(request):
        if request.method == "POST":
            payload = await request.json()
            http_calls.append(payload["model"])
            return web.json_response(
                {
                    "id": "resp_http_discovery",
                    "object": "response",
                    "status": "completed",
                    "model": payload["model"],
                    "output": [],
                    "usage": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
                }
            )
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        payload = await ws.receive_json()
        ws_calls.append(payload["model"])
        await complete(ws, model=payload["model"])
        async for _ in ws:
            pass
        return ws

    async with stub_source_upstreams() as start:
        source_ids = []
        for model, capable in [("gpt-5.4", canonical_capable), (alias, alias_capable)]:
            if capable is None:
                continue
            sid = await _create_model_source(
                async_client,
                name=model,
                model=model,
                base_url=await start(provider, shutdown_timeout=1),
                supports_responses=True,
            )
            response = await async_client.patch(
                f"/api/model-sources/{sid}", json={"supportsResponsesWebsocket": capable}
            )
            assert response.status_code == 200
            source_ids.append(sid)
        key = await create_key(async_client, source_ids[0])
        response = await async_client.patch(
            f"/api/api-keys/{key['id']}", json={"assignedSourceIds": source_ids, "enforcedModel": alias}
        )
        assert response.status_code == 200
        response = await async_client.put("/api/settings", json={"prohibitFastMode": prohibit})
        assert response.status_code == 200
        headers = {"Authorization": f"Bearer {key['key']}"}
        if alias_capable is not None:
            catalog = await _catalog(async_client, headers)
            assert catalog.status_code == 200
            entry = next(model for model in catalog.json()["models"] if model["slug"] == alias)
            assert entry["prefer_websockets"] is expected
        assert all(row.status == "released" for row in await _reservations())
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            event = await ws.receive()
            if expected:
                assert event["type"] == "response.created", event
                assert (await ws.receive())["type"] == "response.output_text.delta"
                assert (await ws.receive())["type"] == "response.completed"
            else:
                assert event["error"]["code"] == "model_source_requires_http_transport", event
        assert ws_calls == ([selected] if expected else [])
        if not expected:
            assert all(row.status == "released" for row in await _reservations())
            response = await async_client.post(
                path, headers=headers, json={"model": alias, "input": "hi", "stream": False}
            )
            assert response.status_code == 200
            assert http_calls == [selected]


@pytest.mark.parametrize(
    "scope,expected",
    [("capable", True), ("mixed", False), ("disabled", True), ("unassigned", False), ("allowlist", False)],
)
async def test_discovery_effective_pool_is_scoped_and_conservative(async_client, scope, expected):
    sources = []
    for name, model, capable, streaming in [
        ("alias", "gpt-5.4-fast", True, True),
        ("canonical", "gpt-5.4", True, True),
        ("http-peer", "gpt-5.4", False, False),
    ]:
        sid = await _create_model_source(
            async_client,
            name=name,
            model=model,
            base_url="http://127.0.0.1:9/v1",
            supports_responses=True,
            supports_streaming=streaming,
        )
        assert (
            await async_client.patch(f"/api/model-sources/{sid}", json={"supportsResponsesWebsocket": capable})
        ).status_code == 200
        sources.append(sid)
    assigned = sources if scope in {"mixed", "disabled"} else sources[:1] if scope == "unassigned" else sources[:2]
    key = await create_key(async_client, sources[0])
    update = {"assignedSourceIds": assigned, "enforcedModel": "gpt-5.4-fast"}
    if scope == "allowlist":
        update["allowedModels"] = ["gpt-5.4-fast"]
    assert (await async_client.patch(f"/api/api-keys/{key['id']}", json=update)).status_code == 200
    if scope == "disabled":
        assert (
            await async_client.patch(f"/api/model-sources/{sources[2]}", json={"isEnabled": False})
        ).status_code == 200
    assert (await async_client.put("/api/settings", json={"prohibitFastMode": True})).status_code == 200
    headers = {"Authorization": f"Bearer {key['key']}"}
    catalog = await _catalog(async_client, headers)
    assert catalog.status_code == 200
    alias = next(model for model in catalog.json()["models"] if model["slug"] == "gpt-5.4-fast")
    assert alias["prefer_websockets"] is expected
    assert all(row.status == "released" for row in await _reservations())


@pytest.mark.parametrize(
    "extra",
    ["none", "chat_only", "disabled_source", "disabled_model", "unassigned", "unknown", "http_only", "nonstreaming"],
)
async def test_catalog_and_route_ignore_unrelated_models(async_client, app_instance, extra):
    calls = []

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for _ in ws:
            calls.append(request.path)
            await complete(ws)
        return ws

    async with stub_source_upstreams() as start:
        sid = await create_source(async_client, await start(provider, shutdown_timeout=1))
        assigned, allowed = [sid], ["source-ws-model"]
        if extra != "none":
            allowed.append("other-model")
        if extra not in {"none", "unknown"}:
            other = await _create_model_source(
                async_client,
                name="other",
                model="other-model",
                base_url="http://127.0.0.1:9/v1",
                supports_responses=extra != "chat_only",
                supports_streaming=extra != "nonstreaming",
            )
            if extra != "unassigned":
                assigned.append(other)
            if extra == "disabled_source":
                response = await async_client.patch(f"/api/model-sources/{other}", json={"isEnabled": False})
                assert response.status_code == 200, response.text
            elif extra == "disabled_model":
                response = await async_client.patch(
                    f"/api/model-sources/{other}", json={"models": [{"model": "other-model", "isEnabled": False}]}
                )
                assert response.status_code == 200, response.text
            elif extra == "nonstreaming":
                response = await async_client.patch(
                    f"/api/model-sources/{other}", json={"supportsResponsesWebsocket": True}
                )
                assert response.status_code == 200, response.text
        key = await create_key(async_client, sid)
        response = await async_client.patch(
            f"/api/api-keys/{key['id']}", json={"assignedSourceIds": assigned, "allowedModels": allowed}
        )
        assert response.status_code == 200, response.text
        headers = {"Authorization": f"Bearer {key['key']}"}
        catalog = await _catalog(async_client, headers)
        assert catalog.status_code == 200
        entry = next(model for model in catalog.json()["models"] if model["slug"] == "source-ws-model")
        assert entry["prefer_websockets"] is True
        assert all(row.status == "released" for row in await _reservations())
        async with websocket_client(app_instance, "/v1/responses", key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            assert [(await ws.receive())["type"] for _ in range(3)] == [
                "response.created",
                "response.output_text.delta",
                "response.completed",
            ]
        assert len(calls) == 1
        assert sorted(row.status for row in await _reservations()) == ["finalized", "released"]
        assert get_source_bulkhead().in_flight(sid) == 0
