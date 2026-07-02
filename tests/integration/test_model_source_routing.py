from __future__ import annotations

import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeAlias

import pytest
from aiohttp import web
from sqlalchemy import select

from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _create_model_source(
    async_client,
    *,
    name: str,
    model: str,
    base_url: str,
    input_per_1m: float | None = None,
    cached_input_per_1m: float | None = None,
    output_per_1m: float | None = None,
) -> str:
    model_entry: dict[str, object] = {
        "model": model,
        "displayName": model,
        "contextWindow": 8192,
        "maxOutputTokens": 1024,
        "supportsStreaming": True,
        "supportsTools": True,
    }
    if input_per_1m is not None:
        model_entry["inputPer1M"] = input_per_1m
    if cached_input_per_1m is not None:
        model_entry["cachedInputPer1M"] = cached_input_per_1m
    if output_per_1m is not None:
        model_entry["outputPer1M"] = output_per_1m
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": name,
            "baseUrl": base_url,
            "apiKey": f"token-{name}",
            "supportsChatCompletions": True,
            "supportsResponses": False,
            "models": [model_entry],
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


async def _enable_api_key_auth(async_client) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert response.status_code == 200


_UpstreamHandler: TypeAlias = Callable[[web.Request], Awaitable[web.StreamResponse]]


@pytest.fixture
async def source_upstream() -> AsyncIterator[Callable[[_UpstreamHandler], Awaitable[str]]]:
    runners: list[web.AppRunner] = []

    async def start(handler: _UpstreamHandler) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        port = _free_port()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        runners.append(runner)
        return f"http://127.0.0.1:{port}/v1"

    yield start

    for runner in runners:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_source_stream_upstream_error_maps_to_error_response(async_client, source_upstream):
    async def unauthorized(_request: web.Request) -> web.Response:
        return web.json_response(
            {"error": {"message": "bad key", "type": "invalid_request_error", "code": "invalid_api_key"}},
            status=401,
        )

    base_url = await source_upstream(unauthorized)
    model = "source-stream-error-model"
    await _create_model_source(async_client, name="stream-error", model=model, base_url=base_url)

    response = await async_client.post(
        "/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_source_unreachable_returns_error_envelope_and_releases_reservation(async_client):
    await _enable_api_key_auth(async_client)
    model = "source-unreachable-model"
    closed_port = _free_port()
    source_id = await _create_model_source(
        async_client,
        name="unreachable",
        model=model,
        base_url=f"http://127.0.0.1:{closed_port}/v1",
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "unreachable-source-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_source_unreachable"

    async with SessionLocal() as session:
        result = await session.execute(
            select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.status == "reserved")
        )
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_patch_model_source_returns_updated_model_list(async_client):
    source_id = await _create_model_source(
        async_client,
        name="patchable",
        model="old-model",
        base_url="http://127.0.0.1:9/v1",
    )

    response = await async_client.patch(
        f"/api/model-sources/{source_id}",
        json={
            "models": [
                {
                    "model": "new-model",
                    "displayName": "new-model",
                    "supportsStreaming": True,
                    "supportsTools": False,
                }
            ]
        },
    )

    assert response.status_code == 200
    assert [entry["model"] for entry in response.json()["models"]] == ["new-model"]

    listed = await async_client.get("/api/model-sources/")
    assert listed.status_code == 200
    listed_source = next(row for row in listed.json()["sources"] if row["id"] == source_id)
    assert [entry["model"] for entry in listed_source["models"]] == ["new-model"]


@pytest.mark.asyncio
async def test_source_usage_settles_cost_from_source_pricing(async_client, source_upstream):
    await _enable_api_key_auth(async_client)

    async def completion(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": "chatcmpl_priced",
                "object": "chat.completion",
                "created": 1,
                "model": "priced-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1_000,
                    "completion_tokens": 500,
                    "total_tokens": 1_500,
                    "prompt_tokens_details": {"cached_tokens": 200},
                },
            }
        )

    base_url = await source_upstream(completion)
    model = "priced-model"
    source_id = await _create_model_source(
        async_client,
        name="priced",
        model=model,
        base_url=base_url,
        input_per_1m=2.0,
        cached_input_per_1m=1.0,
        output_per_1m=10.0,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "priced-source-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "cost_usd", "limitWindow": "weekly", "maxValue": 1_000_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]
    key_id = created.json()["id"]

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200

    # billable input 800 @ $2/1M + cached 200 @ $1/1M + output 500 @ $10/1M
    expected_cost_usd = 0.0068
    expected_microdollars = 6_800

    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == expected_microdollars

        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        latest_log = result.scalars().first()
        assert latest_log is not None
        assert latest_log.model_source_id == source_id
        assert latest_log.cost_usd == pytest.approx(expected_cost_usd)


@pytest.mark.asyncio
async def test_limited_key_settles_usage_from_crlf_stream(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    frames = (
        b'data: {"id":"chatcmpl_crlf","object":"chat.completion.chunk","choices":'
        b'[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\r\n\r\n'
        b'data: {"id":"chatcmpl_crlf","object":"chat.completion.chunk","choices":[],'
        b'"usage":{"prompt_tokens":9,"completion_tokens":6,"total_tokens":15}}\r\n\r\n'
        b"data: [DONE]\r\n\r\n"
    )

    async def stream_handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(frames)
        await response.write_eof()
        return response

    base_url = await source_upstream(stream_handler)
    model = "source-crlf-model"
    source_id = await _create_model_source(async_client, name="crlf", model=model, base_url=base_url)
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "crlf-source-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]
    key_id = created.json()["id"]

    async with async_client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        _ = b"".join([chunk async for chunk in response.aiter_bytes()])

    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 15


@pytest.mark.asyncio
async def test_source_stream_success_passes_through_sse(async_client, source_upstream):
    frames = (
        b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","choices":'
        b'[{"index":0,"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
        b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","choices":[],'
        b'"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
        b"data: [DONE]\n\n"
    )

    async def stream_handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        await response.prepare(request)
        await response.write(frames)
        await response.write_eof()
        return response

    base_url = await source_upstream(stream_handler)
    model = "source-stream-ok-model"
    await _create_model_source(async_client, name="stream-ok", model=model, base_url=base_url)

    async with async_client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        received = b"".join([chunk async for chunk in response.aiter_bytes()])

    assert b'"content":"hello"' in received
    assert b"[DONE]" in received
