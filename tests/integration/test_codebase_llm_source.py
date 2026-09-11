from __future__ import annotations

import json

import pytest
from aiohttp import web

from app.modules.model_sources import codebase_llm, trae
from tests.integration.model_source_helpers import stub_source_upstreams

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_codebase_native_binding_and_forwarding(async_client, monkeypatch, tmp_path, path):
    monkeypatch.setattr(trae.Path, "home", classmethod(lambda cls: tmp_path))
    cache = tmp_path / ".trae/cli/auth.json"
    cache.parent.mkdir(parents=True)

    def login(token, kind="cloud_cli_jwt"):
        cache.write_text(json.dumps({"trae": {"access_token": token, "credential_kind": kind, "region": "CN"}}))

    login("first-token")
    calls = []

    async def upstream(request):
        calls.append((request.headers.get("Authorization"), await request.json()))
        final = {
            "id": "resp_native",
            "object": "response",
            "status": "completed",
            "model": "codebase/native-test",
            "output": [],
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        }
        return web.Response(
            text="data: " + json.dumps({"type": "response.completed", "response": final}) + "\n\n",
            content_type="text/event-stream",
        )

    async with stub_source_upstreams() as start:
        url = await start(upstream)
        monkeypatch.setattr(codebase_llm, "CODEBASE_LLM_BASE_URL", url)
        created = await async_client.post(
            "/api/model-sources/",
            json={
                "kind": "codebase_llm",
                "name": "Native",
                "baseUrl": url,
                "supportsChatCompletions": False,
                "supportsResponses": True,
                "models": [
                    {
                        "model": "codebase/native-test",
                        "rawMetadataJson": '{"native_backend":"responses"}',
                    }
                ],
            },
        )
        assert created.status_code == 200
        source = created.json()
        sid = source["id"]
        assert source["isEnabled"] is False
        assert source["companyStatus"]["quotaStatus"] == "unknown"
        for mutation in ({"baseUrl": "https://evil.invalid"}, {"apiKey": "forbidden"}, {"supportsEmbeddings": True}):
            assert (await async_client.patch("/api/model-sources/" + sid, json=mutation)).status_code == 400
        await async_client.patch("/api/model-sources/" + sid, json={"isEnabled": True})
        for token in ("first-token", "second-token"):
            login(token)
            response = await async_client.post(
                path, json={"model": "codebase/native-test", "input": "hello", "stream": True}
            )
            assert response.status_code == 200
            assert "response.completed" in response.text
            assert token not in response.text
        assert [call[0] for call in calls] == ["Bearer first-token", "Bearer second-token"]
        assert all(call[1]["model"] == "native-test" for call in calls)
        login("third-token", "trae_oauth")
        response = await async_client.post(
            path, json={"model": "codebase/native-test", "input": "hello", "stream": True}
        )
        assert response.status_code >= 400
        assert len(calls) == 2
        status = (await async_client.get("/api/model-sources/")).json()["sources"][0]["companyStatus"]
        assert status["credentialCache"] == "unavailable"
        assert status["observedUsage"]["requests"] == 3
        assert status["observedUsage"]["requestsWithoutUsage"] == 1
        assert status["observedUsage"]["inputTokens"] == 22


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        {"model": "gpt-5.4", "rawMetadataJson": '{"native_backend":"responses"}'},
        {"model": "codebase/gpt-5.4"},
        {"model": "codebase/gpt-5.4", "rawMetadataJson": '{"native_backend":"other"}'},
    ],
)
async def test_codebase_rejects_ambiguous_model_bindings(async_client, monkeypatch, model):
    monkeypatch.setattr(codebase_llm, "validate_binding", lambda *args: None)
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "kind": "codebase_llm",
            "name": "Native",
            "baseUrl": codebase_llm.CODEBASE_LLM_BASE_URL,
            "supportsResponses": True,
            "models": [model],
        },
    )
    assert response.status_code == 400
