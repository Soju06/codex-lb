from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from aiohttp import web
from httpx import AsyncClient

from app.core.types import JsonValue
from tests.integration.model_source_helpers import _create_model_source, stub_source_upstreams

pytestmark = pytest.mark.integration

_RESPONSE_PATHS = (
    "/v1/responses",
    "/v1/responses/",
    "/backend-api/codex/responses",
    "/backend-api/codex/responses/",
)
_NAMESPACE: dict[str, JsonValue] = {
    "type": "namespace",
    "name": "collaboration",
    "description": "Delegate work and collect its result.",
    "tools": [
        {
            "type": "function",
            "name": "spawn_agent",
            "description": "Start a child agent.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}, "agent_type": {"type": "string"}},
                "required": ["message", "agent_type"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "wait",
            "description": "Wait for child agents.",
            "parameters": {
                "type": "object",
                "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["ids"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ],
}
_FUNCTION: dict[str, JsonValue] = {
    "type": "function",
    "name": "shell",
    "description": "Run a shell command.",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
}


@pytest.fixture
async def source_upstream() -> AsyncIterator[Callable[..., Awaitable[str]]]:
    async with stub_source_upstreams() as start:
        yield start


async def _capture_source_request(
    async_client: AsyncClient,
    source_upstream: Callable[..., Awaitable[str]],
    *,
    path: str,
    metadata: dict[str, JsonValue],
    tool_choice: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    captured: list[dict[str, JsonValue]] = []

    async def capture(request: web.Request) -> web.Response:
        assert request.path == "/v1/responses"
        captured.append(await request.json())
        event = {
            "type": "response.completed",
            "response": {"id": "resp_collaboration", "status": "completed", "output": []},
        }
        return web.Response(text=f"data: {json.dumps(event)}\n\n", content_type="text/event-stream")

    base_url = await source_upstream(capture)
    await _create_model_source(
        async_client,
        name="collaboration-source",
        model="collaboration-model",
        base_url=base_url,
        supports_responses=True,
        raw_metadata_json=json.dumps(metadata),
    )
    response = await async_client.post(
        path,
        json={
            "model": "collaboration-model",
            "instructions": "Use the available tools.",
            "input": [{"role": "user", "content": "Delegate this task."}],
            "stream": True,
            "tools": [_NAMESPACE, _FUNCTION, {"type": "web_search"}],
            "tool_choice": tool_choice,
            "parallel_tool_calls": True,
            "include": ["web_search_call.action.sources", "reasoning.encrypted_content"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 200, response.text
    assert "response.completed" in response.text
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _RESPONSE_PATHS)
@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({"multi_agent_version": "v1"}, id="v1"),
        pytest.param({"multi_agent_version": "v2"}, id="v2"),
        pytest.param({"experimental_supported_tools": ["namespace"]}, id="explicit-namespace"),
    ],
)
@pytest.mark.parametrize(
    "choice",
    [
        pytest.param({"type": "namespace", "name": "collaboration"}, id="namespace-choice"),
        pytest.param({"type": "function", "namespace": "collaboration", "name": "spawn_agent"}, id="function-choice"),
        pytest.param(
            {"type": "allowed_tools", "mode": "required", "tools": [{"type": "namespace", "name": "collaboration"}]},
            id="allowed-choice",
        ),
    ],
)
async def test_source_responses_preserves_collaboration_tools_and_choices(
    async_client: AsyncClient,
    source_upstream: Callable[..., Awaitable[str]],
    path: str,
    metadata: dict[str, JsonValue],
    choice: dict[str, JsonValue],
) -> None:
    captured = await _capture_source_request(
        async_client, source_upstream, path=path, metadata=metadata, tool_choice=choice
    )

    assert captured["tools"] == [_NAMESPACE, _FUNCTION]
    assert captured["tool_choice"] == choice
    assert captured["parallel_tool_calls"] is True
    assert captured["include"] == ["reasoning.encrypted_content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _RESPONSE_PATHS)
@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({}, id="absent"),
        pytest.param({"multi_agent_version": " \t"}, id="blank"),
        pytest.param({"multi_agent_version": ["v2"]}, id="non-string"),
    ],
)
async def test_source_responses_without_collaboration_capability_drops_namespaces(
    async_client: AsyncClient,
    source_upstream: Callable[..., Awaitable[str]],
    path: str,
    metadata: dict[str, JsonValue],
) -> None:
    captured = await _capture_source_request(
        async_client,
        source_upstream,
        path=path,
        metadata=metadata,
        tool_choice={"type": "namespace", "name": "collaboration"},
    )

    assert captured["tools"] == [_FUNCTION]
    assert "tool_choice" not in captured
    assert captured["include"] == ["reasoning.encrypted_content"]
