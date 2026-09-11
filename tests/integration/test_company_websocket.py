from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from sqlalchemy import select

from app.db.models import RequestLog
from app.db.session import get_background_session
from app.modules.model_sources import llmbox
from tests.integration.model_source_helpers import stub_source_upstreams
from tests.integration.test_llmbox_source import create_source, install_cache

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [True, False])
async def test_company_websocket_cancellation_releases_slot(async_client, app_instance, monkeypatch, tmp_path, cancel):
    install_cache(monkeypatch, tmp_path)
    gate = asyncio.Event()
    calls = []

    async def upstream(request):
        calls.append(await request.json())
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(
            b'data: {"type":"response.created","response":{"id":"resp_wait","status":"in_progress"}}\n\n'
        )
        if len(calls) == 1:
            await gate.wait()
        else:
            await response.write(
                b'data: {"type":"response.completed","response":{"id":"resp_done","status":"completed",'
                b'"output":[],"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
            )
        return response

    async with stub_source_upstreams() as start:
        monkeypatch.setattr(llmbox, "LLMBOX_BASE_URL", await start(upstream))
        sid = (await create_source(async_client, maxConcurrency=1)).json()["id"]
        await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
        try:
            async with websocket(app_instance, "/v1/responses") as (incoming, outgoing):
                await send(incoming, input="wait")
                frame = await asyncio.wait_for(outgoing.get(), 5)
                assert json.loads(frame["text"])["type"] == "response.created"
                if cancel:
                    await incoming.put({"type": "websocket.receive", "text": '{"type":"response.cancel"}'})
                    assert (await terminal(outgoing))[-1]["error"]["code"] == "response_cancelled"
            gate.set()
            async with websocket(app_instance, "/v1/responses") as (incoming, outgoing):
                await send(incoming, input="after cancellation")
                assert (await terminal(outgoing))[-1]["type"] == "response.completed"
            assert len(calls) == 2
            state = (await async_client.get("/api/model-sources/")).json()["sources"][0]["companyStatus"]
            assert state["failures"] == 0
        finally:
            gate.set()


@asynccontextmanager
async def websocket(app, path):
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"localhost")],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 80),
        "subprotocols": [],
        "state": {},
    }
    task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
    await incoming.put({"type": "websocket.connect"})
    try:
        assert (await asyncio.wait_for(outgoing.get(), 5))["type"] == "websocket.accept"
        yield incoming, outgoing
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(task, 5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def send(queue, **body):
    await queue.put(
        {"type": "websocket.receive", "text": json.dumps({"type": "response.create", "model": "company-test", **body})}
    )


async def terminal(queue):
    events = []
    while True:
        frame = await asyncio.wait_for(queue.get(), 5)
        assert frame["type"] == "websocket.send", frame
        event = json.loads(frame["text"])
        events.append(event)
        if event["type"] in {"response.completed", "response.failed", "error"}:
            return events


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_company_websocket_tools_and_policy(async_client, app_instance, monkeypatch, tmp_path, path):
    install_cache(monkeypatch, tmp_path)
    calls = []
    tool = {
        "type": "function_call",
        "id": "fc_test",
        "call_id": "call_test",
        "name": "read",
        "arguments": "{}",
        "status": "completed",
    }

    async def upstream(request):
        calls.append(await request.json())
        final = {
            "id": f"resp_{len(calls)}",
            "object": "response",
            "status": "completed",
            "model": "company-test",
            "output": [tool] if len(calls) == 1 else [],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }
        events = [
            {"type": "response.created", "response": {**final, "status": "in_progress", "output": []}},
            {"type": "response.completed", "response": final},
        ]
        return web.Response(
            text="".join(f"data: {json.dumps(e)}\n\n" for e in events), content_type="text/event-stream"
        )

    async with stub_source_upstreams() as start:
        monkeypatch.setattr(llmbox, "LLMBOX_BASE_URL", await start(upstream))
        sid = (
            await create_source(
                async_client,
                maxConcurrency=1,
                models=[
                    {"model": "company-test", "supportsTools": True},
                    {"model": "company-other", "supportsTools": True},
                ],
            )
        ).json()["id"]
        async with websocket(app_instance, path) as (incoming, outgoing):
            await send(incoming, input="hello")
            assert (await terminal(outgoing))[-1]["type"] == "error"
            assert not calls
            await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
            await send(incoming, input="hello")
            first = (await terminal(outgoing))[-1]
            assert first["type"] == "response.completed", first
            await send(
                incoming,
                previous_response_id=first["response"]["id"],
                input=[{"type": "function_call_output", "call_id": "call_test", "output": "hello"}],
            )
            assert (await terminal(outgoing))[-1]["type"] == "response.completed"
            assert calls[1]["input"][-2:] == [
                tool,
                {"type": "function_call_output", "call_id": "call_test", "output": "hello"},
            ]
            assert "previous_response_id" not in calls[1]
            await send(incoming, model="company-other", previous_response_id="resp_2", input=[])
            assert (await terminal(outgoing))[-1]["error"]["code"] == "previous_response_not_found"
            await send(incoming, previous_response_id="resp_missing", input=[])
            assert (await terminal(outgoing))[-1]["error"]["code"] == "previous_response_not_found"
            await async_client.patch(f"/api/model-sources/{sid}", json={"localTokenBudget": 1})
            await send(incoming, input="hello")
            assert (await terminal(outgoing))[-1]["error"]["code"] == "model_source_budget_exhausted"
        assert len(calls) == 2
        async with get_background_session() as session:
            rows = list((await session.scalars(select(RequestLog).where(RequestLog.model_source_id == sid))).all())
            assert len(rows) == 2
            assert all(row.transport == "websocket" and row.account_id is None for row in rows)
