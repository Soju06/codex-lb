from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web
from sqlalchemy import select

from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from tests.integration.model_source_helpers import (
    _AsgiStream,
    _create_model_source,
    _enable_api_key_auth,
    stub_source_upstreams,
)

pytestmark = pytest.mark.integration
PUBLIC = "cd/gpt-6-astra"
UPSTREAM = "cd/linxaq"
TEXT = f"Tiếng Việt {UPSTREAM}"
USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
TOOLS = [
    {
        "type": "namespace",
        "name": "collaboration",
        "tools": [
            {"type": "function", "name": "spawn_agent", "parameters": {"type": "object", "properties": {}}},
        ],
    }
]


@pytest.fixture
async def source_upstream():
    async with stub_source_upstreams() as start:
        yield start


async def _alias_source(client, base_url: str) -> str:
    return await _create_model_source(
        client,
        name="alias-source",
        model=PUBLIC,
        base_url=base_url,
        supports_responses=True,
        supports_embeddings=True,
        supports_audio_transcriptions=True,
        input_per_1m=2,
        output_per_1m=4,
        raw_metadata_json=json.dumps(
            {
                "upstream_model": UPSTREAM,
                "slug": UPSTREAM,
                "multi_agent_version": "v2",
                "base_instructions": "Use collaboration tools.",
                "source_request_overrides": {"model": "must-not-override-mapping"},
            }
        ),
    )


async def _key(client, source_id: str, **extra: Any) -> tuple[str, str]:
    response = await client.post(
        "/api/api-keys/",
        json={
            "name": "alias-key",
            "assignedSourceIds": [source_id],
            "allowedModels": [PUBLIC],
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 10000}],
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["key"], response.json()["id"]


