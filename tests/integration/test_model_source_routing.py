from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from tempfile import SpooledTemporaryFile
from typing import TypeAlias, cast

import pytest
import starlette.formparsers as starlette_formparsers
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
    supports_embeddings: bool = False,
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
            "supportsEmbeddings": supports_embeddings,
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


def _record_multipart_spools(monkeypatch: pytest.MonkeyPatch) -> list[SpooledTemporaryFile[bytes]]:
    original = starlette_formparsers.SpooledTemporaryFile
    spools: list[SpooledTemporaryFile[bytes]] = []

    def create_spool(*, max_size: int) -> SpooledTemporaryFile[bytes]:
        spool = original(max_size=max_size)
        spools.append(spool)
        return spool

    monkeypatch.setattr(starlette_formparsers, "SpooledTemporaryFile", create_spool)
    return spools


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


def _embedding_success_response(model: str) -> web.Response:
    return web.json_response(
        {
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
            "model": model,
            "usage": {"prompt_tokens": 21, "total_tokens": 21},
        }
    )


@pytest.mark.asyncio
async def test_source_audio_transcription_routes_multipart_and_settles_usage(
    async_client,
    source_upstream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}
    spools: list[SpooledTemporaryFile[bytes]] = []

    async def transcribe(request: web.Request) -> web.Response:
        assert spools
        assert all(spool.closed for spool in spools)
        captured["path"] = request.path
        captured["authorization"] = request.headers.get("authorization")
        reader = await request.multipart()
        fields: dict[str, list[str]] = {}
        field_items: list[tuple[str, str]] = []
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
                value = await part.text()
                fields.setdefault(part.name, []).append(value)
                field_items.append((part.name, value))
        captured["fields"] = fields
        captured["field_items"] = field_items
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
    spools = _record_multipart_spools(monkeypatch)

    response = await async_client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        files=[
            ("model", (None, model)),
            ("prompt", (None, "first context")),
            ("response_format", (None, "json")),
            ("prompt", (None, "domain words")),
            ("file", ("sample.wav", b"\x01\x02\x03", "audio/wav")),
        ],
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
        "prompt": ["first context", "domain words"],
        "response_format": ["json"],
    }
    assert captured["field_items"] == [
        ("model", model),
        ("prompt", "first context"),
        ("response_format", "json"),
        ("prompt", "domain words"),
    ]

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
async def test_responses_model_is_source_owned_detects_streaming_source(async_client):
    from app.modules.model_sources.selection import responses_model_is_source_owned

    model = "ws-guard-streaming-model"
    await _create_model_source(
        async_client,
        name="ws-guard-streaming",
        model=model,
        base_url="http://127.0.0.1:9/v1",
        supports_responses=True,
        supports_streaming=True,
    )

    assert await responses_model_is_source_owned(model, None) is True
    # A subscription model must stay on the WebSocket path.
    assert await responses_model_is_source_owned("gpt-5.6-sol", None) is False
    assert await responses_model_is_source_owned(None, None) is False


@pytest.mark.asyncio
async def test_responses_model_is_source_owned_requires_streaming(async_client):
    """The guard mirrors the HTTP selector, which requires a streaming source."""
    from app.modules.model_sources.selection import responses_model_is_source_owned

    model = "ws-guard-non-streaming-model"
    await _create_model_source(
        async_client,
        name="ws-guard-non-streaming",
        model=model,
        base_url="http://127.0.0.1:9/v1",
        supports_responses=True,
        supports_streaming=False,
    )

    assert await responses_model_is_source_owned(model, None) is False


@pytest.mark.asyncio
async def test_responses_model_is_source_owned_honors_enforced_model(async_client):
    """An API key that forces a source-owned model must also be caught.

    The HTTP handlers build their candidate list from the enforced model as
    well as the requested one; the WebSocket guard has to match or an enforced
    source model would fall through to subscription-account selection.
    """
    from app.modules.model_sources.selection import responses_model_is_source_owned

    model = "ws-guard-enforced-model"
    await _create_model_source(
        async_client,
        name="ws-guard-enforced",
        model=model,
        base_url="http://127.0.0.1:9/v1",
        supports_responses=True,
        supports_streaming=True,
    )
    enforcing_key = ApiKeyData(
        id="key_ws_guard_enforced",
        name="ws guard enforced",
        key_prefix="sk-test-ws-enforced",
        allowed_models=[],
        enforced_model=model,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    # The client asked for a subscription model, but the key forces the source.
    assert await responses_model_is_source_owned("gpt-5.6-sol", enforcing_key) is True


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
async def test_responses_model_is_source_owned_prefers_the_raw_alias(async_client):
    """WebSocket parity for the raw-alias candidate (see the HTTP test above).

    Request preparation normalizes ``gpt-5-high`` to ``gpt-5`` before the
    WebSocket guards run, so the guard helper must accept the client's raw
    model and offer it to source selection ahead of the normalized one — the
    HTTP path routes the identical request via ``raw_source_model``.
    """
    from app.modules.model_sources.selection import responses_model_is_source_owned

    model = "gpt-5-high"
    await _create_model_source(
        async_client,
        name="ws-guard-raw-alias",
        model=model,
        base_url="http://127.0.0.1:9/v1",
        supports_responses=True,
        supports_streaming=True,
    )
    exact_key = ApiKeyData(
        id="key_ws_raw_alias",
        name="ws raw alias",
        key_prefix="sk-test-ws-raw-alias",
        allowed_models=[model],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    assert await responses_model_is_source_owned("gpt-5", exact_key, raw_model=model) is True
    # Without the raw candidate the exact allowlist hides the source. This is
    # the pre-fix WebSocket behaviour; keeping it false proves the assertion
    # above matched through the raw candidate, not some other fallback.
    assert await responses_model_is_source_owned("gpt-5", exact_key) is False


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
async def test_cancelled_buffered_stream_releases_reservation_when_close_fails(async_client, monkeypatch):
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceUsageHolder

    released: list[object] = []

    async def record_release(reservation: object) -> None:
        released.append(reservation)

    async def fail_close(_stream: object) -> None:
        raise RuntimeError("close failed")

    monkeypatch.setattr(proxy_api, "_release_reservation", record_release)
    monkeypatch.setattr(proxy_api, "_aclose_stream", fail_close)

    async def cancelled_stream() -> AsyncIterator[bytes]:
        yield b"data: partial\n\n"
        raise asyncio.CancelledError()

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
        id="src_cancelled_close_fails",
        name="cancelled-close-fails",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_cancelled_close_fails",
        key_id="key_cancelled_close_fails",
        model="cancelled-model",
    )

    with pytest.raises(RuntimeError, match="close failed"):
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


@pytest.mark.asyncio
async def test_cancelled_buffered_stream_finishes_usage_settlement(async_client, monkeypatch):
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceUsage, SourceUsageHolder

    settlement_started = asyncio.Event()
    settlement_can_finish = asyncio.Event()
    settled: list[object] = []
    logs: list[dict[str, object]] = []

    async def settle(reservation: object, **_kwargs: object) -> bool:
        settlement_started.set()
        await settlement_can_finish.wait()
        settled.append(reservation)
        return True

    async def record_log(*_args: object, **kwargs: object) -> None:
        logs.append(kwargs)

    monkeypatch.setattr(proxy_api, "_settle_source_reservation", settle)
    monkeypatch.setattr(proxy_api, "_log_source_chat_completion", record_log)

    async def complete_stream() -> AsyncIterator[bytes]:
        yield b"data: done\n\n"

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
        id="src_settlement_cancelled",
        name="settlement-cancelled",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_settlement_cancelled",
        key_id="key_settlement_cancelled",
        model="cancelled-model",
    )
    usage_holder = SourceUsageHolder(usage=SourceUsage(input_tokens=3, output_tokens=5))

    task = asyncio.create_task(
        proxy_api._buffered_limited_source_chat_stream_response(
            request,
            source=source,
            api_key=None,
            model="cancelled-model",
            reservation=reservation,
            stream=complete_stream(),
            usage_holder=usage_holder,
            rate_limit_headers={},
        )
    )
    await asyncio.wait_for(settlement_started.wait(), timeout=1)
    task.cancel()
    settlement_can_finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert settled == [reservation]
    assert logs[-1]["status"] == "cancelled"
    assert logs[-1]["error_code"] == "client_disconnected"
    assert logs[-1]["usage"] == usage_holder.usage


