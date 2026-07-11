from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import TypeAlias, cast

import pytest
from aiohttp import web
from aiohttp.multipart import BodyPartReader
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyData, ApiKeysService, ApiKeyUsageReservationData

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
    audio_per_minute: float | None = None,
    raw_metadata_json: str | None = None,
    supports_responses: bool = False,
    supports_streaming: bool = True,
    supports_audio_transcriptions: bool = False,
) -> str:
    model_entry: dict[str, object] = {
        "model": model,
        "displayName": model,
        "contextWindow": 8192,
        "maxOutputTokens": 1024,
        "supportsStreaming": supports_streaming,
        "supportsTools": True,
    }
    if raw_metadata_json is not None:
        model_entry["rawMetadataJson"] = raw_metadata_json
    if input_per_1m is not None:
        model_entry["inputPer1M"] = input_per_1m
    if cached_input_per_1m is not None:
        model_entry["cachedInputPer1M"] = cached_input_per_1m
    if output_per_1m is not None:
        model_entry["outputPer1M"] = output_per_1m
    if audio_per_minute is not None:
        model_entry["audioPerMinute"] = audio_per_minute
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": name,
            "baseUrl": base_url,
            "apiKey": f"token-{name}",
            "supportsChatCompletions": True,
            "supportsResponses": supports_responses,
            "supportsAudioTranscriptions": supports_audio_transcriptions,
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
async def test_source_audio_transcription_routes_multipart_and_settles_usage(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def transcribe(request: web.Request) -> web.Response:
        captured["path"] = request.path
        captured["authorization"] = request.headers.get("authorization")
        reader = await request.multipart()
        fields: dict[str, list[str]] = {}
        while True:
            next_part = await reader.next()
            if next_part is None:
                break
            part = cast(BodyPartReader, next_part)
            if part.filename:
                captured["filename"] = part.filename
                captured["file_bytes"] = await part.read()
                captured["file_content_type"] = part.headers.get("Content-Type")
                continue
            if part.name is not None:
                fields.setdefault(part.name, []).append(await part.text())
        captured["fields"] = fields
        return web.json_response(
            {
                "text": "hello from source asr",
                "usage": {
                    "prompt_tokens": 37,
                    "completion_tokens": 0,
                    "total_tokens": 37,
                },
            }
        )

    base_url = await source_upstream(transcribe)
    model = "whisper-large-v3"
    source_id = await _create_model_source(
        async_client,
        name="asr",
        model=model,
        base_url=base_url,
        input_per_1m=3.0,
        supports_audio_transcriptions=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "asr-source-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    response = await async_client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        data={"model": model, "prompt": "domain words", "response_format": "json"},
        files={"file": ("sample.wav", b"\x01\x02\x03", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "hello from source asr"
    assert captured["path"] == "/v1/audio/transcriptions"
    assert captured["authorization"] == "Bearer token-asr"
    assert captured["filename"] == "sample.wav"
    assert captured["file_bytes"] == b"\x01\x02\x03"
    assert captured["file_content_type"] == "audio/wav"
    assert captured["fields"] == {
        "model": [model],
        "prompt": ["domain words"],
        "response_format": ["json"],
    }

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.model == model))
        log = result.scalar_one()
        assert log.account_id is None
        assert log.model_source_id == source_id
        assert log.source == "model_source"
        assert log.input_tokens == 37
        assert log.output_tokens == 0
        assert log.status == "success"


@pytest.mark.asyncio
async def test_source_audio_transcription_bills_by_duration(async_client, source_upstream):
    await _enable_api_key_auth(async_client)

    async def transcribe(_request: web.Request) -> web.Response:
        # No token usage, only a duration — the duration-priced path must settle cost.
        return web.json_response({"text": "labas", "duration": 120.0})

    base_url = await source_upstream(transcribe)
    model = "whisper-duration"
    source_id = await _create_model_source(
        async_client,
        name="asr-duration",
        model=model,
        base_url=base_url,
        supports_audio_transcriptions=True,
        audio_per_minute=0.30,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "asr-cost-key",
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
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        data={"model": model},
        files={"file": ("sample.wav", b"\x01\x02", "audio/wav")},
    )
    assert response.status_code == 200

    # 120s == 2 min @ $0.30/min == $0.60 == 600_000 microdollars
    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 600_000

        result = await session.execute(select(RequestLog).where(RequestLog.model == model))
        log = result.scalar_one()
        assert log.input_tokens == 0
        assert log.output_tokens == 0
        assert log.cost_usd == pytest.approx(0.60)
        assert log.status == "success"


@pytest.mark.asyncio
async def test_source_audio_transcription_text_response_passes_through(async_client, source_upstream):
    async def transcribe_text(_request: web.Request) -> web.Response:
        return web.Response(status=200, text="hello plain text", content_type="text/plain")

    base_url = await source_upstream(transcribe_text)
    model = "whisper-text"
    await _create_model_source(
        async_client,
        name="asr-text",
        model=model,
        base_url=base_url,
        supports_audio_transcriptions=True,
    )

    response = await async_client.post(
        "/v1/audio/transcriptions",
        data={"model": model, "response_format": "text"},
        files={"file": ("sample.wav", b"\x01\x02", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "hello plain text"


@pytest.mark.asyncio
async def test_source_audio_transcription_without_usage_fails_closed_for_limited_key(async_client, source_upstream):
    await _enable_api_key_auth(async_client)

    async def transcribe_no_usage(_request: web.Request) -> web.Response:
        return web.json_response({"text": "no usage here"})

    base_url = await source_upstream(transcribe_no_usage)
    model = "whisper-no-usage"
    source_id = await _create_model_source(
        async_client,
        name="asr-no-usage",
        model=model,
        base_url=base_url,
        supports_audio_transcriptions=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "asr-limited-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    response = await async_client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        data={"model": model},
        files={"file": ("sample.wav", b"\x01\x02", "audio/wav")},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "usage_unavailable"

    async with SessionLocal() as session:
        result = await session.execute(
            select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.status == "reserved")
        )
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_source_audio_transcription_raw_alias_lookup_requires_exact_allowlist(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    called = False

    async def transcribe(_request: web.Request) -> web.Response:
        nonlocal called
        called = True
        return web.json_response({"text": "should not route"})

    base_url = await source_upstream(transcribe)
    source_model = "gpt-5-high"
    source_id = await _create_model_source(
        async_client,
        name="asr-alias",
        model=source_model,
        base_url=base_url,
        supports_audio_transcriptions=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "asr-alias-key",
            "assignedSourceIds": [source_id],
            "allowedModels": ["gpt-5"],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    response = await async_client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        data={"model": source_model},
        files={"file": ("sample.wav", b"\x01\x02", "audio/wav")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request_error"
    assert called is False


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
async def test_responses_source_selector_can_require_streaming(async_client):
    from app.modules.model_sources.repository import ModelSourcesRepository

    model = "responses-non-streaming-model"
    await _create_model_source(
        async_client,
        name="responses-non-streaming",
        model=model,
        base_url="http://127.0.0.1:9/v1",
        supports_responses=True,
        supports_streaming=False,
    )

    async with SessionLocal() as session:
        repo = ModelSourcesRepository(session)
        non_streaming = await repo.find_responses_source_for_model(model)
        streaming = await repo.find_responses_source_for_model(model, require_streaming=True)

    assert non_streaming is not None
    assert streaming is None


@pytest.mark.asyncio
async def test_responses_source_raw_alias_lookup_requires_exact_allowlist(async_client):
    import app.modules.proxy.api as proxy_api

    model = "gpt-5-high"
    await _create_model_source(
        async_client,
        name="responses-alias-like-allowlist-source",
        model=model,
        base_url="http://127.0.0.1:9/v1",
        supports_responses=True,
    )
    canonical_only_key = ApiKeyData(
        id="key_responses_canonical_only",
        name="responses canonical only",
        key_prefix="sk-test-resp-canonical",
        allowed_models=["gpt-5"],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    exact_key = ApiKeyData(
        id="key_responses_exact_alias",
        name="responses exact alias",
        key_prefix="sk-test-resp-exact",
        allowed_models=[model],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    canonical_selection = await proxy_api._select_responses_model_source(
        "gpt-5",
        canonical_only_key,
        raw_model=model,
    )
    exact_selection = await proxy_api._select_responses_model_source(
        "gpt-5",
        exact_key,
        raw_model=model,
    )

    assert canonical_selection is None
    assert exact_selection is not None
    source, selected_model = exact_selection
    assert source.name == "responses-alias-like-allowlist-source"
    assert selected_model == model


@pytest.mark.asyncio
async def test_chat_source_selector_can_require_streaming(async_client):
    from app.modules.model_sources.repository import ModelSourcesRepository

    model = "chat-non-streaming-model"
    await _create_model_source(
        async_client,
        name="chat-non-streaming",
        model=model,
        base_url="http://127.0.0.1:9/v1",
        supports_streaming=False,
    )

    async with SessionLocal() as session:
        repo = ModelSourcesRepository(session)
        non_streaming = await repo.find_chat_source_for_model(model)
        streaming = await repo.find_chat_source_for_model(model, require_streaming=True)

    assert non_streaming is not None
    assert streaming is None


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
async def test_unpriced_source_usage_settles_zero_cost_for_priced_slug(async_client, source_upstream):
    await _enable_api_key_auth(async_client)

    async def completion(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": "chatcmpl_unpriced",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-5.2",
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
                },
            }
        )

    base_url = await source_upstream(completion)
    source_id = await _create_model_source(
        async_client,
        name="unpriced",
        model="gpt-5.2",
        base_url=base_url,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "unpriced-source-key",
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
        json={"model": "gpt-5.2", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 0

        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        latest_log = result.scalars().first()
        assert latest_log is not None
        assert latest_log.model_source_id == source_id
        assert latest_log.cost_usd == 0.0


@pytest.mark.asyncio
async def test_settlement_failure_releases_reservation(async_client, source_upstream, monkeypatch):
    await _enable_api_key_auth(async_client)

    async def completion(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": "chatcmpl_settle_fail",
                "object": "chat.completion",
                "created": 1,
                "model": "settle-fail-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }
        )

    base_url = await source_upstream(completion)
    model = "settle-fail-model"
    source_id = await _create_model_source(async_client, name="settle-fail", model=model, base_url=base_url)
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "settle-fail-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    async def broken_finalize(self, reservation_id, **kwargs):
        raise RuntimeError("settlement boom")

    monkeypatch.setattr(ApiKeysService, "finalize_usage_reservation", broken_finalize)

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "usage_settlement_failed"

    async with SessionLocal() as session:
        result = await session.execute(
            select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.status == "reserved")
        )
        assert result.scalars().all() == []


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
async def test_source_invalid_json_2xx_maps_to_error_response(async_client, source_upstream):
    async def html_response(_request: web.Request) -> web.Response:
        return web.Response(status=200, text="<html>gateway page</html>", content_type="text/html")

    base_url = await source_upstream(html_response)
    model = "source-invalid-json-model"
    await _create_model_source(async_client, name="invalid-json", model=model, base_url=base_url)

    response = await async_client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_upstream_response"


@pytest.mark.asyncio
async def test_cancelled_buffered_stream_releases_reservation(async_client, monkeypatch):
    import asyncio

    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceUsageHolder

    released: list[object] = []
    stream_closed = False

    async def record_release(reservation: object) -> None:
        released.append(reservation)

    monkeypatch.setattr(proxy_api, "_release_reservation", record_release)

    async def cancelled_stream() -> AsyncIterator[bytes]:
        nonlocal stream_closed
        try:
            yield b"data: partial\n\n"
            raise asyncio.CancelledError()
        finally:
            stream_closed = True

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
        }
    )
    source = ModelSource(
        id="src_cancelled",
        name="cancelled",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_cancelled",
        key_id="key_cancelled",
        model="cancelled-model",
    )

    with pytest.raises(asyncio.CancelledError):
        await proxy_api._buffered_limited_source_chat_stream_response(
            request,
            source=source,
            api_key=None,
            model="cancelled-model",
            reservation=reservation,
            stream=cancelled_stream(),
            usage_holder=SourceUsageHolder(),
            rate_limit_headers={},
        )

    assert released == [reservation]
    assert stream_closed is True


@pytest.mark.asyncio
async def test_downstream_disconnect_closes_source_stream(async_client, monkeypatch):
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceUsageHolder

    released: list[object] = []
    stream_closed = False

    async def record_release(reservation: object) -> None:
        released.append(reservation)

    async def skip_log(*args, **kwargs) -> None:
        del args, kwargs

    monkeypatch.setattr(proxy_api, "_release_reservation", record_release)
    monkeypatch.setattr(proxy_api, "_log_source_chat_completion", skip_log)

    async def source_stream() -> AsyncIterator[bytes]:
        nonlocal stream_closed
        try:
            yield b"data: partial\n\n"
            await asyncio.sleep(60)
        finally:
            stream_closed = True

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
        }
    )
    source = ModelSource(
        id="src_disconnect",
        name="disconnect",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_disconnect",
        key_id="key_disconnect",
        model="disconnect-model",
    )
    response_stream = cast(
        AsyncGenerator[bytes, None],
        proxy_api._source_chat_stream_with_settlement(
            source_stream(),
            usage_holder=SourceUsageHolder(),
            request=request,
            source=source,
            api_key=None,
            model="disconnect-model",
            reservation=reservation,
        ),
    )

    assert await anext(response_stream) == b"data: partial\n\n"
    await response_stream.aclose()

    assert released == [reservation]
    assert stream_closed is True


@pytest.mark.asyncio
async def test_opportunistic_key_routes_to_source_without_account_pool(async_client, source_upstream):
    await _enable_api_key_auth(async_client)

    async def completion(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": "chatcmpl_opportunistic",
                "object": "chat.completion",
                "created": 1,
                "model": "opportunistic-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    base_url = await source_upstream(completion)
    model = "opportunistic-model"
    source_id = await _create_model_source(async_client, name="opportunistic", model=model, base_url=base_url)
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "opportunistic-source-key",
            "assignedSourceIds": [source_id],
            "trafficClass": "opportunistic",
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    # No subscription accounts exist, so opportunistic admission would deny
    # with 429 if it (incorrectly) gated the account-free source path.
    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "chatcmpl_opportunistic"


@pytest.mark.asyncio
async def test_source_credential_decrypt_failure_maps_to_error_and_releases_reservation(
    async_client, source_upstream, monkeypatch
):
    await _enable_api_key_auth(async_client)

    async def completion(_request: web.Request) -> web.Response:
        return web.json_response({"unreachable": True})

    base_url = await source_upstream(completion)
    model = "credential-fail-model"
    source_id = await _create_model_source(async_client, name="credential-fail", model=model, base_url=base_url)
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "credential-fail-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    from app.core.crypto import TokenEncryptor

    def broken_decrypt(self, value):
        raise ValueError("decryption boom")

    monkeypatch.setattr(TokenEncryptor, "decrypt", broken_decrypt)

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_source_credentials_error"

    async with SessionLocal() as session:
        result = await session.execute(
            select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.status == "reserved")
        )
        assert result.scalars().all() == []


def _chat_completion_body(model: str) -> dict[str, object]:
    return {
        "id": "chatcmpl_sanitized",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "4"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


@pytest.mark.asyncio
async def test_source_chat_payload_drops_empty_tools_and_reasoning_toggles(async_client, source_upstream):
    captured: dict[str, object] = {}

    async def capture(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(_chat_completion_body("sanitized-model"))

    base_url = await source_upstream(capture)
    model = "sanitized-model"
    await _create_model_source(async_client, name="sanitized", model=model, base_url=base_url)

    response = await async_client.post(
        "/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Kiek yra 2+2?"}],
            "tools": [],
            "tool_choice": "none",
            "include_reasoning": True,
            "separate_reasoning": True,
            "stream_reasoning": True,
            "reasoning_effort": "low",
            "max_tokens": 200,
        },
    )

    assert response.status_code == 200
    assert captured["model"] == model
    assert captured["max_tokens"] == 200
    for key in (
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "include_reasoning",
        "separate_reasoning",
        "stream_reasoning",
        "reasoning",
        "reasoning_effort",
    ):
        assert key not in captured


@pytest.mark.asyncio
async def test_source_chat_payload_enforced_reasoning_stays_stripped_for_plain_model(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def capture(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(_chat_completion_body("plain-enforced-model"))

    base_url = await source_upstream(capture)
    model = "plain-enforced-model"
    source_id = await _create_model_source(
        async_client,
        name="plain-enforced",
        model=model,
        base_url=base_url,
    )
    key_response = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "plain enforced source key",
            "enforcedReasoningEffort": "high",
            "sourceAssignmentScopeEnabled": True,
            "assignedSourceIds": [source_id],
        },
    )
    assert key_response.status_code == 200
    key = key_response.json()["key"]

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert "reasoning" not in captured
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_source_chat_without_usage_ignores_limits_for_other_models(async_client, source_upstream):
    await _enable_api_key_auth(async_client)

    async def completion_without_usage(_request: web.Request) -> web.Response:
        body = _chat_completion_body("source-unlimited-by-filter")
        body.pop("usage", None)
        return web.json_response(body)

    base_url = await source_upstream(completion_without_usage)
    model = "source-unlimited-by-filter"
    source_id = await _create_model_source(
        async_client,
        name="source-unlimited-by-filter",
        model=model,
        base_url=base_url,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source limit for other model",
            "assignedSourceIds": [source_id],
            "limits": [
                {
                    "limitType": "total_tokens",
                    "limitWindow": "weekly",
                    "maxValue": 5,
                    "modelFilter": "some-other-model",
                },
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

    assert response.status_code == 200
    assert response.json()["id"] == "chatcmpl_sanitized"

    async with SessionLocal() as session:
        result = await session.execute(
            select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.status == "reserved")
        )
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_source_chat_prefers_raw_alias_like_model_slug(async_client, source_upstream):
    captured: dict[str, object] = {}

    async def capture(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(_chat_completion_body("gpt-5-high"))

    base_url = await source_upstream(capture)
    model = "gpt-5-high"
    await _create_model_source(
        async_client,
        name="alias-like-source",
        model=model,
        base_url=base_url,
    )

    response = await async_client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert captured["model"] == model
    assert response.json()["model"] == model


@pytest.mark.asyncio
async def test_source_chat_raw_alias_lookup_requires_exact_allowlist(async_client):
    import app.modules.proxy.api as proxy_api

    model = "gpt-5-high"
    await _create_model_source(
        async_client,
        name="alias-like-allowlist-source",
        model=model,
        base_url="http://127.0.0.1:9/v1",
    )
    canonical_only_key = ApiKeyData(
        id="key_canonical_only",
        name="canonical only",
        key_prefix="sk-test-canonical",
        allowed_models=["gpt-5"],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    exact_key = ApiKeyData(
        id="key_exact_alias",
        name="exact alias",
        key_prefix="sk-test-exact",
        allowed_models=[model],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    canonical_selection = await proxy_api._select_chat_model_source(
        "gpt-5",
        canonical_only_key,
        raw_model=model,
    )
    exact_selection = await proxy_api._select_chat_model_source(
        "gpt-5",
        exact_key,
        raw_model=model,
    )

    assert canonical_selection is None
    assert exact_selection is not None
    source, selected_model = exact_selection
    assert source.name == "alias-like-allowlist-source"
    assert selected_model == model


@pytest.mark.asyncio
async def test_v1_models_metadata_reflects_reasoning_optin(async_client):
    await _create_model_source(
        async_client,
        name="reasoning-metadata",
        model="reasoning-metadata-model",
        base_url="http://127.0.0.1:9/v1",
        raw_metadata_json='{"supports_reasoning": true}',
    )
    await _create_model_source(
        async_client,
        name="plain-metadata",
        model="plain-metadata-model",
        base_url="http://127.0.0.1:9/v1",
    )

    response = await async_client.get("/v1/models")
    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()["data"]}

    assert by_id["reasoning-metadata-model"]["supports_reasoning"] is True
    assert by_id["plain-metadata-model"]["supports_reasoning"] is False


@pytest.mark.asyncio
async def test_source_chat_payload_keeps_reasoning_toggles_for_optin_model(async_client, source_upstream):
    captured: dict[str, object] = {}

    async def capture(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(_chat_completion_body("reasoning-model"))

    base_url = await source_upstream(capture)
    model = "reasoning-model"
    await _create_model_source(
        async_client,
        name="reasoning-optin",
        model=model,
        base_url=base_url,
        raw_metadata_json='{"supports_reasoning": true}',
    )

    response = await async_client.post(
        "/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "include_reasoning": True,
            "reasoning_effort": "high",
        },
    )

    assert response.status_code == 200
    assert captured["include_reasoning"] is True
    assert captured["reasoning_effort"] == "high"
    assert "tools" not in captured


@pytest.mark.asyncio
async def test_source_chat_payload_overrides_enforced_reasoning_object(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def capture(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(_chat_completion_body("reasoning-enforced-model"))

    base_url = await source_upstream(capture)
    model = "reasoning-enforced-model"
    source_id = await _create_model_source(
        async_client,
        name="reasoning-enforced",
        model=model,
        base_url=base_url,
        raw_metadata_json='{"supports_reasoning": true}',
    )
    key_response = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "reasoning enforced source key",
            "enforcedReasoningEffort": "high",
            "sourceAssignmentScopeEnabled": True,
            "assignedSourceIds": [source_id],
        },
    )
    assert key_response.status_code == 200
    key = key_response.json()["key"]

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning": {"effort": "low", "summary": "auto"},
            "reasoning_effort": "low",
        },
    )

    assert response.status_code == 200
    assert captured["reasoning"] == {"effort": "high", "summary": "auto"}
    assert captured["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_dashboard_models_endpoint_lists_source_models(async_client):
    model = "picker-source-model"
    await _create_model_source(
        async_client,
        name="picker",
        model=model,
        base_url="http://127.0.0.1:9/v1",
    )

    response = await async_client.get("/api/models")
    assert response.status_code == 200
    models = response.json()["models"]
    ids = [entry["id"] for entry in models]
    assert model in ids
    assert ids.count(model) == 1
    source_entry = next(entry for entry in models if entry["id"] == model)
    assert source_entry["sourceOnly"] is True


@pytest.mark.asyncio
async def test_allowlisted_source_model_routes_through(async_client, source_upstream):
    await _enable_api_key_auth(async_client)

    async def completion(_request: web.Request) -> web.Response:
        return web.json_response(_chat_completion_body("allowlisted-model"))

    base_url = await source_upstream(completion)
    model = "allowlisted-model"
    source_id = await _create_model_source(async_client, name="allowlisted", model=model, base_url=base_url)
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "allowlisted-key",
            "assignedSourceIds": [source_id],
            "allowedModels": [model],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    allowed = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert allowed.status_code == 200

    denied = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "some-other-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_scoped_key_does_not_route_to_unassigned_source(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    unassigned_hits = 0

    async def unassigned_upstream(_request: web.Request) -> web.Response:
        nonlocal unassigned_hits
        unassigned_hits += 1
        return web.json_response(
            {
                "id": "chatcmpl_unassigned",
                "object": "chat.completion",
                "created": 1,
                "model": "unassigned-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "leak"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    assigned_source_id = await _create_model_source(
        async_client,
        name="assigned-scope",
        model="assigned-model",
        base_url=f"http://127.0.0.1:{_free_port()}/v1",
    )
    unassigned_base_url = await source_upstream(unassigned_upstream)
    await _create_model_source(
        async_client,
        name="unassigned-scope",
        model="unassigned-model",
        base_url=unassigned_base_url,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={"name": "scoped-key", "assignedSourceIds": [assigned_source_id]},
    )
    assert created.status_code == 200
    key = created.json()["key"]

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "unassigned-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code != 200
    assert unassigned_hits == 0


@pytest.mark.asyncio
async def test_buffer_limit_closes_abandoned_upstream_stream(async_client, monkeypatch):
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceUsageHolder

    closed = False

    async def big_stream() -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            while True:
                yield b"x" * 1024
        finally:
            closed = True

    monkeypatch.setattr(proxy_api, "_SOURCE_LIMITED_STREAM_BUFFER_BYTES", 4096)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
        }
    )
    source = ModelSource(
        id="src_buffer_limit",
        name="buffer-limit",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )

    response = await proxy_api._buffered_limited_source_chat_stream_response(
        request,
        source=source,
        api_key=None,
        model="buffer-limit-model",
        reservation=None,
        stream=big_stream(),
        usage_holder=SourceUsageHolder(),
        rate_limit_headers={},
    )

    assert response.status_code == 502
    assert closed is True


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
