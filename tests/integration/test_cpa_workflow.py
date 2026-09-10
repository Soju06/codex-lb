from __future__ import annotations

import json

import pytest
from aiohttp import web

from tests.integration.model_source_helpers import stub_source_upstreams

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_cpa_workflow_preserves_custom_tool_round_trip(async_client, path):
    requests = []
    call = {
        "type": "custom_tool_call",
        "id": "ctc_fixture",
        "call_id": "call_fixture",
        "name": "apply_patch",
        "namespace": "functions",
        "input": "*** Begin Patch\n*** End Patch",
    }

    async def cpa(request: web.Request) -> web.StreamResponse:
        if request.method == "GET":
            return web.json_response({"models": [{"slug": "cpa-fixture", "context_window": 8192}]})
        body = await request.json()
        requests.append(body)
        output = (
            [call]
            if len(requests) == 1
            else [
                {
                    "type": "message",
                    "id": "msg_done",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "Done.", "annotations": []}],
                }
            ]
        )
        response = {
            "id": f"resp_fixture_{len(requests)}",
            "object": "response",
            "status": "completed",
            "model": "cpa-fixture",
            "output": output,
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
        stream = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await stream.prepare(request)
        await stream.write(
            ("data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n").encode()
        )
        await stream.write_eof()
        return stream

    tools = [{"type": "custom", "name": "apply_patch", "description": "Apply a patch", "format": {"type": "text"}}]
    initial = [{"role": "user", "content": "Apply the empty patch."}]
    async with stub_source_upstreams() as start:
        created = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "cpa",
                "baseUrl": await start(cpa),
                "supportsResponses": True,
                "catalogMode": "cli_proxy_api",
            },
        )
        assert created.status_code == 200
        listed = await async_client.get("/v1/models")
        assert "cpa-fixture" in [item["id"] for item in listed.json()["data"]]
        first = await async_client.post(
            path,
            json={
                "model": "cpa-fixture",
                "instructions": "Use tools.",
                "input": initial,
                "tools": tools,
                "stream": True,
            },
        )
        assert first.status_code == 200, first.text
        assert '"custom_tool_call"' in first.text
        history = [
            *initial,
            call,
            {"type": "custom_tool_call_output", "call_id": "call_fixture", "output": "Patch applied."},
        ]
        second = await async_client.post(
            path,
            json={
                "model": "cpa-fixture",
                "instructions": "Use tools.",
                "input": history,
                "tools": tools,
                "stream": True,
            },
        )
        assert second.status_code == 200, second.text
        assert "Done." in second.text
    assert requests[0]["tools"] == tools
    assert requests[1]["tools"] == tools
    assert requests[1]["input"][-2:] == history[-2:]


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_cpa_workflow_preserves_discovered_function_and_continuation(async_client, path):
    captured = []
    loaded = {
        "type": "function",
        "name": "calculate",
        "defer_loading": True,
        "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}},
    }
    search = {
        "type": "tool_search_call",
        "id": "tsc_fixture",
        "call_id": "search_fixture",
        "execution": "server",
        "status": "completed",
        "arguments": {"query": "calculate"},
    }
    found = {
        "type": "tool_search_output",
        "id": "tso_fixture",
        "call_id": "search_fixture",
        "execution": "server",
        "status": "completed",
        "tools": [loaded],
    }
    called = {
        "type": "function_call",
        "id": "fc_fixture",
        "call_id": "calculate_fixture",
        "name": "calculate",
        "arguments": '{"a":23}',
        "status": "completed",
    }
    items = [search, found, called]

    async def cpa(request):
        if request.method == "GET":
            return web.json_response({"models": [{"slug": "cpa-search", "context_window": 8192}]})
        captured.append(await request.json())
        result = {
            "id": "resp_search_fixture",
            "object": "response",
            "status": "completed",
            "model": "cpa-search",
            "output": items,
            "usage": {"input_tokens": 3, "output_tokens": 5},
        }
        stream = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await stream.prepare(request)
        await stream.write(
            ("data: " + json.dumps({"type": "response.completed", "response": result}) + "\n\n").encode()
        )
        await stream.write_eof()
        return stream

    tools = [{"type": "tool_search", "execution": "server"}, loaded]
    async with stub_source_upstreams() as start:
        created = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "cpa",
                "baseUrl": await start(cpa),
                "supportsResponses": True,
                "catalogMode": "cli_proxy_api",
            },
        )
        assert created.status_code == 200
        listed = await async_client.get("/v1/models")
        assert "cpa-search" in [item["id"] for item in listed.json()["data"]]
        first = await async_client.post(
            path, json={"model": "cpa-search", "instructions": "Use tools", "input": [], "tools": tools, "stream": True}
        )
        assert first.status_code == 200, first.text
        assert "tool_search_output" in first.text
        history = [*items, {"type": "function_call_output", "call_id": "calculate_fixture", "output": "23"}]
        second = await async_client.post(
            path,
            json={
                "model": "cpa-search",
                "previous_response_id": "resp_search_fixture",
                "instructions": "Use tools",
                "input": history,
                "tools": tools,
                "stream": True,
            },
        )
        assert second.status_code == 200, second.text
    assert captured[0]["tools"] == tools
    assert captured[1]["tools"] == tools
    assert captured[1]["previous_response_id"] == "resp_search_fixture"
    assert captured[1]["input"] == history


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_cpa_workflow_normalizes_allowed_tool_aliases_without_dropping_tools(async_client, path):
    captured = []

    async def cpa(request: web.Request) -> web.StreamResponse:
        if request.method == "GET":
            return web.json_response({"models": [{"slug": "cpa-alias", "context_window": 8192}]})
        captured.append(await request.json())
        response = {
            "id": "resp_alias_fixture",
            "object": "response",
            "status": "completed",
            "model": "cpa-alias",
            "output": [],
            "usage": {"input_tokens": 3, "output_tokens": 0},
        }
        stream = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await stream.prepare(request)
        await stream.write(
            ("data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n").encode()
        )
        await stream.write_eof()
        return stream

    custom = {"type": "custom", "name": "apply_patch", "format": {"type": "text"}}
    namespace = {"type": "namespace", "name": "functions", "tools": [custom]}
    custom_choice = {"type": "custom", "name": "apply_patch", "namespace": "functions"}
    async with stub_source_upstreams() as start:
        created = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "cpa",
                "baseUrl": await start(cpa),
                "supportsResponses": True,
                "catalogMode": "cli_proxy_api",
            },
        )
        assert created.status_code == 200
        listed = await async_client.get("/v1/models")
        assert "cpa-alias" in [item["id"] for item in listed.json()["data"]]
        result = await async_client.post(
            path,
            json={
                "model": "cpa-alias",
                "instructions": "Use tools.",
                "input": [],
                "tools": [{"type": "web_search_preview"}, namespace],
                "tool_choice": {
                    "type": "allowed_tools",
                    "mode": "required",
                    "tools": [{"type": "web_search_preview"}, custom_choice],
                },
                "stream": True,
            },
        )
        assert result.status_code == 200, result.text
    assert captured[0]["tools"] == [{"type": "web_search"}, namespace]
    assert captured[0]["tool_choice"] == {
        "type": "allowed_tools",
        "mode": "required",
        "tools": [{"type": "web_search"}, custom_choice],
    }
