from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.core.clients import proxy as proxy_client
from app.core.crypto import TokenEncryptor
from app.core.openai.requests import ResponsesRequest
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.proxy import service as proxy_service
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.service import ProxyService

pytestmark = pytest.mark.unit


class _SettingsCache:
    def __init__(self, settings: object) -> None:
        self._settings = settings

    async def get(self) -> object:
        return self._settings


class _RequestLogsRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def add_log(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


class _RepoContext:
    def __init__(self, request_logs: _RequestLogsRecorder) -> None:
        self._repos = cast(ProxyRepositories, SimpleNamespace(request_logs=request_logs))

    async def __aenter__(self) -> ProxyRepositories:
        return self._repos

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _repo_factory(request_logs: _RequestLogsRecorder) -> ProxyRepoFactory:
    def factory() -> _RepoContext:
        return _RepoContext(request_logs)

    return cast(ProxyRepoFactory, factory)


def _make_proxy_settings() -> object:
    return SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        upstream_stream_transport="default",
        openai_cache_affinity_max_age_seconds=300,
        openai_prompt_cache_key_derivation_enabled=True,
        routing_strategy="usage_weighted",
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        sticky_reallocation_budget_threshold_pct=95.0,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
    )


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    now = utcnow()
    return Account(
        id=account_id,
        chatgpt_account_id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-token"),
        refresh_token_encrypted=encryptor.encrypt("refresh-token"),
        id_token_encrypted=encryptor.encrypt("id-token"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


class _BufferedSSEContent:
    def __init__(self, first_event: bytes, buffered_tail: bytes) -> None:
        self._first_event = first_event
        self._buffered_tail = buffered_tail

    async def iter_chunked(self, size: int):
        if size >= 8 * 1024:
            await asyncio.sleep(0.03)
            yield self._first_event + self._buffered_tail
            return
        yield self._first_event
        await asyncio.sleep(0.03)
        yield self._buffered_tail


class _BufferedSSEResponse:
    def __init__(self, first_event: bytes, buffered_tail: bytes) -> None:
        self.content = _BufferedSSEContent(first_event, buffered_tail)


async def _time_to_first_sse_event(chunk_size: int) -> float:
    first_event = b'data: {"delta":"x"}\n\n'
    buffered_tail = b"y" * 7000
    response = _BufferedSSEResponse(first_event, buffered_tail)
    started = time.monotonic()
    original_chunk_size = proxy_client._SSE_READ_CHUNK_SIZE
    setattr(proxy_client, "_SSE_READ_CHUNK_SIZE", chunk_size)
    try:
        iterator = proxy_client._iter_sse_events(cast(proxy_client.SSEResponse, response), 1.0, 16 * 1024)
        first = await iterator.__anext__()
        assert first == first_event.decode("utf-8")
    finally:
        setattr(proxy_client, "_SSE_READ_CHUNK_SIZE", original_chunk_size)
    return time.monotonic() - started


@pytest.mark.asyncio
async def test_smaller_sse_chunks_deliver_first_event_faster() -> None:
    small_chunk_elapsed = await _time_to_first_sse_event(1 * 1024)
    large_chunk_elapsed = await _time_to_first_sse_event(8 * 1024)

    assert small_chunk_elapsed + 0.01 < large_chunk_elapsed


def test_normalize_sse_event_block_skips_json_parsing_without_type(monkeypatch) -> None:
    block = 'data: {"delta":"hi"}\n\n'

    def fail_json_parse(_: str) -> object:
        raise AssertionError("json.loads should not run for blocks without a type field")

    monkeypatch.setattr(proxy_client.json, "loads", fail_json_parse)

    assert proxy_client._normalize_sse_event_block(block) == block


@pytest.mark.asyncio
async def test_stream_responses_tracks_latency_first_token_ms(monkeypatch) -> None:
    settings = _make_proxy_settings()
    request_logs = _RequestLogsRecorder()
    service = ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ttft")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        del payload, headers, access_token, account_id, base_url, raise_for_status
        await asyncio.sleep(0.02)
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_ttft","usage":'
            '{"input_tokens":1,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]
    latency_first_token_ms = cast(int, request_logs.calls[0]["latency_first_token_ms"])

    assert len(chunks) == 2
    assert latency_first_token_ms > 0


@pytest.mark.asyncio
async def test_stream_responses_ttft_ignores_control_frame_before_text_delta(monkeypatch) -> None:
    settings = _make_proxy_settings()
    request_logs = _RequestLogsRecorder()
    service = ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ttft_control_frame")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        del payload, headers, access_token, account_id, base_url, raise_for_status
        yield 'data: {"type":"response.created","response":{"id":"resp_ttft"}}\n\n'
        await asyncio.sleep(0.03)
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_ttft","usage":'
            '{"input_tokens":1,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream-control"})]
    latency_first_token_ms = cast(int, request_logs.calls[0]["latency_first_token_ms"])

    assert len(chunks) == 3
    assert latency_first_token_ms >= 20
