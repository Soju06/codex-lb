"""Exercise source keepalive with real sockets that deliberately withhold pongs."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import WSMsgType, web
from sqlalchemy import select

from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.modules.model_sources import websocket as transport
from app.modules.proxy import api
from app.modules.proxy import source_websocket as native
from app.modules.proxy.source_admission import get_source_bulkhead
from tests.integration.model_source_helpers import stub_source_upstreams
from tests.integration.test_model_source_websocket import create_key, create_source, websocket_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ROUTES = ["/v1/responses", "/backend-api/codex/responses"]


@pytest.fixture
def fast_keepalive(monkeypatch):
    # Accelerate the library defaults, preserving explicit production options.
    # Removing ping_timeout=None restores this short, failing pong deadline.
    connections = []
    original = transport._SourceConnect

    async def connect(*args, **options):
        options.setdefault("ping_interval", 0.025)
        options.setdefault("ping_timeout", 0.075)
        connection = await original(*args, **options)
        connections.append(connection)
        return connection

    monkeypatch.setattr(transport, "_SourceConnect", connect)
    return connections


async def created(ws, response_id):
    await ws.send_json(
        {"type": "response.created", "response": {"id": response_id, "status": "in_progress", "output": []}}
    )


@pytest.mark.parametrize("path", [*ROUTES, *(f"{path}/" for path in ROUTES)])
@pytest.mark.parametrize("late_pongs", [False, True])
async def test_delayed_pong_keeps_stream_and_continuation_alive(
    async_client, app_instance, fast_keepalive, path, late_pongs
):
    calls, pings = [], []
    handshakes = 0

    async def provider(request):
        nonlocal handshakes
        handshakes += 1
        ws = web.WebSocketResponse(autoping=False)
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.PING:
                if late_pongs:
                    await ws.pong(message.data)
                continue
            assert message.type == WSMsgType.TEXT
            calls.append(message.json())
            response_id = f"resp_delayed_pong_{len(calls)}"
            await created(ws, response_id)
            ping = await ws.receive(timeout=3)
            assert ping.type == WSMsgType.PING
            pings.append(ping.data)
            for _ in range(5):
                await asyncio.sleep(0.05)
                await ws.send_json({"type": "response.output_text.delta", "delta": "progress"})
            await ws.send_json(
                {
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
                    },
                }
            )
            if late_pongs:
                await ws.pong(ping.data)
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, path, key["key"]) as ws:
            for turn in range(2):
                payload = {"type": "response.create", "model": "source-ws-model", "input": "hi"}
                if turn:
                    payload["previous_response_id"] = "resp_delayed_pong_1"
                await ws.send(payload)
                assert (await ws.receive())["type"] == "response.created"
                for _ in range(5):
                    assert (await ws.receive())["type"] == "response.output_text.delta"
                assert (await ws.receive())["type"] == "response.completed"
                assert len(fast_keepalive[0].pending_pings) <= 1
        assert handshakes == 1 and len(calls) == len(pings) == 2
        assert not fast_keepalive[0].pending_pings
        assert fast_keepalive[0].keepalive_task is None or fast_keepalive[0].keepalive_task.done()
        assert calls[1]["previous_response_id"] == "resp_delayed_pong_1"
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            logs = (
                (await session.execute(select(RequestLog).where(RequestLog.model_source_id == source_id)))
                .scalars()
                .all()
            )
        assert [row.status for row in reservations] == ["finalized", "finalized"]
        assert len(logs) == 2
        assert all(row.status == "success" and row.input_tokens == 7 and row.output_tokens == 3 for row in logs)
        assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.parametrize("path", ROUTES)
async def test_client_disconnect_cancels_source_keepalive(async_client, app_instance, fast_keepalive, path):
    ping_seen, peer_closed = asyncio.Event(), asyncio.Event()

    async def provider(request):
        ws = web.WebSocketResponse(autoping=False)
        await ws.prepare(request)
        await ws.receive_json()
        await created(ws, "resp_client_leaves")
        await ws.send_json({"type": "response.output_text.delta", "delta": "partial"})
        async for message in ws:
            if message.type == WSMsgType.PING:
                ping_seen.set()
        peer_closed.set()
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            assert (await ws.receive())["type"] == "response.created"
            assert (await ws.receive())["type"] == "response.output_text.delta"
            await asyncio.wait_for(ping_seen.wait(), 3)
            keepalive_task = fast_keepalive[0].keepalive_task
            assert keepalive_task is not None and not keepalive_task.done()
            await ws.disconnect()
        await asyncio.wait_for(peer_closed.wait(), 3)
        assert keepalive_task.done() and not fast_keepalive[0].pending_pings
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            logs = (
                (await session.execute(select(RequestLog).where(RequestLog.model_source_id == source_id)))
                .scalars()
                .all()
            )
        assert [row.status for row in reservations] == ["finalized"]
        assert len(logs) == 1 and logs[0].status == "cancelled" and logs[0].error_code == "client_disconnected"
        assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("ending", ["first_frame_timeout", "stream_idle_timeout", "peer_close"])
async def test_missing_pong_preserves_failure_cleanup(
    async_client, app_instance, monkeypatch, fast_keepalive, path, ending
):
    calls, pings = [], []
    peer_closed = asyncio.Event()
    monkeypatch.setattr(native, "SOURCE_FIRST_FRAME_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(api, "source_stream_idle_seconds", lambda: 0.5)

    async def provider(request):
        ws = web.WebSocketResponse(autoping=False)
        await ws.prepare(request)
        calls.append(await ws.receive_json())
        if ending != "first_frame_timeout":
            await created(ws, "resp_stalled")
            await ws.send_json({"type": "response.output_text.delta", "delta": "partial"})
        async for message in ws:
            if message.type == WSMsgType.PING:
                pings.append(message.data)
                if ending == "peer_close":
                    await ws.close(code=1011, message=b"provider-secret-must-not-escape")
                    break
        peer_closed.set()
        return ws

    async with stub_source_upstreams() as start:
        source_id = await create_source(async_client, await start(provider, shutdown_timeout=1))
        key = await create_key(async_client, source_id)
        async with websocket_client(app_instance, path, key["key"]) as ws:
            await ws.send({"type": "response.create", "model": "source-ws-model", "input": "hi"})
            if ending != "first_frame_timeout":
                assert (await ws.receive())["type"] == "response.created"
                assert (await ws.receive())["type"] == "response.output_text.delta"
            event = await ws.receive()
            expected = "model_source_stream_truncated" if ending == "peer_close" else "model_source_timeout"
            assert event["type"] == "error" and event["error"]["code"] == expected
            assert event["status"] == (502 if ending == "peer_close" else 504)
            assert "provider-secret" not in str(event)
            assert await ws.receive_close() == 1000
        await asyncio.wait_for(peer_closed.wait(), 3)
        assert len(calls) == 1 and pings
        assert not fast_keepalive[0].pending_pings
        assert fast_keepalive[0].keepalive_task is None or fast_keepalive[0].keepalive_task.done()
        async with SessionLocal() as session:
            reservations = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            logs = (
                (await session.execute(select(RequestLog).where(RequestLog.model_source_id == source_id)))
                .scalars()
                .all()
            )
        assert [row.status for row in reservations] == ["released"]
        assert len(logs) == 1 and logs[0].status == "error" and logs[0].error_code == expected
        assert get_source_bulkhead().in_flight(source_id) == 0