def _response(model: str = UPSTREAM) -> dict[str, Any]:
    return {
        "id": "resp_alias",
        "object": "response",
        "model": model,
        "status": "completed",
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": TEXT, "annotations": []},
                ],
            },
        ],
        "usage": USAGE,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses", "/backend-api/codex/responses/"]
)
@pytest.mark.parametrize("streaming", [False, True])
async def test_alias_responses_routing_catalog_and_accounting(async_client, source_upstream, path, streaming):
    await _enable_api_key_auth(async_client)
    requests: list[dict[str, Any]] = []

    async def handler(request: web.Request):
        payload = await request.json()
        requests.append(payload)
        assert request.headers["Authorization"] == "Bearer token-alias-source"
        if not payload["stream"]:
            return web.json_response(_response())
        frames: list[dict[str, Any]] = [
            {"type": "response.created", "response": {**_response(), "status": "in_progress", "output": []}},
            {"type": "response.completed", "response": _response()},
        ]
        wire = b"\xef\xbb\xbf" + b"".join(
            (
                "id: frame-1\r\nevent: "
                + frame["type"]
                + "\r\ndata: "
                + json.dumps(frame, ensure_ascii=False)
                + "\r\n\r\n"
            ).encode()
            for frame in frames
        )
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for offset in range(0, len(wire), 7):
            await response.write(wire[offset : offset + 7])
        return response

    source_id = await _alias_source(async_client, await source_upstream(handler))
    key, key_id = await _key(async_client, source_id)
    headers = {"Authorization": f"Bearer {key}", "session_id": "alias-session"}
    for catalog_path in ["/v1/models", "/backend-api/codex/models", "/v1/models?client_version=0.156.1"]:
        catalog = await async_client.get(catalog_path, headers=headers)
        assert catalog.status_code == 200, catalog.text
        assert "upstream_model" not in catalog.text
        assert PUBLIC in catalog.text
        if "models" in catalog.json():
            entry = next(item for item in catalog.json()["models"] if item["slug"] == PUBLIC)
            assert entry["multi_agent_version"] == "v2"
            assert entry["base_instructions"] == "Use collaboration tools."
    result = await async_client.post(
        path,
        headers=headers,
        json={
            "model": PUBLIC,
            "input": "hi",
            "stream": streaming,
            "tools": TOOLS,
        },
    )
    assert result.status_code == 200, result.text
    assert requests[0]["model"] == UPSTREAM
    assert requests[0]["tools"] == TOOLS
    if requests[0]["stream"]:
        events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: {")]
        completed = next(event["response"] for event in events if event.get("type") == "response.completed")
        assert all(event["response"]["model"] == PUBLIC for event in events if "model" in event.get("response", {}))
    else:
        completed = result.json()
        assert completed["model"] == PUBLIC
    assert completed["output"][0]["content"][0]["text"] == TEXT
    assert completed["usage"]["total_tokens"] == 15
    async with SessionLocal() as session:
        rows = list(
            (await session.execute(select(RequestLog).where(RequestLog.model_source_id == source_id))).scalars()
        )
        assert len(rows) == 1
        assert rows[0].model == PUBLIC
        assert rows[0].status == "success"
        assert rows[0].cost_usd == pytest.approx(0.00004)
        reservations = list(
            (
                await session.execute(
                    select(ApiKeyUsageReservation).where(
                        ApiKeyUsageReservation.api_key_id == key_id, ApiKeyUsageReservation.model == PUBLIC
                    )
                )
            ).scalars()
        )
        assert [row.status for row in reservations] == ["finalized"]
        assert reservations[0].model == PUBLIC


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route",
    [
        "chat/completions",
        "chat/completions/",
        "embeddings",
        "embeddings/",
        "audio/transcriptions",
        "audio/transcriptions/",
    ],
)
async def test_alias_other_http_routes(async_client, source_upstream, route):
    observed = []

    async def handler(request):
        if "audio/" in request.path:
            body = await request.post()
            observed.append(body["model"])
            return web.json_response(
                {"model": UPSTREAM, "text": TEXT, "usage": {"prompt_tokens": 10, "total_tokens": 10}}
            )
        body = await request.json()
        observed.append(body["model"])
        if "embeddings" in request.path:
            return web.json_response(
                {
                    "object": "list",
                    "model": UPSTREAM,
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.2]}],
                    "usage": {"prompt_tokens": 10, "total_tokens": 10},
                }
            )
        return web.json_response(
            {
                "id": "chatcmpl_alias",
                "object": "chat.completion",
                "created": 1,
                "model": UPSTREAM,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": TEXT}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )

    await _alias_source(async_client, await source_upstream(handler))
    if "audio/" in route:
        result = await async_client.post(
            "/v1/" + route, data={"model": PUBLIC}, files={"file": ("a.wav", b"audio", "audio/wav")}
        )
    else:
        payload = {"model": PUBLIC}
        payload.update(
            {"input": "hi", "dimensions": None}
            if "embeddings" in route
            else {"messages": [{"role": "user", "content": "hi"}]}
        )
        result = await async_client.post("/v1/" + route, json=payload)
    if route.endswith("/"):
        assert result.status_code == 405
        assert result.json()["error"]["code"] == "invalid_request_error"
        assert observed == []
        return
    assert result.status_code == 200, result.text
    assert observed == [UPSTREAM]
    assert result.json()["model"] == PUBLIC
    if "chat/" in route:
        assert result.json()["choices"][0]["message"]["content"] == TEXT
    if "audio/" in route:
        assert result.json()["text"] == TEXT


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, "", "  ", 12, [], {}, "x" * 256])
async def test_invalid_alias_metadata_is_atomic(async_client, invalid):
    source_id = await _alias_source(async_client, "http://127.0.0.1:9/v1")
    models = [{"model": PUBLIC, "rawMetadataJson": json.dumps({"upstream_model": invalid})}]
    created = await async_client.post(
        "/api/model-sources/", json={"name": "invalid", "baseUrl": "http://127.0.0.1:9/v1", "models": models}
    )
    assert created.status_code == 400
    updated = await async_client.patch(f"/api/model-sources/{source_id}", json={"models": models})
    assert updated.status_code == 400
    sources = (await async_client.get("/api/model-sources/")).json()["sources"]
    assert len(sources) == 1
    assert json.loads(sources[0]["models"][0]["rawMetadataJson"])["upstream_model"] == UPSTREAM


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["model", "source", "disabled"])
async def test_alias_cannot_escape_policy(async_client, source_upstream, restriction):
    await _enable_api_key_auth(async_client)
    requests = []

    async def handler(request):
        requests.append(await request.json())
        return web.json_response(_response())

    base_url = await source_upstream(handler)
    source_id = await _alias_source(async_client, base_url)
    extra = {"allowedModels": [UPSTREAM]} if restriction == "model" else {}
    if restriction == "source":
        other_id = await _create_model_source(
            async_client, name="other", model="other", base_url=base_url, supports_responses=True
        )
        extra["assignedSourceIds"] = [other_id]
    if restriction == "disabled":
        assert (
            await async_client.patch(f"/api/model-sources/{source_id}", json={"isEnabled": False})
        ).status_code == 200
    key, _ = await _key(async_client, source_id, **extra)
    result = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": PUBLIC, "input": "hi", "stream": False},
    )
    assert result.status_code >= 400, result.text
    assert "error" in result.json()
    assert requests == []