@pytest.mark.asyncio
async def test_cancelled_buffered_stream_logs_disconnect(async_client, monkeypatch):
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceUsageHolder

    released: list[object] = []
    logs: list[dict[str, object]] = []

    async def record_release(reservation: object) -> None:
        released.append(reservation)

    async def record_log(*_args: object, **kwargs: object) -> None:
        logs.append(kwargs)

    monkeypatch.setattr(proxy_api, "_release_reservation", record_release)
    monkeypatch.setattr(proxy_api, "_log_source_chat_completion", record_log)

    async def cancelled_stream() -> AsyncIterator[bytes]:
        yield b"data: partial\n\n"
        raise asyncio.CancelledError()

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
        id="src_buffered_cancelled_log",
        name="buffered-cancelled-log",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_buffered_cancelled_log",
        key_id="key_buffered_cancelled_log",
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
    assert logs[-1]["status"] == "cancelled"
    assert logs[-1]["error_code"] == "client_disconnected"
    assert logs[-1]["error_message"] == "client disconnected during source stream buffering"


@pytest.mark.asyncio
async def test_source_completion_success_log_finishes_after_cancellation(async_client, monkeypatch):
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.core.openai.chat_requests import ChatCompletionsRequest
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceChatCompletion, SourceUsage

    log_started = asyncio.Event()
    allow_log_finish = asyncio.Event()
    logs: list[dict[str, object]] = []

    async def fake_forward(*_args: object, **_kwargs: object) -> SourceChatCompletion:
        return SourceChatCompletion(
            payload={"id": "chatcmpl_cancelled_after_settlement"},
            usage=SourceUsage(input_tokens=3, output_tokens=5),
            timings=None,
            upstream_status_code=200,
        )

    async def settle(*_args: object, **_kwargs: object) -> bool:
        return True

    async def record_log(*_args: object, **kwargs: object) -> None:
        logs.append(kwargs)
        log_started.set()
        await allow_log_finish.wait()

    monkeypatch.setattr(proxy_api, "forward_chat_completion", fake_forward)
    monkeypatch.setattr(proxy_api, "_settle_source_reservation", settle)
    monkeypatch.setattr(proxy_api, "_log_source_chat_completion", record_log)

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
        id="src_completion_cancelled_log",
        name="completion-cancelled-log",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    payload = ChatCompletionsRequest.model_validate(
        {
            "model": "completion-cancelled-log",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        }
    )

    task = asyncio.create_task(
        proxy_api._source_chat_completion_response(
            request,
            payload,
            source=source,
            model="completion-cancelled-log",
            api_key=None,
            reservation=None,
            rate_limit_headers={},
        )
    )
    await asyncio.wait_for(log_started.wait(), timeout=1)
    task.cancel()
    allow_log_finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert logs[-1]["status"] == "success"
    assert logs[-1]["usage"] == SourceUsage(input_tokens=3, output_tokens=5)


