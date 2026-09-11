from __future__ import annotations

import json

import pytest
from aiohttp import web

from app.modules.model_sources import llmbox
from tests.integration.model_source_helpers import stub_source_upstreams

pytestmark = pytest.mark.integration


def install_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(llmbox.Path, "home", classmethod(lambda cls: tmp_path))
    cache = tmp_path / ".llmbox/cache/accesstoken"
    cache.parent.mkdir(parents=True)
    cache.write_text("metadata\nfirst-token\n")
    return cache


async def create_source(client, **changes):
    return await client.post(
        "/api/model-sources/",
        json={
            "kind": "llmbox",
            "name": "Company",
            "baseUrl": llmbox.LLMBOX_BASE_URL,
            "supportsResponses": True,
            "supportsChatCompletions": False,
            "models": [{"model": "company-test", "supportsTools": True}],
            **changes,
        },
    )


@pytest.mark.asyncio
async def test_llmbox_disabled_binding_and_honest_quota(async_client, monkeypatch, tmp_path):
    cache = install_cache(monkeypatch, tmp_path)
    created = await create_source(async_client)
    assert created.status_code == 200
    data = created.json()
    sid = data["id"]
    assert data["kind"] == "llmbox" and data["isEnabled"] is False
    assert "first-token" not in created.text
    for mutation in (
        {"baseUrl": "https://evil.invalid/v1"},
        {"apiKey": "not-allowed"},
        {"supportsEmbeddings": True},
        {"supportsAudioTranscriptions": True},
    ):
        assert (await async_client.patch(f"/api/model-sources/{sid}", json=mutation)).status_code == 400
    assert (await create_source(async_client, baseUrl="https://evil.invalid/v1")).status_code == 400
    refused = await async_client.post("/v1/responses", json={"model": "company-test", "input": "hello"})
    assert refused.status_code >= 400
    listed = (await async_client.get("/api/model-sources/")).json()["sources"][0]["companyStatus"]
    assert listed["quotaStatus"] == "unknown" and listed["remaining"] is None and listed["resetsAt"] is None
    assert listed["credentialCache"] == "present"
    assert listed["observedUsage"]["requests"] == 0
    assert listed["observedUsage"]["inputTokens"] is None
    cache.unlink()
    listed = (await async_client.get("/api/model-sources/")).json()["sources"][0]["companyStatus"]
    assert listed["credentialCache"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_llmbox_routes_sse_tools_refreshes_auth_and_logs_usage(async_client, monkeypatch, tmp_path, path):
    cache = install_cache(monkeypatch, tmp_path)
    calls = []

    async def upstream(request):
        body = await request.json()
        calls.append((request.headers.get("Authorization"), request.headers.get("x-source"), body))
        output = [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "sum_numbers",
                "arguments": '{"a":2,"b":3}',
                "status": "completed",
            }
        ]
        final = {
            "id": "resp_company",
            "object": "response",
            "status": "completed",
            "model": "company-test",
            "output": output,
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        }
        if body.get("stream"):
            events = [
                {"type": "response.created", "response": {**final, "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0, "item": output[0]},
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_1",
                    "output_index": 0,
                    "delta": '{"a":2,"b":3}',
                },
                {"type": "response.output_item.done", "output_index": 0, "item": output[0]},
                {"type": "response.completed", "response": final},
            ]
            return web.Response(
                text="".join(f"data: {json.dumps(e)}\n\n" for e in events), content_type="text/event-stream"
            )
        return web.json_response(final)

    async with stub_source_upstreams() as start:
        url = await start(upstream)
        monkeypatch.setattr(llmbox, "LLMBOX_BASE_URL", url)
        created = await create_source(async_client)
        assert created.status_code == 200
        sid = created.json()["id"]
        assert (await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})).status_code == 200
        response = await async_client.post(path, json={"model": "company-test", "input": "Add", "stream": True})
        assert response.status_code == 200, response.text
        assert '"call_1"' in response.text and "response.completed" in response.text
        cache.write_text("metadata\nsecond-token\n")
        tool_output = {"type": "function_call_output", "call_id": "call_1", "output": "5"}
        response = await async_client.post(path, json={"model": "company-test", "input": [tool_output], "stream": True})
        assert response.status_code == 200, response.text
        assert [c[0] for c in calls] == ["Bearer at-first-token", "Bearer at-second-token"]
        assert all(c[1] == "llmgw" for c in calls)
        assert calls[1][2]["input"] == [tool_output]
        usage = (await async_client.get("/api/model-sources/")).json()["sources"][0]["companyStatus"]["observedUsage"]
        assert usage["requests"] == 2 and usage["inputTokens"] == 22 and usage["outputTokens"] == 14
        cache.unlink()
        failed = await async_client.post(path, json={"model": "company-test", "input": "hello", "stream": True})
        assert failed.status_code >= 400
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_llmbox_errors_do_not_reflect_token(async_client, monkeypatch, tmp_path):
    install_cache(monkeypatch, tmp_path)
    calls = []

    async def upstream(request):
        calls.append(request.path)
        return web.Response(
            status=429,
            text=json.dumps({"error": {"message": "Bearer at-first-token"}}),
            content_type="application/json",
        )

    async with stub_source_upstreams() as start:
        url = await start(upstream)
        monkeypatch.setattr(llmbox, "LLMBOX_BASE_URL", url)
        sid = (await create_source(async_client)).json()["id"]
        await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
        response = await async_client.post("/v1/responses", json={"model": "company-test", "input": "hello"})
        assert response.status_code >= 400
        assert "first-token" not in response.text
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_llmbox_redirect_is_not_followed(async_client, monkeypatch, tmp_path):
    install_cache(monkeypatch, tmp_path)
    calls = []

    async def upstream(request):
        calls.append(request.path)
        return web.Response(status=307, headers={"Location": "/stolen"})

    async with stub_source_upstreams() as start:
        url = await start(upstream)
        monkeypatch.setattr(llmbox, "LLMBOX_BASE_URL", url)
        sid = (await create_source(async_client)).json()["id"]
        await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
        models = (await async_client.get("/v1/models")).json()
        assert "company-test" not in str(models)  # Unverified models remain explicitly callable.
        response = await async_client.post("/v1/responses", json={"model": "company-test", "input": "hello"})
        assert response.status_code >= 400
        assert calls == ["/v1/responses"]
