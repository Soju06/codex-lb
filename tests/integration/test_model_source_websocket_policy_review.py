"""Route regressions for subscription ownership and source admission races."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from sqlalchemy import select

from app.core.clients.proxy import ProxyResponseError
from app.core.clock import RealClock
from app.core.errors import openai_error
from app.core.utils.time import utcnow
from app.db.models import Account, ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.capability_lineage import CapabilityLineageAlias
from app.modules.proxy.capability_routing import CapabilityRouter, RoutingCapability, RoutingIntent
from app.modules.proxy.service import ProxyService
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.proxy.source_pool import SourcePool
from tests.integration.model_source_helpers import _create_model_source, stub_source_upstreams
from tests.integration.test_model_source_websocket import complete, create_key, create_source, websocket_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ROUTES = ["/v1/responses", "/backend-api/codex/responses"]
TURN_STATE = "http_turn_0123456789abcdef0123456789abcdef"


async def register_subscription_owner(client, app, key, *, turn_state=TURN_STATE, previous_response_id=None):
    account_id = f"subscription-{key['id']}"
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                email="turn-owner@example.test",
                plan_type="plus",
                access_token_encrypted=b"unused-local-fixture",
                refresh_token_encrypted=b"unused-local-fixture",
                id_token_encrypted=b"unused-local-fixture",
                last_refresh=utcnow(),
            )
        )
        await session.commit()
    response = await client.patch(f"/api/api-keys/{key['id']}", json={"assignedAccountIds": [account_id]})
    assert response.status_code == 200, response.text
    coordinator = get_proxy_service_for_app(app)._durable_bridge
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value=f"subscription-session-{key['id']}",
        api_key_id=key["id"],
        instance_id="local-policy-fixture",
        owner_process_epoch="local-policy-fixture",
        lease_ttl_seconds=120.0,
        account_id=account_id,
        model="source-ws-model",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=claimed.session_id,
        api_key_id=key["id"],
        instance_id="local-policy-fixture",
        owner_epoch=claimed.owner_epoch,
        turn_state=turn_state,
        lease_ttl_seconds=120.0,
    )
    if previous_response_id is not None:
        async with SessionLocal() as session:
            session.add(
                RequestLog(
                    request_id=previous_response_id,
                    account_id=account_id,
                    api_key_id=key["id"],
                    model="source-ws-model",
                    status="success",
                )
            )
            await session.commit()
    return account_id


async def key_reservations(key_id):
    async with SessionLocal() as session:
        return (
            (await session.execute(select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == key_id)))
            .scalars()
            .all()
        )


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("ownership", ["same_key", "other_key", "unregistered"])
async def test_source_respects_exact_key_subscription_turn_owner(
    async_client, app_instance, monkeypatch, path, ownership
):
    calls, subscription_calls = [], []

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        calls.append(await ws.receive_json())
        await complete(ws)
        async for _ in ws:
            pass
        return ws

    async def subscription_prepare(self, payload, **kwargs):
        subscription_calls.append(payload)
        raise ProxyResponseError(409, openai_error("subscription_probe", "Local subscription fixture"))

    monkeypatch.setattr(ProxyService, "_prepare_websocket_response_create_request", subscription_prepare)
    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        if ownership != "unregistered":
            owner_key = key if ownership == "same_key" else await create_key(async_client, source_id)
            await register_subscription_owner(async_client, app_instance, owner_key)
        async with websocket_client(
            app_instance, path, key["key"], extra_headers=[(b"x-codex-turn-state", TURN_STATE.encode())]
        ) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "continue"})
            event = await ws.receive()
            if ownership == "same_key":
                assert event["error"]["code"] == "subscription_probe"
            else:
                assert event["type"] == "response.created"
                await ws.receive()
                assert (await ws.receive())["type"] == "response.completed"
        assert len(calls) == (0 if ownership == "same_key" else 1)
        assert len(subscription_calls) == (1 if ownership == "same_key" else 0)
        assert [row.status for row in await key_reservations(key["id"])] == (
            [] if ownership == "same_key" else ["finalized"]
        )


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("failure", ["missing", "lookup", "conflict"])
async def test_source_turn_owner_failure_is_closed(async_client, app_instance, monkeypatch, path, failure):
    source_id = await create_source(async_client, "http://127.0.0.1:9/v1")
    key = await create_key(async_client, source_id)
    service = get_proxy_service_for_app(app_instance)
    turn_state = "opaque-provider-turn" if failure == "missing" else TURN_STATE
    if failure == "lookup":

        async def unavailable(**kwargs):
            raise RuntimeError("injected local database outage")

        monkeypatch.setattr(service._durable_bridge, "lookup_turn_state_target", unavailable)
    elif failure == "conflict":
        await register_subscription_owner(async_client, app_instance, key)
        service._http_bridge_turn_state_index[(turn_state, key["id"])] = "conflicting-live-owner"
        service._http_bridge_sessions["conflicting-live-owner"] = SimpleNamespace(
            account=SimpleNamespace(id="another-subscription-account")
        )
    try:
        async with websocket_client(
            app_instance, path, key["key"], extra_headers=[(b"x-codex-turn-state", turn_state.encode())]
        ) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "continue"})
            event = await ws.receive()
            assert event["error"]["code"] == (
                "continuity_owner_conflict" if failure == "conflict" else "turn_state_owner_unavailable"
            )
        assert not await key_reservations(key["id"])
        assert get_source_bulkhead().in_flight(source_id) == 0
    finally:
        if failure == "conflict":
            service._http_bridge_turn_state_index.pop((turn_state, key["id"]), None)
            service._http_bridge_sessions.pop("conflicting-live-owner", None)


@pytest.mark.parametrize("path", ROUTES)
async def test_source_reused_socket_rechecks_subscription_turn_owner(async_client, app_instance, path):
    calls = []

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            calls.append(json.loads(message.data))
            await complete(ws)
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(
            app_instance, path, key["key"], extra_headers=[(b"x-codex-turn-state", TURN_STATE.encode())]
        ) as ws:
            payload = {"type": "response.create", "model": "source-ws-model", "input": "hello"}
            await ws.send(payload)
            for _ in range(3):
                await ws.receive()
            await register_subscription_owner(async_client, app_instance, key)
            await ws.send({**payload, "previous_response_id": "resp_native"})
            assert (await ws.receive())["error"]["code"] == "websocket_reconnect_required"
        assert len(calls) == 1
        assert [row.status for row in await key_reservations(key["id"])] == ["finalized"]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("carrier", ["original", "effective"])
async def test_source_preserves_subscription_previous_owner_before_admission(
    async_client, app_instance, monkeypatch, path, carrier
):
    calls, subscription_calls = [], []

    async def provider(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        calls.append(await ws.receive_json())
        await complete(ws)
        async for _ in ws:
            pass
        return ws

    async def subscription_prepare(self, payload, **kwargs):
        subscription_calls.append(payload)
        raise ProxyResponseError(409, openai_error("subscription_probe", "Local subscription fixture"))

    monkeypatch.setattr(ProxyService, "_prepare_websocket_response_create_request", subscription_prepare)
    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        payload = {"type": "response.create", "model": "source-ws-model", "input": "hello"}
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send(payload)
            for _ in range(3):
                await ws.receive()
        await register_subscription_owner(async_client, app_instance, key, previous_response_id="resp_native")
        if carrier == "original":
            payload["previous_response_id"] = "resp_native"
        else:
            response = await async_client.patch(
                f"/api/model-sources/{source_id}",
                json={
                    "models": [
                        {
                            "model": "source-ws-model",
                            "supportsStreaming": True,
                            "rawMetadataJson": json.dumps(
                                {"source_request_overrides": {"previous_response_id": "resp_native"}}
                            ),
                        }
                    ]
                },
            )
            assert response.status_code == 200, response.text
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send(payload)
            assert (await ws.receive())["error"]["code"] == (
                "subscription_probe" if carrier == "original" else "websocket_reconnect_required"
            )
        assert len(calls) == 1
        assert len(subscription_calls) == (1 if carrier == "original" else 0)
        assert [row.status for row in await key_reservations(key["id"])] == ["finalized"]
        assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("mode", ["portable", "pinned", "replacement_background", "replacement_capability", "deadline"])
async def test_source_reselects_after_admission_race_without_bypassing_policy(
    async_client, app_instance, monkeypatch, path, mode
):
    selected = []
    blocked, resume, finish = (asyncio.Event() for _ in range(3))
    calls = []

    class AdvanceableClock(RealClock):
        offset = 0.0

        def monotonic(self):
            return super().monotonic() + self.offset

    clock = AdvanceableClock()

    def provider_for(label):
        async def provider(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            payload = await ws.receive_json()
            calls.append((label, payload["instructions"]))
            if payload["instructions"] == "establish ownership":
                await complete(ws, response_id="resp_pinned")
            else:
                response = {"id": f"resp_{label}_{len(calls)}", "model": "source-ws-model", "output": []}
                await ws.send_json({"type": "response.created", "response": {**response, "status": "in_progress"}})
                await finish.wait()
                await ws.send_json({"type": "response.completed", "response": {**response, "status": "completed"}})
            async for _ in ws:
                pass
            return ws

        return provider

    metadata = None
    if mode == "replacement_background":
        metadata = json.dumps({"source_request_overrides": {"background": True}})
    elif mode == "replacement_capability":
        metadata = json.dumps(
            {"source_request_overrides": {"client_metadata": {"x-codex-parent-thread-id": "restricted-replacement"}}}
        )
    async with stub_source_upstreams() as start:
        first = await create_source(async_client, await start(provider_for("first"), shutdown_timeout=1))
        second = await _create_model_source(
            async_client,
            name="equivalent-native",
            model="source-ws-model",
            base_url=await start(provider_for("second"), shutdown_timeout=1),
            supports_responses=True,
            raw_metadata_json=metadata,
        )
        for source_id in (first, second):
            response = await async_client.patch(
                f"/api/model-sources/{source_id}", json={"supportsResponsesWebsocket": True, "maxConcurrency": 1}
            )
            assert response.status_code == 200, response.text
        key = await create_key(async_client, first)
        if mode == "pinned":
            async with websocket_client(app_instance, path, key["key"]) as ws:
                await ws.send(
                    {
                        "type": "response.create",
                        "model": "source-ws-model",
                        "input": "hello",
                        "instructions": "establish ownership",
                    }
                )
                for _ in range(3):
                    await ws.receive()
        if mode == "deadline":
            response = await async_client.patch(f"/api/model-sources/{first}", json={"timeoutSeconds": 60})
            assert response.status_code == 200, response.text
            monkeypatch.setattr(get_proxy_service_for_app(app_instance), "_clock", clock)
        response = await async_client.patch(f"/api/api-keys/{key['id']}", json={"assignedSourceIds": [first, second]})
        assert response.status_code == 200, response.text
        competitor_key = await create_key(async_client, first)
        if mode == "replacement_capability":
            await get_proxy_service_for_app(app_instance)._capability_router.route(
                RoutingIntent.requiring(RoutingCapability.TRUSTED_CYBER),
                api_key_id=key["id"],
                aliases=(CapabilityLineageAlias(kind="codex_task", value="restricted-replacement"),),
            )
        choose, route = SourcePool.choose, CapabilityRouter.route

        def observed_choose(self, sources, *, excluded):
            preferred = [source for source in sources if source.id == first]
            result = choose(self, preferred if first not in excluded else sources, excluded=excluded)
            if result is not None and not selected:
                selected.append(result.id)
            return result

        async def delayed_route(self, intent, **kwargs):
            result = await route(self, intent, **kwargs)
            if kwargs.get("api_key_id") == key["id"] and selected and not blocked.is_set():
                blocked.set()
                await resume.wait()
            return result

        monkeypatch.setattr(SourcePool, "choose", observed_choose)
        monkeypatch.setattr(CapabilityRouter, "route", delayed_route)
        try:
            async with websocket_client(app_instance, path, key["key"]) as ws:
                payload = {
                    "type": "response.create",
                    "model": "source-ws-model",
                    "input": "hello",
                    "instructions": "racing turn",
                }
                if mode == "pinned":
                    payload["previous_response_id"] = "resp_pinned"
                await ws.send(payload)
                await asyncio.wait_for(blocked.wait(), 5)
                async with websocket_client(app_instance, path, competitor_key["key"]) as competing_ws:
                    await competing_ws.send(
                        {
                            "type": "response.create",
                            "model": "source-ws-model",
                            "input": "hello",
                            "instructions": "competitor",
                        }
                    )
                    assert (await competing_ws.receive())["type"] == "response.created"
                    assert get_source_bulkhead().in_flight(first) == 1
                    assert get_source_bulkhead().in_flight(second) == 0
                    if mode == "deadline":
                        finish.set()
                        assert (await competing_ws.receive())["type"] == "response.completed"
                        clock.offset = 61
                    resume.set()
                    event = await ws.receive()
                    finish.set()
                    if mode == "portable":
                        assert event["type"] == "response.created"
                        assert (await ws.receive())["type"] == "response.completed"
                    else:
                        assert (
                            event["error"]["code"]
                            == {
                                "pinned": "model_source_busy",
                                "replacement_background": "unsupported_operation",
                                "replacement_capability": "websocket_reconnect_required",
                                "deadline": "model_source_timeout",
                            }[mode]
                        )
                    if mode != "deadline":
                        assert (await competing_ws.receive())["type"] == "response.completed"
        finally:
            resume.set()
            finish.set()
        assert ("second", "racing turn") in calls if mode == "portable" else ("second", "racing turn") not in calls
        assert [row.status for row in await key_reservations(key["id"])] == (
            ["finalized"] if mode in {"portable", "pinned"} else []
        )
        assert all(get_source_bulkhead().in_flight(source_id) == 0 for source_id in (first, second))
        async with SessionLocal() as session:
            logs = (await session.execute(select(RequestLog).where(RequestLog.api_key_id == key["id"]))).scalars().all()
        assert len(logs) == (1 if mode in {"portable", "pinned"} else 0)