@pytest.mark.asyncio
async def test_source_stream_setup_cancellation_logs_visible_error_even_if_release_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.core.openai.chat_requests import ChatCompletionsRequest
    from app.db.models import ModelSource

    logs: list[dict[str, object]] = []
    release_attempts: list[object] = []

    async def cancel_during_open(*_args: object, **_kwargs: object) -> object:
        raise asyncio.CancelledError

    async def fail_release(reservation: object) -> None:
        release_attempts.append(reservation)
        raise RuntimeError("sqlite busy")

    async def record_log(*_args: object, **kwargs: object) -> None:
        logs.append(dict(kwargs))

    monkeypatch.setattr(proxy_api, "stream_source_chat_completion", cancel_during_open)
    monkeypatch.setattr(proxy_api, "_release_reservation_deferring_cancellation", fail_release)
    monkeypatch.setattr(proxy_api, "_log_source_chat_completion", record_log)

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
        id="src_stream_setup_cancel",
        name="stream-setup-cancel",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_stream_setup_cancel",
        key_id="key_stream_setup_cancel",
        model="stream-setup-cancel",
    )
    payload = ChatCompletionsRequest.model_validate(
        {
            "model": "stream-setup-cancel",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
    )

    with pytest.raises(asyncio.CancelledError):
        await proxy_api._source_chat_completion_response(
            request,
            payload,
            source=source,
            model="stream-setup-cancel",
            api_key=None,
            reservation=reservation,
            rate_limit_headers={},
        )

    assert release_attempts == [reservation]
    assert logs == [
        {
            "source": source,
            "api_key": None,
            "model": "stream-setup-cancel",
            "status": "cancelled",
            "error_code": "client_disconnected",
            "error_message": "client disconnected during source stream setup",
        }
    ]


@pytest.mark.asyncio
async def test_source_request_setup_cancellation_logs_disconnect_even_if_release_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.core.openai.chat_requests import ChatCompletionsRequest
    from app.db.models import ModelSource

    logs: list[dict[str, object]] = []
    release_attempts: list[object] = []

    async def cancel_during_forward(*_args: object, **_kwargs: object) -> object:
        raise asyncio.CancelledError

    async def fail_release(reservation: object) -> None:
        release_attempts.append(reservation)
        raise RuntimeError("sqlite busy")

    async def record_log(*_args: object, **kwargs: object) -> None:
        logs.append(dict(kwargs))

    monkeypatch.setattr(proxy_api, "forward_chat_completion", cancel_during_forward)
    monkeypatch.setattr(proxy_api, "_release_reservation_deferring_cancellation", fail_release)
    monkeypatch.setattr(proxy_api, "_log_source_chat_completion", record_log)

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
        id="src_request_setup_cancel_release_fail",
        name="request-setup-cancel-release-fail",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_request_setup_cancel_release_fail",
        key_id="key_request_setup_cancel_release_fail",
        model="request-setup-cancel-release-fail",
    )
    payload = ChatCompletionsRequest.model_validate(
        {
            "model": "request-setup-cancel-release-fail",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        }
    )

    with pytest.raises(asyncio.CancelledError):
        await proxy_api._source_chat_completion_response(
            request,
            payload,
            source=source,
            model="request-setup-cancel-release-fail",
            api_key=None,
            reservation=reservation,
            rate_limit_headers={},
        )

    assert release_attempts == [reservation]
    assert logs == [
        {
            "source": source,
            "api_key": None,
            "model": "request-setup-cancel-release-fail",
            "status": "cancelled",
            "error_code": "client_disconnected",
            "error_message": "client disconnected during source request setup",
        }
    ]


@pytest.mark.asyncio
async def test_buffered_stream_cancellation_logs_disconnect_even_if_release_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceUsageHolder

    logs: list[dict[str, object]] = []
    release_attempts: list[object] = []

    async def fail_release(reservation: object) -> None:
        release_attempts.append(reservation)
        raise RuntimeError("sqlite busy")

    async def record_log(*_args: object, **kwargs: object) -> None:
        logs.append(dict(kwargs))

    monkeypatch.setattr(proxy_api, "_release_reservation_deferring_cancellation", fail_release)
    monkeypatch.setattr(proxy_api, "_log_source_chat_completion", record_log)

    async def cancelled_stream() -> AsyncIterator[bytes]:
        yield b"data: partial\n\n"
        raise asyncio.CancelledError()

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
        id="src_buffered_cancel_release_fail",
        name="buffered-cancel-release-fail",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_buffered_cancel_release_fail",
        key_id="key_buffered_cancel_release_fail",
        model="buffered-cancel-release-fail",
    )

    with pytest.raises(asyncio.CancelledError):
        await proxy_api._buffered_limited_source_chat_stream_response(
            request,
            source=source,
            api_key=None,
            model="buffered-cancel-release-fail",
            reservation=reservation,
            stream=cancelled_stream(),
            usage_holder=SourceUsageHolder(),
            rate_limit_headers={},
        )

    assert release_attempts == [reservation]
    assert logs[-1]["status"] == "cancelled"
    assert logs[-1]["error_code"] == "client_disconnected"
    assert logs[-1]["error_message"] == "client disconnected during source stream buffering"


