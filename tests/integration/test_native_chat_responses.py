from __future__ import annotations

import json

import pytest
from aiohttp import web

from app.modules.model_sources import codebase_llm, trae
from tests.integration.model_source_helpers import stub_source_upstreams

pytestmark = pytest.mark.integration


def parse_events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ") and line[6:] != "[DONE]"]


def sse(payload):
    return "data: " + json.dumps(payload) + "\n\n"


async def create(client, url):
    r = await client.post(
        "/api/model-sources/",
        json={
            "kind": "codebase_llm",
            "name": "Native Chat",
            "baseUrl": url,
            "supportsResponses": True,
            "supportsChatCompletions": True,
            "models": [
                {
                    "model": "codebase/test",
                    "supportsTools": True,
                    "rawMetadataJson": json.dumps({"native_backend": "chat"}),
                }
            ],
        },
    )
    assert r.status_code == 200
    sid = r.json()["id"]
    await client.patch("/api/model-sources/" + sid, json={"isEnabled": True})
    return sid


def auth(monkeypatch):
    monkeypatch.setattr(trae, "auth_headers", lambda: {"Authorization": "Cloud-CLI-JWT test-token"})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_native_chat_tools_reasoning_continuation_and_usage(async_client, monkeypatch, path):
    auth(monkeypatch)
    requests = []

    async def upstream(request):
        body = await request.json()
        requests.append(body)
        assert request.path.endswith("/chat/completions")
        assert body["model"] == "test"
        assert "session_id" not in body
        if len(requests) == 1:
            calls = [
                {
                    "index": i,
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": "get_marker", "arguments": "{}"},
                }
                for i in range(2)
            ]
            data = sse(
                {
                    "model": "test",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_content": "private-state", "tool_calls": calls},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            data += sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            assistant = next(m for m in body["messages"] if m.get("tool_calls"))
            assert assistant["reasoning_content"] == "private-state"
            assert len(assistant["tool_calls"]) == 2
            assert [m["tool_call_id"] for m in body["messages"] if m["role"] == "tool"] == ["call_0", "call_1"]
            data = sse(
                {"model": "test", "choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}]}
            )
        data += (
            sse(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "prompt_tokens_details": {"cached_tokens": 3},
                    },
                }
            )
            + "data: [DONE]\n\n"
        )
        return web.Response(text=data, content_type="text/event-stream")

    async with stub_source_upstreams() as start:
        url = await start(upstream)
        monkeypatch.setattr(codebase_llm, "CODEBASE_LLM_BASE_URL", url)
        await create(async_client, url)
        payload = {
            "model": "codebase/test",
            "input": [{"role": "user", "content": "Get marker"}],
            "tools": [{"type": "function", "name": "get_marker", "parameters": {"type": "object", "properties": {}}}],
            "stream": True,
        }
        r = await async_client.post(path, json=payload)
        assert r.status_code == 200
        assert "private-state" not in r.text
        final = parse_events(r.text)[-1]["response"]
        assert final["status"] == "completed"
        assert final["usage"]["input_tokens_details"]["cached_tokens"] == 3
        assert final["output"][-1]["encrypted_content"].startswith("codebase_chat-v1:")
        payload["input"] += final["output"] + [
            {"type": "function_call_output", "call_id": f"call_{i}", "output": "OK"} for i in range(2)
        ]
        payload["stream"] = False
        r = await async_client.post(path, json=payload)
        assert r.status_code == 200, r.text
        assert r.json()["output"][0]["content"][0]["text"] == "OK"
        assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["", sse({"error": {"message": "test-token"}})])
async def test_native_chat_truncation_and_error_never_complete(async_client, monkeypatch, ending):
    auth(monkeypatch)

    async def upstream(request):
        return web.Response(
            text=sse({"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]}) + ending,
            content_type="text/event-stream",
        )

    async with stub_source_upstreams() as start:
        url = await start(upstream)
        monkeypatch.setattr(codebase_llm, "CODEBASE_LLM_BASE_URL", url)
        await create(async_client, url)
        r = await async_client.post("/v1/responses", json={"model": "codebase/test", "input": "hi", "stream": True})
        events = parse_events(r.text)
        assert events[-1]["type"] == "response.failed"
        assert "response.completed" not in r.text
        assert "test-token" not in r.text


@pytest.mark.asyncio
async def test_native_chat_cancel_releases_source_for_next_request(async_client, monkeypatch):
    import asyncio
    import contextlib

    auth(monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def upstream(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(sse({"choices": [{"delta": {"content": "partial"}}]}).encode())
            started.set()
            await release.wait()
            with contextlib.suppress(ConnectionError):
                await response.write_eof()
            return response
        return web.Response(
            text=sse({"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]}) + "data: [DONE]\n\n",
            content_type="text/event-stream",
        )

    async with stub_source_upstreams() as start:
        url = await start(upstream)
        monkeypatch.setattr(codebase_llm, "CODEBASE_LLM_BASE_URL", url)
        sid = await create(async_client, url)
        await async_client.patch("/api/model-sources/" + sid, json={"maxConcurrency": 1})
        payload = {"model": "codebase/test", "input": "hello", "stream": True}
        task = asyncio.create_task(async_client.post("/v1/responses", json=payload))
        try:
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        result = await asyncio.wait_for(async_client.post("/v1/responses", json=payload), 3)
        assert result.status_code == 200
        assert parse_events(result.text)[-1]["type"] == "response.completed"
        assert calls == 2


@pytest.mark.asyncio
async def test_native_presets_exclude_failed_and_glm_models(async_client):
    response = await async_client.get("/api/model-sources/company-catalog/codebase")
    assert response.status_code == 200
    models = response.json()
    assert len(models) == 10
    assert all(not row["isEnabled"] and row["model"].startswith("codebase/") for row in models)
    assert all("glm" not in row["model"] and "5.2-codex" not in row["model"] for row in models)
    assert sum(json.loads(row["rawMetadataJson"])["native_backend"] == "chat" for row in models) == 8
