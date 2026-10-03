"""Preserve public domain errors before and after native source binding."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from sqlalchemy import select

from app.db.models import ApiKeyLimit, ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.modules.proxy import api
from app.modules.proxy.source_admission import get_source_bulkhead
from tests.integration.model_source_helpers import _enable_api_key_auth, stub_source_upstreams
from tests.integration.test_model_source_websocket import complete, create_key, create_source, websocket_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROUTES = ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses", "/backend-api/codex/responses/"]


async def _reservation_statuses(key_id):
    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == key_id)))
            .scalars()
            .all()
        )
    return [row.status for row in rows]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("backend", ["subscription", "source_initial", "source_reused"])
@pytest.mark.parametrize("kind", ["model", "effort", "revoked"])
async def test_domain_errors_match_http_and_preserve_parameters(async_client, app_instance, path, backend, kind):
    calls = []

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for _ in ws:
            calls.append(request.path)
            await complete(ws)
        return ws

    async with stub_source_upstreams() as start:
        source_id = None
        if backend == "subscription":
            await _enable_api_key_auth(async_client)
            response = await async_client.post("/api/api-keys/", json={"name": "error-contract"})
            assert response.status_code == 200, response.text
            key = response.json()
            model = "gpt-5.4"
        else:
            source_id = await create_source(async_client, await start(provider, shutdown_timeout=1))
            key = await create_key(async_client, source_id)
            model = "source-ws-model"
        response = await async_client.patch(
            f"/api/api-keys/{key['id']}", json={"allowedModels": [model], "allowedReasoningEfforts": ["low"]}
        )
        assert response.status_code == 200, response.text
        payload = {"model": model, "input": "hello"}
        async with websocket_client(app_instance, path, key["key"]) as ws:
            if backend == "source_reused":
                await ws.send({"type": "response.create", **payload})
                assert [(await ws.receive())["type"] for _ in range(3)] == [
                    "response.created",
                    "response.output_text.delta",
                    "response.completed",
                ]
            if kind == "revoked":
                response = await async_client.patch(f"/api/api-keys/{key['id']}", json={"isActive": False})
                assert response.status_code == 200, response.text
            elif kind == "model":
                payload["model"] = "gpt-5.3-codex"
            else:
                payload["reasoning"] = {"effort": "high"}
            http = await async_client.post(
                path.rstrip("/"), headers={"Authorization": f"Bearer {key['key']}"}, json=payload
            )
            assert http.status_code == (401 if kind == "revoked" else 403), http.text
            await ws.send({"type": "response.create", **payload})
            event = await ws.receive()
            assert event == {"type": "error", "status": http.status_code, **http.json()}
            assert event["error"]["type"] == ("authentication_error" if kind == "revoked" else "permission_error")
            if kind == "effort":
                assert event["error"]["param"] == "reasoning.effort"
        assert len(calls) == (1 if backend == "source_reused" else 0)
        assert await _reservation_statuses(key["id"]) == (["finalized"] if backend == "source_reused" else [])
        if source_id is not None:
            assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("reused", [False, True])
async def test_source_quota_errors_match_http_without_new_reservations(
    async_client, app_instance, monkeypatch, path, reused
):
    calls = []
    settled = asyncio.Event()
    settle = api._settle_source_reservation

    async def observed_settlement(*args, **kwargs):
        result = await settle(*args, **kwargs)
        settled.set()
        return result

    monkeypatch.setattr(api, "_settle_source_reservation", observed_settlement)

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for _ in ws:
            calls.append(request.path)
            await complete(ws)
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        payload = {"model": "source-ws-model", "input": "hello"}
        async with websocket_client(app_instance, path, key["key"]) as ws:
            if reused:
                await ws.send({"type": "response.create", **payload})
                assert [(await ws.receive())["type"] for _ in range(3)] == [
                    "response.created",
                    "response.output_text.delta",
                    "response.completed",
                ]
                await asyncio.wait_for(settled.wait(), 5)
            async with SessionLocal() as session:
                limit = (
                    await session.execute(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key["id"]))
                ).scalar_one()
                limit.current_value = limit.max_value
                await session.commit()
            http = await async_client.post(
                path.rstrip("/"), headers={"Authorization": f"Bearer {key['key']}"}, json=payload
            )
            assert http.status_code == 429, http.text
            await ws.send({"type": "response.create", **payload})
            event = await ws.receive()
            assert event == {"type": "error", "status": 429, **http.json()}
            assert event["error"]["type"] == "rate_limit_error"
        assert len(calls) == int(reused)
        assert await _reservation_statuses(key["id"]) == (["finalized"] if reused else [])
        assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("websocket_enabled", [False, True])
@pytest.mark.parametrize("reference", ["response", "item"])
async def test_unavailable_source_owner_preserves_http_error_envelope(
    async_client, app_instance, path, websocket_enabled, reference
):
    calls = []

    async def provider(request):
        calls.append(await request.json())
        return web.json_response(
            {
                "id": "resp_owned_http",
                "object": "response",
                "status": "completed",
                "model": "source-ws-model",
                "output": [
                    {
                        "id": "msg_owned_http",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "hello", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
            }
        )

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(provider), enabled=websocket_enabled)
        key = await create_key(async_client, source_id)
        headers = {"Authorization": f"Bearer {key['key']}"}
        payload = {"model": "source-ws-model", "input": "hello", "stream": False}
        first = await async_client.post(path, headers=headers, json=payload)
        assert first.status_code == 200, first.text
        disabled = await async_client.patch(f"/api/model-sources/{source_id}", json={"isEnabled": False})
        assert disabled.status_code == 200, disabled.text
        continuation = (
            {**payload, "previous_response_id": first.json()["id"]}
            if reference == "response"
            else {**payload, "input": [{"type": "item_reference", "id": first.json()["output"][0]["id"]}]}
        )
        expected = {
            "error": {
                "type": "server_error",
                "code": "previous_response_owner_unavailable"
                if reference == "response"
                else "model_source_owner_unavailable",
                "message": "The request's upstream state has no unambiguous available source. "
                "Use its original source or resend portable full context.",
            }
        }
        http = await async_client.post(path, headers=headers, json=continuation)
        assert http.status_code == 409, http.text
        assert http.json() == expected
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send({"type": "response.create", **continuation})
            assert await ws.receive() == {"type": "error", "status": 409, **expected}
        assert len(calls) == 1
        assert await _reservation_statuses(key["id"]) == ["finalized"]
        assert get_source_bulkhead().in_flight(source_id) == 0