@pytest.mark.asyncio
async def test_source_stream_body_teardown_survives_repeated_cancellation(monkeypatch: pytest.MonkeyPatch):
    from contextlib import AsyncExitStack

    import app.modules.model_sources.forwarding as forwarding_module
    from app.db.models import ModelSource

    stream_blocked = asyncio.Event()
    release_started = asyncio.Event()
    allow_release = asyncio.Event()
    release_finished = asyncio.Event()

    class _SlowLease:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
            release_started.set()
            await allow_release.wait()
            release_finished.set()
            return False

    stack = AsyncExitStack()
    await stack.enter_async_context(_SlowLease())

    class _FakeContent:
        def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
            async def gen() -> AsyncIterator[bytes]:
                yield b"data: chunk\n\n"
                stream_blocked.set()
                await asyncio.Event().wait()

            return gen()

    class _FakeResponse:
        status = 200
        content = _FakeContent()

    async def fake_open(*_args: object, **_kwargs: object) -> object:
        return stack, _FakeResponse()

    monkeypatch.setattr(forwarding_module, "_open_source_stream", fake_open)

    source = ModelSource(
        id="src_body_teardown_repeated_cancel",
        name="body-teardown-repeated-cancel",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    stream = await forwarding_module.stream_chat_completion(source, {"model": "body-teardown"})

    async def consume() -> None:
        async for _chunk in stream.body:
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(stream_blocked.wait(), timeout=1)
    await asyncio.sleep(0)

    task.cancel()
    await asyncio.wait_for(release_started.wait(), timeout=1)
    # Second cancellation delivery while the exit stack is unwinding: teardown
    # must still return the pooled HTTP lease.
    task.cancel()
    await asyncio.sleep(0)
    allow_release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert release_finished.is_set()


@pytest.mark.asyncio
async def test_open_source_stream_cleanup_finishes_after_cancellation(monkeypatch: pytest.MonkeyPatch):
    import app.modules.model_sources.forwarding as forwarding_module
    from app.db.models import ModelSource

    cleanup_started = asyncio.Event()
    allow_cleanup_finish = asyncio.Event()
    cleanup_finished = asyncio.Event()

    class _FailingPostContext:
        async def __aenter__(self):
            raise asyncio.CancelledError()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

    class _Session:
        def post(self, *_args: object, **_kwargs: object) -> _FailingPostContext:
            return _FailingPostContext()

    class _SessionLease:
        async def __aenter__(self) -> _Session:
            return _Session()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            cleanup_started.set()
            await allow_cleanup_finish.wait()
            cleanup_finished.set()
            return False

    monkeypatch.setattr(forwarding_module, "lease_http_session", lambda: _SessionLease())

    source = ModelSource(
        id="src_open_cancelled_cleanup",
        name="open-cancelled-cleanup",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )

    task = asyncio.create_task(
        forwarding_module._open_source_stream(
            source,
            "/chat/completions",
            {"model": "open-cancelled-cleanup"},
            encryptor=None,
        )
    )
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    task.cancel()
    allow_cleanup_finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert cleanup_finished.is_set() is True


@pytest.mark.asyncio
async def test_forward_chat_completion_cleanup_finishes_after_cancellation(monkeypatch: pytest.MonkeyPatch):
    import app.modules.model_sources.forwarding as forwarding_module
    from app.db.models import ModelSource

    cleanup_started = asyncio.Event()
    allow_cleanup_finish = asyncio.Event()
    cleanup_finished = asyncio.Event()

    class _Response:
        status = 200

        async def json(self, content_type=None):
            del content_type
            return {
                "id": "chatcmpl_forward_cancelled_cleanup",
                "usage": {"prompt_tokens": 3, "completion_tokens": 5},
            }

    class _PostContext:
        async def __aenter__(self) -> _Response:
            return _Response()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

    class _Session:
        def post(self, *_args: object, **_kwargs: object) -> _PostContext:
            return _PostContext()

    class _SessionLease:
        async def __aenter__(self) -> _Session:
            return _Session()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            cleanup_started.set()
            await allow_cleanup_finish.wait()
            cleanup_finished.set()
            return False

    monkeypatch.setattr(forwarding_module, "lease_http_session", lambda: _SessionLease())

    source = ModelSource(
        id="src_forward_cancelled_cleanup",
        name="forward-cancelled-cleanup",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )

    task = asyncio.create_task(
        forwarding_module.forward_chat_completion(
            source,
            {"model": "forward-cancelled-cleanup", "messages": [{"role": "user", "content": "hello"}]},
        )
    )
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    task.cancel()
    allow_cleanup_finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert cleanup_finished.is_set() is True


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
async def test_source_stream_disconnect_logs_cancelled_not_error(async_client, db_setup, monkeypatch):
    """Regression for #1552: a downstream disconnect mid-stream on a
    model-source route is a normal client-side terminal — recorded as
    status=cancelled (like the main proxy path), counted in cancelled_count,
    and excluded from the error rate and top_error."""
    from datetime import timedelta

    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceUsageHolder
    from app.modules.request_logs.repository import RequestLogsRepository

    async def record_release(reservation: object) -> None:
        del reservation

    monkeypatch.setattr(proxy_api, "_release_reservation", record_release)

    async def source_stream() -> AsyncIterator[bytes]:
        yield b"data: partial\n\n"
        await asyncio.sleep(60)

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
        id="src_cx_log",
        name="cx-log",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    response_stream = cast(
        AsyncGenerator[bytes, None],
        proxy_api._source_chat_stream_with_settlement(
            source_stream(),
            usage_holder=SourceUsageHolder(),
            request=request,
            source=source,
            api_key=None,
            model="cx-log-model",
            reservation=None,
        ),
    )

    assert await anext(response_stream) == b"data: partial\n\n"
    await response_stream.aclose()

    async with SessionLocal() as session:
        row = (await session.execute(select(RequestLog).where(RequestLog.model_source_id == "src_cx_log"))).scalar_one()
        assert row.status == "cancelled"
        assert row.error_code == "client_disconnected"

        # The status classification is what every metric surface keys on:
        # the disconnect must not join the error numerator or top_error.
        aggregate = await RequestLogsRepository(session).aggregate_usage_metrics_since(utcnow() - timedelta(minutes=5))
        assert aggregate.request_count == 1
        assert aggregate.error_count == 0
        assert aggregate.cancelled_count == 1
        assert aggregate.top_error is None


@pytest.mark.asyncio
async def test_source_stream_settlement_cancellation_logs_cancelled_not_success(monkeypatch: pytest.MonkeyPatch):
    from starlette.requests import Request

    import app.modules.proxy.api as proxy_api
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceUsage, SourceUsageHolder

    settle_started = asyncio.Event()
    allow_settle_finish = asyncio.Event()
    released: list[object] = []
    logs: list[dict[str, object]] = []

    async def settle(*_args: object, **_kwargs: object) -> bool:
        settle_started.set()
        await allow_settle_finish.wait()
        return True

    async def record_release(reservation: object) -> None:
        released.append(reservation)

    async def record_log(*_args: object, **kwargs: object) -> None:
        logs.append(dict(kwargs))

    monkeypatch.setattr(proxy_api, "_settle_source_reservation", settle)
    monkeypatch.setattr(proxy_api, "_release_reservation", record_release)
    monkeypatch.setattr(proxy_api, "_log_source_chat_completion", record_log)

    async def source_stream() -> AsyncIterator[bytes]:
        yield b"data: partial\n\n"

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/chat/completions",
            "raw_path": b"/v1/chat/completions",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("testserver", 80),
        }
    )
    source = ModelSource(
        id="src_stream_settlement_cancel",
        name="stream-settlement-cancel",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
    )
    reservation = ApiKeyUsageReservationData(
        reservation_id="resv_stream_settlement_cancel",
        key_id="key_stream_settlement_cancel",
        model="stream-settlement-cancel",
    )
    usage_holder = SourceUsageHolder(usage=SourceUsage(input_tokens=3, output_tokens=5))

    async def consume_stream() -> None:
        async for _chunk in proxy_api._source_chat_stream_with_settlement(
            source_stream(),
            usage_holder=usage_holder,
            request=request,
            source=source,
            api_key=None,
            model="stream-settlement-cancel",
            reservation=reservation,
        ):
            pass

    task = asyncio.create_task(consume_stream())
    await asyncio.wait_for(settle_started.wait(), timeout=1)
    task.cancel()
    allow_settle_finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert released == []
    assert logs[-1]["status"] == "cancelled"
    assert logs[-1]["error_code"] == "client_disconnected"
    assert logs[-1]["error_message"] == "client disconnected during source usage settlement"
    assert logs[-1]["usage"] == usage_holder.usage


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
async def test_v1_models_context_window_override_applies_to_source_model(async_client, monkeypatch):
    # Source-catalog models synthesize `max_context_window == context_window`
    # purely so Codex clients can parse the entry; that parseability default
    # must not clamp an operator raise override to the un-raised window.
    await _create_model_source(
        async_client,
        name="override-source",
        model="override-source-model",
        base_url="http://127.0.0.1:9/v1",
    )

    from app.core.config.settings import get_settings
    from app.modules.proxy import api as proxy_api_module

    patched = get_settings().model_copy(update={"model_context_window_overrides": {"override-source-model": 32_768}})
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: patched)

    response = await async_client.get("/v1/models")
    assert response.status_code == 200
    item = next(m for m in response.json()["data"] if m["id"] == "override-source-model")
    assert item["metadata"]["context_window"] == 32_768
    assert item["metadata"]["input_context_window"] == 32_768
    assert item["capabilities"]["context_length"] == 32_768
    assert item["contextLength"] == 32_768
    assert item["context_length"] == 32_768


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
@pytest.mark.parametrize(
    "reasoning_controls",
    [
        {"reasoning_effort": "max"},
        {"thinking": "minimal"},
        {"reasoning_effort": "max", "reasoning": {"summary": "auto"}},
        {"thinking": False, "enable_thinking": True},
        {"thinking": "disabled", "enable_thinking": True},
        {"thinking": {"summary": "auto", "enabled": True}},
        {"thinking": {"summary": "auto"}, "enable_thinking": True},
    ],
)
async def test_source_chat_reasoning_allowlist_rejects_before_source_dispatch(
    async_client,
    source_upstream,
    reasoning_controls,
):
    await _enable_api_key_auth(async_client)
    source_hits = 0

    async def completion(_request: web.Request) -> web.Response:
        nonlocal source_hits
        source_hits += 1
        return web.json_response(_chat_completion_body("source-reasoning-policy"))

    base_url = await source_upstream(completion)
    model = "source-reasoning-policy"
    source_id = await _create_model_source(async_client, name="source-reasoning-policy", model=model, base_url=base_url)
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source-reasoning-policy-key",
            "assignedSourceIds": [source_id],
            "allowedReasoningEfforts": ["low"],
        },
    )
    assert created.status_code == 200

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            **reasoning_controls,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "reasoning_effort_not_allowed"
    assert source_hits == 0


