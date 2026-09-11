from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web

from app.modules.model_sources import trae
from tests.integration.model_source_helpers import TraeRawChatHarness, _AsgiStream, stub_source_upstreams

pytestmark = pytest.mark.integration


def frame(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def install_login(monkeypatch, tmp_path):
    monkeypatch.setattr(trae.Path, "home", classmethod(lambda cls: tmp_path))
    path = tmp_path / ".trae/cli/auth.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"trae": {"access_token": "local-secret", "credential_kind": "cloud_cli_jwt", "region": "CN"}})
    )
    return path


async def create(client):
    response = await client.post(
        "/api/model-sources/",
        json={
            "kind": "trae",
            "name": "TRAE",
            "baseUrl": trae.TRAE_BASE_URL,
            "supportsChatCompletions": False,
            "supportsResponses": True,
            "models": [
                {
                    "model": "trae-astra",
                    "supportsTools": True,
                    "rawMetadataJson": json.dumps(
                        {"trae_config_name": "gpt-6-astra", "trae_model_name": "gpt-6-astra__dev"}
                    ),
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["isEnabled"] is False
    sid = response.json()["id"]
    await client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
    return sid


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_trae_tool_roundtrip_and_usage(async_client, monkeypatch, tmp_path, path):
    install_login(monkeypatch, tmp_path)
    requests = []

    async def upstream(request):
        body = await request.json()
        if requests:
            assert body["session_id"] == requests[0]["session_id"]
            assistant = next(x for x in body["messages"] if x["role"] == "assistant")
            assert assistant["extra_info"] == {"opaque": "continuation-test"}
        requests.append(body)
        assert request.headers["Authorization"] == "Cloud-CLI-JWT local-secret"
        assert body["model_name"] == "gpt-6-astra__dev"
        if len(requests) == 1:
            call = {"index": 0, "id": "call_probe", "function_call": {"name": "get_marker", "arguments": ""}}
            data = frame("queue_begin", {}) + frame("output", {"tool_calls": [call]})
            data += frame("output", {"tool_calls": [{"index": 0, "function_call": {"arguments": "{}"}}]})
            data += frame("output", {"tool_calls": [call]})
            reason = "tool_calls"
        else:
            tool = next(x for x in body["messages"] if x["role"] == "tool")
            assert tool["tool_call_id"] == "call_probe" and tool["name"] == "get_marker"
            assert tool["content"][0]["text"] == "MARKER_42"
            data = frame("output", {"response": "MARKER_42"})
            reason = "stop"
        data += frame("token_usage", {"prompt_tokens": 15, "completion_tokens": 4})
        data += frame("timing_cost", {"total_ms": 10})
        data += frame("extra_info", {"opaque": "continuation-test"})
        data += frame("done", {"finish_reason": reason})
        return web.Response(text=data, content_type="text/event-stream")

    async with stub_source_upstreams() as start:
        url = await start(upstream)
        monkeypatch.setattr(trae, "TRAE_BASE_URL", url)
        sid = await create(async_client)
        inp = [{"role": "user", "content": "Get marker"}]
        first = await async_client.post(path, json={"model": "trae-astra", "input": inp, "stream": True})
        assert first.status_code == 200, first.text
        events = [
            json.loads(line[6:])
            for line in first.text.splitlines()
            if line.startswith("data: ") and line[6:] != "[DONE]"
        ]
        final = next(e["response"] for e in events if e.get("type") == "response.completed")
        call = final["output"][0]
        assert call["name"] == "get_marker" and call["arguments"] == "{}"
        result = {"type": "function_call_output", "call_id": call["call_id"], "output": "MARKER_42"}
        second = await async_client.post(path, json={"model": "trae-astra", "input": inp + final["output"] + [result]})
        assert second.status_code == 200, second.text
        assert "MARKER_42" in second.text
        usage = (await async_client.get("/api/model-sources/")).json()["sources"][0]["companyStatus"]
        assert usage["quotaStatus"] == "unknown"
        assert usage["observedUsage"]["inputTokens"] == 30
        assert "local-secret" not in json.dumps(usage)
        for change in [{"baseUrl": "https://untrusted.invalid"}, {"apiKey": "foo"}, {"supportsChatCompletions": True}]:
            assert (await async_client.patch(f"/api/model-sources/{sid}", json=change)).status_code == 400


@pytest.mark.asyncio
async def test_trae_continues_after_foreign_reasoning_and_compaction(async_client, monkeypatch, tmp_path):
    install_login(monkeypatch, tmp_path)
    harness = TraeRawChatHarness(response_text="continued")

    async with stub_source_upstreams() as start:
        monkeypatch.setattr(trae, "TRAE_BASE_URL", await start(harness.handler))
        await create(async_client)
        result = await async_client.post(
            "/backend-api/codex/responses",
            json={
                "model": "trae-astra",
                "input": [
                    {"role": "assistant", "content": "visible answer"},
                    {"type": "reasoning", "encrypted_content": "foreign-provider-state"},
                    {"type": "compaction", "encrypted_content": "foreign-compaction-state"},
                    {"role": "user", "content": "continue"},
                ],
                "stream": True,
            },
        )

    assert result.status_code == 200, result.text
    assert "continued" in result.text
    messages = harness.requests[0]["messages"]
    assert [message["role"] for message in messages] == ["system", "assistant", "user"]
    assert "cannot be decoded" in messages[0]["content"][0]["text"]
    assert messages[-1]["content"][0]["text"] == "continue"


@pytest.mark.asyncio
async def test_trae_harness_proves_live_streaming_and_four_slot_admission(async_client, monkeypatch, tmp_path):
    install_login(monkeypatch, tmp_path)
    harness = TraeRawChatHarness(response_text="streamed", hold_after_output=True)

    async with stub_source_upstreams() as start:
        monkeypatch.setattr(trae, "TRAE_BASE_URL", await start(harness.handler))
        source_id = await create(async_client)
        patched = await async_client.patch(f"/api/model-sources/{source_id}", json={"maxConcurrency": 4})
        assert patched.status_code == 200, patched.text

        body = json.dumps({"model": "trae-astra", "input": "hello", "stream": True}).encode()
        streams = [
            _AsgiStream(
                app=async_client._transport.app,
                path="/backend-api/codex/responses",
                headers={},
                body=body,
            )
            for _ in range(4)
        ]
        runners = [asyncio.create_task(stream.run()) for stream in streams]
        await harness.wait_for_active(4)
        await asyncio.gather(*(stream.wait_for_text("response.output_text.delta") for stream in streams))

        overflow = await async_client.post(
            "/backend-api/codex/responses",
            json={"model": "trae-astra", "input": "overflow", "stream": True},
        )
        assert overflow.status_code == 503
        assert overflow.json()["error"]["code"] == "model_source_busy"
        assert harness.max_active == 4

        harness.release.set()
        await asyncio.gather(*runners)

    assert all(stream.status == 200 for stream in streams)
    assert all(b"response.completed" in stream.received() for stream in streams)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["error", "eof", "redirect"])
async def test_trae_failure_is_not_success(async_client, monkeypatch, tmp_path, mode):
    install_login(monkeypatch, tmp_path)
    paths = []

    async def upstream(request):
        paths.append(request.path)
        if mode == "redirect":
            return web.Response(status=307, headers={"Location": "/secret"})
        return web.Response(
            text=frame("error", {"message": "local-secret"})
            if mode == "error"
            else frame("progress_notice", "waiting"),
            content_type="text/event-stream",
        )

    async with stub_source_upstreams() as start:
        monkeypatch.setattr(trae, "TRAE_BASE_URL", await start(upstream))
        await create(async_client)
        result = await async_client.post("/v1/responses", json={"model": "trae-astra", "input": "hello"})
        assert result.status_code >= 400, result.text
        assert "local-secret" not in result.text
        assert paths == ["/v1/llm_raw_chat"]
