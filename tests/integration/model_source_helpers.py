"""Shared fixtures and helpers for model-source routing integration tests.

Extracted from ``test_model_source_routing.py`` so the transport-hardening and
source-dispatch suites (#2123 WP-C1) can drive the same stub upstream and
dashboard helpers without importing a test module.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeAlias

from aiohttp import web

_UpstreamHandler: TypeAlias = Callable[[web.Request], Awaitable[web.StreamResponse]]


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


@asynccontextmanager
async def stub_source_upstreams() -> AsyncIterator[Callable[..., Awaitable[str]]]:
    """Run stub OpenAI-compatible upstreams for one test; yields ``start(handler) -> base_url``.

    Test modules wrap this in a local ``source_upstream`` fixture (a fixture
    imported across modules trips ruff's F811 on every test parameter).
    ``start`` accepts ``handler_cancellation`` / ``shutdown_timeout`` for
    handlers that deliberately stall until the client leaves, so teardown does
    not wait out aiohttp's 60 s default drain.
    """

    runners: list[web.AppRunner] = []

    async def start(
        handler: _UpstreamHandler,
        *,
        handler_cancellation: bool = False,
        shutdown_timeout: float = 60.0,
    ) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        runner = web.AppRunner(app, handler_cancellation=handler_cancellation, shutdown_timeout=shutdown_timeout)
        await runner.setup()
        port = _free_port()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        runners.append(runner)
        return f"http://127.0.0.1:{port}/v1"

    try:
        yield start
    finally:
        for runner in runners:
            await runner.cleanup()