@pytest.mark.asyncio
async def test_source_chat_reasoning_allowlist_preserves_client_plane_effort(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def completion(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(_chat_completion_body("source-client-plane-reasoning"))

    base_url = await source_upstream(completion)
    model = "source-client-plane-reasoning"
    source_id = await _create_model_source(
        async_client,
        name="source-client-plane-reasoning",
        model=model,
        base_url=base_url,
        raw_metadata_json='{"supports_reasoning": true}',
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source-client-plane-reasoning-key",
            "assignedSourceIds": [source_id],
            "allowedReasoningEfforts": ["minimal"],
        },
    )
    assert created.status_code == 200

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "minimal",
            "thinking": {
                "effort": "minimal",
                "type": "disabled",
                "enabled": False,
                "vendor_hint": "keep",
            },
        },
    )

    assert response.status_code == 200
    assert captured["reasoning_effort"] == "minimal"
    assert captured["thinking"] == {"effort": "minimal", "vendor_hint": "keep"}
    assert "reasoning" not in captured


@pytest.mark.asyncio
async def test_source_chat_reasoning_allowlist_preserves_enable_thinking(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def completion(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(_chat_completion_body("source-enable-thinking"))

    base_url = await source_upstream(completion)
    model = "source-enable-thinking"
    source_id = await _create_model_source(
        async_client,
        name="source-enable-thinking",
        model=model,
        base_url=base_url,
        raw_metadata_json='{"supports_reasoning": true}',
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source-enable-thinking-key",
            "assignedSourceIds": [source_id],
            "allowedReasoningEfforts": ["medium"],
        },
    )
    assert created.status_code == 200

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "enable_thinking": True,
        },
    )

    assert response.status_code == 200
    assert captured["enable_thinking"] is True
    assert "reasoning" not in captured
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("thinking", "enable_thinking", "expected_thinking"),
    [
        (
            {"type": "enabled", "budget_tokens": 2048},
            False,
            {"type": "enabled", "budget_tokens": 2048},
        ),
        (
            {"enabled": True, "summary": "auto", "vendor_hint": "keep"},
            False,
            {"enabled": True, "summary": "auto", "vendor_hint": "keep"},
        ),
        (
            {"effort": " ", "enabled": True, "budget_tokens": 2048, "vendor_hint": "keep"},
            False,
            {"enabled": True, "budget_tokens": 2048, "vendor_hint": "keep"},
        ),
        ({"enabled": False}, True, None),
        ({"type": "disabled"}, True, None),
    ],
)
async def test_source_chat_reasoning_allowlist_preserves_implicit_thinking_object(
    async_client,
    source_upstream,
    thinking,
    enable_thinking,
    expected_thinking,
):
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def completion(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(_chat_completion_body("source-implicit-thinking"))

    base_url = await source_upstream(completion)
    model = "source-implicit-thinking"
    source_id = await _create_model_source(
        async_client,
        name=model,
        model=model,
        base_url=base_url,
        raw_metadata_json='{"supports_reasoning": true}',
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source-implicit-thinking-key",
            "assignedSourceIds": [source_id],
            "allowedReasoningEfforts": ["medium"],
        },
    )
    assert created.status_code == 200

    request_payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": thinking,
    }
    if enable_thinking:
        request_payload["enable_thinking"] = True
    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
        json=request_payload,
    )

    assert response.status_code == 200
    if expected_thinking is None:
        assert "thinking" not in captured
    else:
        assert captured["thinking"] == expected_thinking
    if enable_thinking:
        assert captured["enable_thinking"] is True
    assert "reasoning" not in captured
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize("alias_source", ["requested", "enforced"])
async def test_source_chat_reasoning_allowlist_materializes_canonicalized_model_alias_effort(
    async_client,
    source_upstream,
    alias_source,
):
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def completion(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(_chat_completion_body("gpt-5.6-sol"))

    base_url = await source_upstream(completion)
    model = "gpt-5.6-sol"
    source_id = await _create_model_source(
        async_client,
        name="source-canonical-model-alias-effort",
        model=model,
        base_url=base_url,
        raw_metadata_json='{"supports_reasoning": true}',
    )
    key_payload = {
        "name": "source-canonical-model-alias-effort-key",
        "assignedSourceIds": [source_id],
        "allowedReasoningEfforts": ["xhigh"],
    }
    if alias_source == "enforced":
        key_payload["enforcedModel"] = f"{model}-xhigh"
    created = await async_client.post("/api/api-keys/", json=key_payload)
    assert created.status_code == 200

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
        json={
            "model": f"{model}-xhigh" if alias_source == "requested" else model,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200
    assert captured["model"] == model
    assert captured["reasoning_effort"] == "xhigh"
    assert "reasoning" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("with_allowlist", "reasoning", "enable_thinking", "thinking_effort"),
    [
        (False, None, False, None),
        (True, None, False, None),
        (True, {"effort": "low"}, False, None),
        (True, {"effort": "low"}, True, None),
        (True, {"effort": "low"}, False, " "),
    ],
)
async def test_source_responses_preserves_effortless_provider_thinking_object(
    async_client,
    source_upstream,
    with_allowlist,
    reasoning,
    enable_thinking,
    thinking_effort,
):
    if with_allowlist:
        await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def responses(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(
            {
                "id": "resp_provider_thinking",
                "object": "response",
                "status": "completed",
                "model": "source-provider-thinking",
                "output": [],
            }
        )

    base_url = await source_upstream(responses)
    model = "source-provider-thinking"
    source_id = await _create_model_source(
        async_client,
        name="source-provider-thinking",
        model=model,
        base_url=base_url,
        supports_responses=True,
    )
    thinking = {"type": "adaptive", "budget": 4096, "budget_tokens": 2048, "vendor_hint": "keep"}
    if thinking_effort is not None:
        thinking["effort"] = thinking_effort
    headers: dict[str, str] = {}
    if with_allowlist:
        created = await async_client.post(
            "/api/api-keys/",
            json={
                "name": "source-provider-thinking-key",
                "assignedSourceIds": [source_id],
                "allowedReasoningEfforts": ["low"],
            },
        )
        assert created.status_code == 200
        headers["Authorization"] = f"Bearer {created.json()['key']}"

    request_payload = {
        "model": model,
        "instructions": "hi",
        "input": [],
        "thinking": thinking,
    }
    if reasoning is not None:
        request_payload["reasoning"] = reasoning
    if enable_thinking:
        request_payload["enable_thinking"] = True

    response = await async_client.post("/v1/responses", headers=headers, json=request_payload)

    assert response.status_code == 200
    expected_thinking = {key: value for key, value in thinking.items() if key != "effort"}
    assert captured["thinking"] == expected_thinking
    if reasoning is None:
        assert "reasoning" not in captured
    else:
        assert captured["reasoning"] == reasoning
    if enable_thinking:
        assert "enable_thinking" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key_policy", "reasoning_control", "expected_control"),
    [
        ("none", {"thinking": "minimal"}, {"thinking": "minimal"}),
        ("unrestricted", {"thinking": "minimal"}, {"thinking": "minimal"}),
        ("allowlisted", {"thinking": "minimal"}, {"reasoning": {"effort": "minimal"}}),
        ("enforced", {"thinking": "low"}, {"reasoning": {"effort": "minimal"}}),
        ("none", {"reasoning": {"effort": "minimal"}}, {"reasoning": {"effort": "minimal"}}),
        ("allowlisted", {"reasoning": {"effort": "minimal"}}, {"reasoning": {"effort": "minimal"}}),
    ],
)
async def test_source_responses_preserves_client_plane_reasoning_effort(
    async_client,
    source_upstream,
    key_policy,
    reasoning_control,
    expected_control,
):
    if key_policy != "none":
        await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def responses(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(
            {
                "id": "resp_provider_reasoning_alias",
                "object": "response",
                "status": "completed",
                "model": "source-provider-reasoning-alias",
                "output": [],
            }
        )

    base_url = await source_upstream(responses)
    model = "source-provider-reasoning-alias"
    source_id = await _create_model_source(
        async_client,
        name=model,
        model=model,
        base_url=base_url,
        supports_responses=True,
    )
    headers: dict[str, str] = {}
    if key_policy != "none":
        key_payload = {
            "name": "source-provider-reasoning-alias-key",
            "assignedSourceIds": [source_id],
        }
        if key_policy == "allowlisted":
            key_payload["allowedReasoningEfforts"] = ["minimal"]
        elif key_policy == "enforced":
            key_payload["enforcedReasoningEffort"] = "minimal"
        created = await async_client.post(
            "/api/api-keys/",
            json=key_payload,
        )
        assert created.status_code == 200
        headers["Authorization"] = f"Bearer {created.json()['key']}"

    response = await async_client.post(
        "/v1/responses",
        headers=headers,
        json={
            "model": model,
            "instructions": "hi",
            "input": [],
            **reasoning_control,
        },
    )

    assert response.status_code == 200
    for field in ("thinking", "reasoning"):
        if field in expected_control:
            assert captured[field] == expected_control[field]
        else:
            assert field not in captured


@pytest.mark.asyncio
async def test_source_responses_reasoning_allowlist_strips_conflicting_aliases(async_client, source_upstream):
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}

    async def responses(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response(
            {
                "id": "resp_reasoning_policy",
                "object": "response",
                "status": "completed",
                "model": "source-responses-reasoning-policy",
                "output": [],
            }
        )

    base_url = await source_upstream(responses)
    model = "source-responses-reasoning-policy"
    source_id = await _create_model_source(
        async_client,
        name="source-responses-reasoning-policy",
        model=model,
        base_url=base_url,
        supports_responses=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source-responses-reasoning-policy-key",
            "assignedSourceIds": [source_id],
            "allowedReasoningEfforts": ["low"],
        },
    )
    assert created.status_code == 200

    response = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
        json={
            "model": model,
            "instructions": "hi",
            "input": [],
            "reasoning": {"effort": "low"},
            "thinking": "max",
        },
    )

    assert response.status_code == 200
    assert captured["reasoning"] == {"effort": "low"}
    assert "thinking" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reasoning_controls",
    [
        {"reasoningEffort": " ", "thinking": "max"},
        {"thinking": False, "enable_thinking": True},
        {"thinking": "disabled", "enable_thinking": True},
        {"thinking": {"summary": "auto", "enabled": True}},
        {"thinking": {"summary": "auto"}, "enable_thinking": True},
    ],
)
async def test_source_responses_reasoning_allowlist_rejects_effort_hidden_by_inactive_alias(
    async_client,
    source_upstream,
    reasoning_controls,
):
    await _enable_api_key_auth(async_client)
    source_hits = 0

    async def responses(_request: web.Request) -> web.Response:
        nonlocal source_hits
        source_hits += 1
        return web.json_response(
            {
                "id": "resp_blank_reasoning_alias",
                "object": "response",
                "status": "completed",
                "model": "source-blank-reasoning-alias",
                "output": [],
            }
        )

    base_url = await source_upstream(responses)
    model = "source-blank-reasoning-alias"
    source_id = await _create_model_source(
        async_client,
        name="source-blank-reasoning-alias",
        model=model,
        base_url=base_url,
        supports_responses=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source-blank-reasoning-alias-key",
            "assignedSourceIds": [source_id],
            "allowedReasoningEfforts": ["low"],
        },
    )
    assert created.status_code == 200

    response = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
        json={
            "model": model,
            "instructions": "hi",
            "input": [],
            **reasoning_controls,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "reasoning_effort_not_allowed"
    assert source_hits == 0


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


@pytest.mark.asyncio
async def test_source_responses_payload_restores_declared_minimal_effort(async_client, source_upstream):
    """The minimal rewrite must be undone for a source that declared the effort.

    This pins the wiring, not just the helper: the restore lives inside
    _source_responses_response, and both the call and the threading of the
    replaced effort through enforcement have to survive for the source to see
    ``minimal`` instead of the ``low`` fallback.
    """
    captured: dict[str, object] = {}

    async def capture(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return web.json_response({"id": "resp_source_reasoning", "status": "completed", "output": []})

    base_url = await source_upstream(capture)
    model = "reasoning-levels-model"
    await _create_model_source(
        async_client,
        name="reasoning-levels",
        model=model,
        base_url=base_url,
        supports_responses=True,
        raw_metadata_json='{"supports_reasoning": true, "supported_reasoning_levels": ["minimal", "low", "high"]}',
    )

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": model,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "reasoning": {"effort": "minimal"},
        },
    )

    assert response.status_code == 200
    reasoning = captured["reasoning"]
    assert isinstance(reasoning, dict)
    assert reasoning["effort"] == "minimal"