@pytest.mark.asyncio
async def test_alias_stream_disconnect_releases_transport_and_reservation(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    upstream_closed = asyncio.Event()

    async def handler(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        try:
            await response.write(
                (
                    "data: "
                    + json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_alias",
                                "object": "response",
                                "model": UPSTREAM,
                                "status": "in_progress",
                                "output": [],
                            },
                        }
                    )
                    + "\n\n"
                ).encode()
            )
            await asyncio.Event().wait()
        finally:
            upstream_closed.set()
        return response

    source_id = await _alias_source(
        async_client, await source_upstream(handler, handler_cancellation=True, shutdown_timeout=1)
    )
    key, key_id = await _key(async_client, source_id)
    stream = _AsgiStream(
        app=async_client._transport.app,
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps({"model": PUBLIC, "input": "hi", "stream": True}).encode(),
    )
    runner = asyncio.create_task(stream.run())
    try:
        await stream.wait_for_response_start()
        stream.disconnect()
        await asyncio.wait_for(runner, 10)
        await asyncio.wait_for(upstream_closed.wait(), 5)
    finally:
        if not runner.done():
            runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
    async with SessionLocal() as session:
        reservations = list(
            (
                await session.execute(
                    select(ApiKeyUsageReservation).where(
                        ApiKeyUsageReservation.api_key_id == key_id, ApiKeyUsageReservation.model == PUBLIC
                    )
                )
            ).scalars()
        )
        assert [row.status for row in reservations] == ["released"]


@pytest.mark.asyncio
@pytest.mark.parametrize("limited", [False, True])
async def test_chat_alias_stream_uses_public_model_and_preserves_tool_calls(async_client, source_upstream, limited):
    await _enable_api_key_auth(async_client)
    captured = []
    arguments = json.dumps({"model": UPSTREAM})

    async def handler(request):
        captured.append(await request.json())
        payload = {
            "id": "chat_alias",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": UPSTREAM,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": TEXT,
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "run", "arguments": arguments},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return web.Response(
            body=("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode(), content_type="text/event-stream"
        )

    source_id = await _alias_source(async_client, await source_upstream(handler))
    key, _ = await _key(async_client, source_id, **({} if limited else {"limits": []}))
    result = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": PUBLIC, "messages": [{"role": "user", "content": "hi"}], "stream": True},
    )
    assert result.status_code == 200, result.text
    assert captured[0]["model"] == UPSTREAM
    payload = json.loads(next(line[6:] for line in result.text.splitlines() if line.startswith("data: {")))
    assert payload["model"] == PUBLIC
    assert payload["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] == arguments
    assert payload["choices"][0]["delta"]["content"] == TEXT
    assert "data: [DONE]" in result.text


@pytest.mark.asyncio
async def test_alias_is_one_hop_and_preserves_previous_response_id(async_client, source_upstream):
    captured = []

    async def handler(request):
        captured.append(await request.json())
        return web.json_response(_response())

    source_id = await _alias_source(async_client, await source_upstream(handler))
    source = (await async_client.get("/api/model-sources/")).json()["sources"][0]
    models = source["models"] + [{"model": UPSTREAM, "rawMetadataJson": json.dumps({"upstream_model": "third-hop"})}]
    assert (await async_client.patch(f"/api/model-sources/{source_id}", json={"models": models})).status_code == 200
    for extra in [{}, {"previous_response_id": "resp_alias"}]:
        result = await async_client.post(
            "/v1/responses", json={"model": PUBLIC, "input": "hi", "stream": False, **extra}
        )
        assert result.status_code == 200, result.text
        assert result.json()["model"] == PUBLIC
    assert [payload["model"] for payload in captured] == [UPSTREAM, UPSTREAM]
    assert captured[1]["previous_response_id"] == "resp_alias"


@pytest.mark.asyncio
async def test_alias_upstream_error_envelope_is_preserved(async_client, source_upstream):
    error = {
        "error": {"type": "invalid_request_error", "code": "model_not_found", "message": f"Unknown model {UPSTREAM}"}
    }

    async def handler(request):
        assert (await request.json())["model"] == UPSTREAM
        return web.json_response(error, status=404)

    await _alias_source(async_client, await source_upstream(handler))
    result = await async_client.post("/v1/responses", json={"model": PUBLIC, "input": "hi", "stream": False})
    assert result.status_code == 404
    assert result.json() == error