@pytest.mark.asyncio
async def test_codex_responses_payload_restores_declared_minimal_effort(async_client, source_upstream):
    """The codex-native route must thread the replaced effort too.

    Codex CLI talks to this route, and ``--reasoning-effort minimal`` is where
    the rewrite originates, so this call site matters more than the /v1 one.
    It forces streaming for source-routed requests, hence the SSE upstream.
    """
    captured: dict[str, object] = {}
    frames = b'data: {"type":"response.completed","response":{"id":"resp_codex","status":"completed"}}\n\n'

    async def capture(request: web.Request) -> web.StreamResponse:
        captured.update(await request.json())
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(frames)
        await response.write_eof()
        return response

    base_url = await source_upstream(capture)
    model = "codex-reasoning-levels-model"
    await _create_model_source(
        async_client,
        name="codex-reasoning-levels",
        model=model,
        base_url=base_url,
        supports_responses=True,
        raw_metadata_json='{"supports_reasoning": true, "supported_reasoning_levels": ["minimal", "low", "high"]}',
    )

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={
            "model": model,
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "stream": True,
            "reasoning": {"effort": "minimal"},
        },
    ) as response:
        assert response.status_code == 200
        async for _ in response.aiter_bytes():
            pass

    reasoning = captured["reasoning"]
    assert isinstance(reasoning, dict)
    assert reasoning["effort"] == "minimal"


@pytest.mark.asyncio
async def test_source_embeddings_routes_explicit_null_fields(async_client, source_upstream) -> None:
    captured: dict[str, object] = {}
    model = "explicit-null-embedder"

    async def embed(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return _embedding_success_response(model)

    base_url = await source_upstream(embed)
    await _create_model_source(
        async_client,
        name="explicit-null-embedder",
        model=model,
        base_url=base_url,
        supports_embeddings=True,
    )

    response = await async_client.post(
        "/v1/embeddings",
        json={
            "model": model,
            "input": "hello",
            "dimensions": None,
            "user": None,
        },
    )

    assert response.status_code == 200
    assert captured == {
        "model": model,
        "input": "hello",
        "dimensions": None,
        "user": None,
    }


@pytest.mark.asyncio
async def test_source_embeddings_omits_unsent_fields(async_client, source_upstream) -> None:
    captured: dict[str, object] = {}
    model = "omitted-fields-embedder"

    async def embed(request: web.Request) -> web.Response:
        captured.update(await request.json())
        return _embedding_success_response(model)

    base_url = await source_upstream(embed)
    await _create_model_source(
        async_client,
        name="omitted-fields-embedder",
        model=model,
        base_url=base_url,
        supports_embeddings=True,
    )

    response = await async_client.post(
        "/v1/embeddings",
        json={"model": model, "input": "hello"},
    )

    assert response.status_code == 200
    assert captured == {"model": model, "input": "hello"}


@pytest.mark.asyncio
async def test_source_embeddings_routes_payload_and_settles_usage(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)
    captured: dict[str, object] = {}
    model = "all-minilm:latest"

    async def embed(request: web.Request) -> web.Response:
        captured["path"] = request.path
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = await request.json()
        return _embedding_success_response(model)

    base_url = await source_upstream(embed)
    source_id = await _create_model_source(
        async_client,
        name="embedder",
        model=model,
        base_url=base_url,
        input_per_1m=0.02,
        supports_embeddings=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "embeddings-source-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]
    key_id = created.json()["id"]

    response = await async_client.post(
        "/v1/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "input": ["hello", "world"],
            "encoding_format": "float",
            "dimensions": 384,
            "user": "client-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert captured["path"] == "/v1/embeddings"
    assert captured["authorization"] == "Bearer token-embedder"
    # Extra OpenAI params pass through verbatim.
    assert captured["payload"] == {
        "model": model,
        "input": ["hello", "world"],
        "encoding_format": "float",
        "dimensions": 384,
        "user": "client-1",
    }

    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 21
        reservations = await session.execute(
            select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.status == "reserved")
        )
        assert reservations.scalars().all() == []

        result = await session.execute(select(RequestLog).where(RequestLog.model == model))
        log = result.scalar_one()
        assert log.account_id is None
        assert log.model_source_id == source_id
        assert log.source == "model_source"
        assert log.input_tokens == 21
        assert log.output_tokens == 0
        assert log.status == "success"


@pytest.mark.asyncio
async def test_source_embeddings_cancellation_releases_reservation(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)
    forward_started = asyncio.Event()
    allow_upstream_finish = asyncio.Event()

    async def embed(_request: web.Request) -> web.Response:
        forward_started.set()
        await allow_upstream_finish.wait()
        return web.json_response(
            {
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
                "model": "cancelled-embedder",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        )

    base_url = await source_upstream(embed)
    model = "cancelled-embedder"
    source_id = await _create_model_source(
        async_client,
        name="cancelled-embedder-source",
        model=model,
        base_url=base_url,
        supports_embeddings=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "cancelled-embeddings-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]
    key_id = created.json()["id"]
    request_task = asyncio.create_task(
        async_client.post(
            "/v1/embeddings",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "input": "hello"},
        )
    )
    await asyncio.wait_for(forward_started.wait(), timeout=1)

    request_task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await request_task
    finally:
        allow_upstream_finish.set()

    async with SessionLocal() as session:
        result = await session.execute(
            select(ApiKeyUsageReservation).where(
                ApiKeyUsageReservation.api_key_id == key_id,
                ApiKeyUsageReservation.model == model,
            )
        )
        assert result.scalar_one().status == "released"


@pytest.mark.asyncio
async def test_source_embeddings_unknown_model_returns_model_not_found(async_client) -> None:
    await _enable_api_key_auth(async_client)
    created = await async_client.post("/api/api-keys/", json={"name": "embeddings-404-key"})
    assert created.status_code == 200
    key = created.json()["key"]

    response = await async_client.post(
        "/v1/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "no-such-embedder", "input": "hello"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"

    # Rejection happens before source selection succeeds, so no source was
    # contacted: the attempt must not appear in the request log at all.
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.model == "no-such-embedder"))
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_source_embeddings_transport_failure_logs_without_upstream_status(async_client) -> None:
    await _enable_api_key_auth(async_client)
    model = "unreachable-embedder"
    closed_port = _free_port()
    source_id = await _create_model_source(
        async_client,
        name="unreachable-embedder-source",
        model=model,
        base_url=f"http://127.0.0.1:{closed_port}/v1",
        supports_embeddings=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={"name": "embeddings-unreachable-key", "assignedSourceIds": [source_id]},
    )
    assert created.status_code == 200
    key = created.json()["key"]

    response = await async_client.post(
        "/v1/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "input": "hello"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_source_unreachable"

    # The attempt reached dispatch, so it is logged -- but no upstream response
    # ever arrived, so there is no upstream status code to carry.
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.model == model))
        log = result.scalar_one()
        assert log.status == "error"
        assert log.model_source_id == source_id
        assert log.upstream_status_code is None


@pytest.mark.asyncio
async def test_source_embeddings_upstream_error_passes_through_and_logs(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)

    async def embed(request: web.Request) -> web.Response:
        return web.json_response(
            {"error": {"message": "model exploded", "type": "server_error"}},
            status=500,
        )

    base_url = await source_upstream(embed)
    model = "broken-embedder"
    source_id = await _create_model_source(
        async_client,
        name="broken-embedder-source",
        model=model,
        base_url=base_url,
        supports_embeddings=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={"name": "embeddings-error-key", "assignedSourceIds": [source_id]},
    )
    assert created.status_code == 200
    key = created.json()["key"]

    response = await async_client.post(
        "/v1/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "input": "hello"},
    )

    assert response.status_code == 500
    assert "model exploded" in response.json()["error"]["message"]

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.model == model))
        log = result.scalar_one()
        assert log.status == "error"
        assert log.model_source_id == source_id


@pytest.mark.asyncio
async def test_source_embeddings_without_usage_fails_closed_for_limited_key(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)

    async def embed(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.5]}],
                "model": "usage-less-embedder",
            }
        )

    base_url = await source_upstream(embed)
    model = "usage-less-embedder"
    source_id = await _create_model_source(
        async_client,
        name="usage-less-embedder-source",
        model=model,
        base_url=base_url,
        supports_embeddings=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "embeddings-limited-key",
            "assignedSourceIds": [source_id],
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000},
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    response = await async_client.post(
        "/v1/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "input": "hello"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "usage_unavailable"
