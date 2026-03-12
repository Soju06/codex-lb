from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from types import SimpleNamespace
from typing import Protocol, cast
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi import WebSocket
from starlette.requests import Request

import app.core.clients.proxy as proxy_module
from app.core.clients.proxy import _build_upstream_headers, filter_inbound_headers
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error
from app.core.openai.models import OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.utils.request_id import get_request_id, reset_request_id, set_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.proxy import api as proxy_api
from app.modules.proxy import service as proxy_service
from app.modules.proxy.load_balancer import AccountSelection

pytestmark = pytest.mark.unit


def _assert_proxy_response_error(exc: BaseException) -> proxy_module.ProxyResponseError:
    assert isinstance(exc, proxy_module.ProxyResponseError)
    return exc


def test_filter_inbound_headers_strips_auth_and_account():
    headers = {
        "Authorization": "Bearer x",
        "chatgpt-account-id": "acc_1",
        "Content-Encoding": "gzip",
        "Content-Type": "application/json",
        "X-Request-Id": "req_1",
    }
    filtered = filter_inbound_headers(headers)
    assert "Authorization" not in filtered
    assert "chatgpt-account-id" not in filtered
    assert filtered["Content-Encoding"] == "gzip"
    assert filtered["Content-Type"] == "application/json"
    assert filtered["X-Request-Id"] == "req_1"


def test_filter_inbound_headers_strips_proxy_identity_headers():
    headers = {
        "X-Forwarded-For": "1.2.3.4",
        "X-Forwarded-Proto": "https",
        "X-Real-IP": "1.2.3.4",
        "Forwarded": "for=1.2.3.4;proto=https",
        "CF-Connecting-IP": "1.2.3.4",
        "CF-Ray": "ray123",
        "True-Client-IP": "1.2.3.4",
        "User-Agent": "codex-test",
        "Accept": "text/event-stream",
    }

    filtered = filter_inbound_headers(headers)

    assert "X-Forwarded-For" not in filtered
    assert "X-Forwarded-Proto" not in filtered
    assert "X-Real-IP" not in filtered
    assert "Forwarded" not in filtered
    assert "CF-Connecting-IP" not in filtered
    assert "CF-Ray" not in filtered
    assert "True-Client-IP" not in filtered
    assert filtered["User-Agent"] == "codex-test"
    assert filtered["Accept"] == "text/event-stream"


def test_build_upstream_headers_overrides_auth():
    inbound = {"X-Request-Id": "req_1"}
    headers = _build_upstream_headers(inbound, "token", "acc_2")
    assert headers["Authorization"] == "Bearer token"
    assert headers["chatgpt-account-id"] == "acc_2"
    assert headers["Accept"] == "text/event-stream"
    assert headers["Content-Type"] == "application/json"


def test_build_upstream_headers_accept_override():
    inbound = {}
    headers = _build_upstream_headers(inbound, "token", None, accept="application/json")
    assert headers["Accept"] == "application/json"


def test_parse_sse_event_reads_json_payload():
    payload = {"type": "response.completed", "response": {"id": "resp_1"}}
    line = f"data: {json.dumps(payload)}\n"
    event = parse_sse_event(line)
    assert event is not None
    assert event.type == "response.completed"
    assert event.response
    assert event.response.id == "resp_1"


def test_parse_sse_event_reads_multiline_payload():
    payload = {
        "type": "response.failed",
        "response": {"id": "resp_1", "status": "failed", "error": {"code": "upstream_error"}},
    }
    line = f"event: response.failed\ndata: {json.dumps(payload)}\n\n"
    event = parse_sse_event(line)
    assert event is not None
    assert event.type == "response.failed"
    assert event.response
    assert event.response.id == "resp_1"


def test_parse_sse_event_ignores_non_data_lines():
    assert parse_sse_event("event: ping\n") is None


def test_parse_sse_event_concats_multiple_data_lines():
    payload = {"type": "response.completed", "response": {"id": "resp_1"}}
    raw = json.dumps(payload)
    first, second = raw[: len(raw) // 2], raw[len(raw) // 2 :]
    line = f"data: {first}\ndata: {second}\n\n"

    event = parse_sse_event(line)

    assert event is not None
    assert event.type == "response.completed"


def test_normalize_sse_event_block_rewrites_response_text_alias():
    block = 'data: {"type":"response.text.delta","delta":"hi"}\n\n'

    normalized = proxy_module._normalize_sse_event_block(block)

    assert '"type":"response.output_text.delta"' in normalized
    assert normalized.endswith("\n\n")


def test_find_sse_separator_prefers_earliest_separator():
    buffer = b"event: one\n\ndata: two\r\n\r\n"

    result = proxy_module._find_sse_separator(buffer)

    assert result == (10, 2)


def test_pop_sse_event_returns_first_event_and_mutates_buffer():
    buffer = bytearray(b"data: one\n\ndata: two\n\n")

    event = proxy_module._pop_sse_event(buffer)

    assert event == b"data: one\n\n"
    assert bytes(buffer) == b"data: two\n\n"


class _DummyContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self._chunks:
            yield chunk


class _DummyResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.content = _DummyContent(chunks)


class _TranscribeResponse:
    def __init__(self, payload: dict[str, object], *, json_error: Exception | None = None) -> None:
        self.status = 200
        self.reason = "OK"
        self._payload = payload
        self._json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, *, content_type=None):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _TranscribeSession:
    def __init__(self, response: _TranscribeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        data=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return self._response


class _TimeoutTranscribeSession:
    def post(
        self,
        url: str,
        *,
        data=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        raise asyncio.TimeoutError


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
        self._repos = SimpleNamespace(request_logs=request_logs)

    async def __aenter__(self) -> object:
        return self._repos

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _repo_factory(request_logs: _RequestLogsRecorder):
    def factory() -> _RepoContext:
        return _RepoContext(request_logs)

    return factory


def _make_proxy_settings(*, log_proxy_service_tier_trace: bool) -> object:
    return SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        routing_strategy="usage_weighted",
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=log_proxy_service_tier_trace,
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


class _JsonCompactResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self.reason = "OK"
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, *, content_type=None):
        return self._payload


class _CompactSession:
    class _CompactResponseLike(Protocol):
        async def __aenter__(self): ...
        async def __aexit__(self, exc_type, exc, tb): ...
        async def json(self, *, content_type=None): ...

    def __init__(self, response: _CompactResponseLike) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._response


class _SsePostResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.status = 200
        self.content = _DummyContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SseSession:
    def __init__(self, response: _SsePostResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._response


class _TimeoutSseSession:
    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        raise asyncio.TimeoutError


@pytest.mark.asyncio
async def test_iter_sse_events_handles_large_single_line_without_chunk_too_big():
    large_data = "A" * (200 * 1024)
    event = f'data: {{"type":"response.output_text.delta","delta":"{large_data}"}}\n\n'.encode("utf-8")
    response = _DummyResponse([event[:4096], event[4096:]])

    chunks = [chunk async for chunk in proxy_module._iter_sse_events(response, 1.0, 512 * 1024)]

    assert len(chunks) == 1
    assert chunks[0].startswith("data: ")
    assert chunks[0].endswith("\n\n")


@pytest.mark.asyncio
async def test_iter_sse_events_raises_on_event_size_limit():
    large_data = b"A" * 1024
    response = _DummyResponse([b"data: ", large_data])

    with pytest.raises(proxy_module.StreamEventTooLargeError):
        async for _ in proxy_module._iter_sse_events(response, 1.0, 256):
            pass


@pytest.mark.asyncio
async def test_iter_sse_events_raises_idle_timeout(monkeypatch):
    response = _DummyResponse([b'data: {"type":"response.in_progress"}\n\n'])

    async def fake_wait(tasks, *args, **kwargs):
        task = next(iter(tasks))
        task.cancel()
        return set(), set(tasks)

    monkeypatch.setattr(proxy_module.asyncio, "wait", fake_wait)

    with pytest.raises(proxy_module.StreamIdleTimeoutError):
        async for _ in proxy_module._iter_sse_events(response, 1.0, 1024):
            pass


@pytest.mark.asyncio
async def test_iter_sse_events_propagates_upstream_timeout():
    class _TimeoutContent:
        async def iter_chunked(self, size: int):
            if size <= 0:
                yield b""
            raise asyncio.TimeoutError

    class _TimeoutResponse:
        def __init__(self) -> None:
            self.content = _TimeoutContent()

    with pytest.raises(asyncio.TimeoutError):
        async for _ in proxy_module._iter_sse_events(_TimeoutResponse(), 1.0, 1024):
            pass


@pytest.mark.asyncio
async def test_iter_sse_events_cancels_pending_chunk_read():
    class _BlockingContent:
        def __init__(self) -> None:
            self.cancelled = asyncio.Event()

        async def iter_chunked(self, size: int):
            try:
                await asyncio.Future()
                if size < 0:
                    yield b""
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    class _BlockingResponse:
        def __init__(self) -> None:
            self.content = _BlockingContent()

    response = _BlockingResponse()

    async def consume() -> None:
        async for _ in proxy_module._iter_sse_events(response, 10.0, 1024):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert response.content.cancelled.is_set()


def test_log_proxy_request_payload(monkeypatch, caplog):
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    class Settings:
        log_proxy_request_payload = True
        log_proxy_request_shape = False
        log_proxy_request_shape_raw_cache_key = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_log_1")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_request_payload("stream", payload, {"X-Request-Id": "req_log_1"})
    finally:
        reset_request_id(token)

    assert "proxy_request_payload" in caplog.text
    assert '"model":"gpt-5.1"' in caplog.text


def test_log_proxy_service_tier_trace(monkeypatch, caplog):
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "secret instructions",
            "input": [{"role": "user", "content": "secret prompt"}],
            "service_tier": "priority",
        }
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = False
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = True

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_tier_trace_1")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_service_tier_trace(
            "stream",
            requested_service_tier=payload.service_tier,
            actual_service_tier="default",
        )
    finally:
        reset_request_id(token)

    assert "proxy_service_tier_trace" in caplog.text
    assert "request_id=req_tier_trace_1" in caplog.text
    assert "kind=stream" in caplog.text
    assert "requested_service_tier=priority" in caplog.text
    assert "actual_service_tier=default" in caplog.text
    assert "secret instructions" not in caplog.text
    assert "secret prompt" not in caplog.text


def test_log_proxy_service_tier_trace_disabled(monkeypatch, caplog):
    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = False
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_tier_trace_2")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_service_tier_trace(
            "compact",
            requested_service_tier="priority",
            actual_service_tier=None,
        )
    finally:
        reset_request_id(token)

    assert "proxy_service_tier_trace" not in caplog.text


def test_log_upstream_request_trace(monkeypatch, caplog):
    class Settings:
        log_upstream_request_summary = True
        log_upstream_request_payload = True

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())

    token = set_request_id("req_upstream_1")
    try:
        caplog.set_level(logging.INFO)
        headers = _build_upstream_headers({"session_id": "sid_1"}, "token", "acc_upstream_1")
        payload_json = '{"model":"gpt-5.1","input":"hi"}'
        proxy_module._maybe_log_upstream_request_start(
            kind="responses",
            url="https://chatgpt.com/backend-api/codex/responses",
            headers=headers,
            payload_summary="model=gpt-5.1 stream=True input=str keys=['input','model','stream']",
            payload_json=payload_json,
        )
        proxy_module._maybe_log_upstream_request_complete(
            kind="responses",
            url="https://chatgpt.com/backend-api/codex/responses",
            headers=headers,
            started_at=0.0,
            status_code=502,
            error_code="upstream_error",
            error_message="backend exploded",
        )
    finally:
        reset_request_id(token)

    assert "upstream_request_start request_id=req_upstream_1" in caplog.text
    assert "upstream_request_payload request_id=req_upstream_1" in caplog.text
    assert "upstream_request_complete request_id=req_upstream_1" in caplog.text
    assert "target=https://chatgpt.com/backend-api/codex/responses" in caplog.text
    assert "error_message=backend exploded" in caplog.text


@pytest.mark.asyncio
async def test_stream_responses_starts_upstream_timer_after_image_inlining(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 1.0
        stream_idle_timeout_seconds = 1.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = True
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 15.0

    inline_ran = False
    recorded: dict[str, float | None] = {}

    async def fake_inline(payload_dict, session, connect_timeout):
        nonlocal inline_ran
        inline_ran = True
        return payload_dict

    monotonic_values = iter([100.0, 104.0, 104.0, 104.0])

    def fake_monotonic():
        return next(monotonic_values, 104.0)

    def fake_complete(**kwargs):
        recorded["started_at"] = kwargs["started_at"]

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_inline_input_image_urls", fake_inline)
    monkeypatch.setattr(proxy_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", fake_complete)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']))

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(11.0)
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']
    assert recorded["started_at"] == 104.0


@pytest.mark.asyncio
async def test_stream_responses_honors_timeout_overrides(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False

    seen: dict[str, object] = {}

    async def fake_iter(resp, idle_timeout_seconds, max_event_bytes):
        seen["idle_timeout_seconds"] = idle_timeout_seconds
        seen["max_event_bytes"] = max_event_bytes
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_iter_sse_events", fake_iter)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _SseSession(_SsePostResponse([b"unused"]))

    token = set_request_id("req_timeout_override")
    try:
        with proxy_module.override_stream_timeouts(
            connect_timeout_seconds=2.5,
            idle_timeout_seconds=3.5,
            total_timeout_seconds=4.5,
        ):
            events = [
                event
                async for event in proxy_module.stream_responses(
                    payload,
                    headers={},
                    access_token="token",
                    account_id="acc_1",
                    session=cast(proxy_module.aiohttp.ClientSession, session),
                )
            ]
    finally:
        reset_request_id(token)

    assert events == ['data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']
    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(4.5, abs=0.01)
    assert timeout.sock_connect == pytest.approx(2.5)
    assert seen["idle_timeout_seconds"] == pytest.approx(3.5)


@pytest.mark.asyncio
async def test_stream_responses_maps_total_timeout_to_request_timeout(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 5.0

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _TimeoutSseSession()),
        )
    ]

    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_request_timeout"


@pytest.mark.asyncio
async def test_stream_responses_maps_connect_timeout_to_upstream_unavailable(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 5.0

    class _ConnectTimeoutSseSession:
        def post(
            self,
            url: str,
            *,
            json=None,
            headers: dict[str, str] | None = None,
            timeout=None,
        ):
            raise proxy_module.aiohttp.ConnectionTimeoutError("connect timed out")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _ConnectTimeoutSseSession()),
        )
    ]

    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_compact_responses_starts_upstream_timer_after_image_inlining(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 1.0
        upstream_compact_timeout_seconds = 12.0
        image_inline_fetch_enabled = True
        log_upstream_request_payload = False

    inline_ran = False
    recorded: dict[str, float | None] = {}

    async def fake_inline(payload_dict, session, connect_timeout):
        nonlocal inline_ran
        inline_ran = True
        return payload_dict

    monotonic_values = iter([200.0, 205.5, 205.5, 205.5])

    def fake_monotonic():
        return next(monotonic_values, 205.5)

    def fake_complete(**kwargs):
        recorded["started_at"] = kwargs["started_at"]

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_inline_input_image_urls", fake_inline)
    monkeypatch.setattr(proxy_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", fake_complete)

    payload = proxy_module.ResponsesCompactRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _CompactSession(_JsonCompactResponse({"output": []}))

    result = await proxy_module.compact_responses(
        payload,
        headers={},
        access_token="token",
        account_id="acc_1",
        session=cast(proxy_module.aiohttp.ClientSession, session),
    )

    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(6.5)
    assert timeout.sock_connect == pytest.approx(0.001)
    assert timeout.sock_read == pytest.approx(6.5)
    assert result.model_extra == {"output": []}
    assert recorded["started_at"] == 205.5


@pytest.mark.asyncio
async def test_compact_responses_uses_configured_timeout_and_maps_read_timeout(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 2.0
        upstream_compact_timeout_seconds = 123.0
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False

    class _TimeoutCompactResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self, *, content_type=None):
            raise proxy_module.aiohttp.SocketTimeoutError("Timeout on reading data from socket")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = proxy_module.ResponsesCompactRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _CompactSession(_TimeoutCompactResponse())

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await proxy_module.compact_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )

    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(123.0, abs=0.05)
    assert timeout.sock_connect == pytest.approx(2.0, abs=0.05)
    assert timeout.sock_read == pytest.approx(123.0, abs=0.05)
    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    assert exc.payload["error"]["message"] == "Timeout on reading data from socket"


@pytest.mark.asyncio
async def test_compact_responses_defaults_to_no_request_timeout(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 2.0
        upstream_compact_timeout_seconds = None
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = proxy_module.ResponsesCompactRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _CompactSession(_JsonCompactResponse({"output": []}))

    result = await proxy_module.compact_responses(
        payload,
        headers={},
        access_token="token",
        account_id="acc_1",
        session=cast(proxy_module.aiohttp.ClientSession, session),
    )

    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total is None
    assert timeout.sock_connect == pytest.approx(2.0, abs=0.05)
    assert timeout.sock_read is None
    assert result.model_extra == {"output": []}


def test_sticky_key_for_responses_request_uses_bounded_cache_affinity():
    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})
    payload.prompt_cache_key = "thread_123"

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.key == "thread_123"
    assert policy.kind == proxy_service.StickySessionKind.PROMPT_CACHE
    assert policy.reallocate_sticky is False
    assert policy.max_age_seconds == 300


def test_sticky_key_for_responses_request_keeps_sticky_threads_durable():
    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})
    payload.prompt_cache_key = "thread_123"

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=True,
    )

    assert policy.key == "thread_123"
    assert policy.kind == proxy_service.StickySessionKind.STICKY_THREAD
    assert policy.reallocate_sticky is True
    assert policy.max_age_seconds is None


def test_sticky_key_for_compact_request_prefers_codex_session_affinity():
    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "prompt_cache_key": "thread_123",
        }
    )

    policy = proxy_service._sticky_key_for_compact_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=True,
    )

    assert policy.key == "codex-session-1"
    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert policy.reallocate_sticky is False
    assert policy.max_age_seconds is None


@pytest.mark.asyncio
async def test_service_compact_budget_does_not_override_unbounded_read_timeout(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_unbounded_read")
    runtime_values = dict(settings.__dict__)
    runtime_values["compact_request_budget_seconds"] = 3.0
    runtime_settings = SimpleNamespace(**runtime_values)
    captured: dict[str, float | None] = {}

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(runtime_settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_compact_api_key_usage", AsyncMock())

    async def fake_compact(payload, headers, access_token, account_id):
        captured["connect_timeout"] = proxy_module._COMPACT_CONNECT_TIMEOUT_OVERRIDE.get()
        captured["total_timeout"] = proxy_module._COMPACT_TOTAL_TIMEOUT_OVERRIDE.get()
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_service, "core_compact_responses", fake_compact)

    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})

    result = await service.compact_responses(payload, {"session_id": "sid-compact"})

    assert captured["connect_timeout"] == pytest.approx(3.0)
    assert captured["total_timeout"] is None
    assert result.model_extra == {"output": []}


def test_logged_error_json_response_emits_proxy_error_log(caplog):
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/responses",
        "raw_path": b"/v1/responses",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 2455),
    }
    request = Request(scope)

    token = set_request_id("req_proxy_error_1")
    try:
        caplog.set_level(logging.WARNING)
        response = proxy_api._logged_error_json_response(
            request,
            502,
            {"error": {"code": "upstream_error", "message": "provider failed"}},
        )
    finally:
        reset_request_id(token)

    assert response.status_code == 502
    assert "proxy_error_response request_id=req_proxy_error_1" in caplog.text
    assert "code=upstream_error" in caplog.text
    assert "message=provider failed" in caplog.text


@pytest.mark.asyncio
async def test_stream_responses_logs_service_tier_trace_from_actual_path(monkeypatch, caplog):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=True)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_trace_stream")

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
        yield 'data: {"type":"response.completed","response":{"id":"resp_trace_stream","service_tier":"default"}}\n\n'

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "service_tier": "priority",
        }
    )

    token = set_request_id(None)
    try:
        caplog.set_level(logging.WARNING)
        chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]
        request_id = get_request_id()
    finally:
        reset_request_id(token)

    assert chunks
    assert request_id
    assert request_logs.calls[0]["service_tier"] == "default"
    assert f"request_id={request_id}" in caplog.text
    assert "kind=stream" in caplog.text
    assert "requested_service_tier=priority" in caplog.text
    assert "actual_service_tier=default" in caplog.text


@pytest.mark.asyncio
async def test_compact_responses_logs_service_tier_trace_and_generates_request_id(monkeypatch, caplog):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=True)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_trace_compact")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_compact_api_key_usage", AsyncMock())

    async def fake_compact(payload, headers, access_token, account_id):
        return OpenAIResponsePayload.model_validate({"output": [], "service_tier": "default"})

    monkeypatch.setattr(proxy_service, "core_compact_responses", fake_compact)

    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "summarize",
            "input": [],
            "service_tier": "priority",
        }
    )

    token = set_request_id(None)
    try:
        caplog.set_level(logging.WARNING)
        response = await service.compact_responses(payload, {"session_id": "sid-compact"}, codex_session_affinity=True)
        request_id = get_request_id()
    finally:
        reset_request_id(token)

    assert proxy_service._service_tier_from_response(response) == "default"
    assert request_id
    assert f"request_id={request_id}" in caplog.text
    assert "kind=compact" in caplog.text
    assert "requested_service_tier=priority" in caplog.text
    assert "actual_service_tier=default" in caplog.text
    assert request_logs.calls[0]["transport"] == "http"


@pytest.mark.asyncio
async def test_stream_responses_propagates_selection_error_code(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(
            return_value=AccountSelection(
                account=None,
                error_message="No fresh additional quota data available for model 'gpt-5.3-codex-spark'",
                error_code="additional_quota_data_unavailable",
            )
        ),
    )

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.3-codex-spark",
            "instructions": "hi",
            "input": [],
            "stream": True,
        }
    )

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "additional_quota_data_unavailable"
    assert request_logs.calls[0]["error_code"] == "additional_quota_data_unavailable"


@pytest.mark.asyncio
async def test_stream_responses_non_retryable_first_failure_does_not_retry(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_no_retry")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    select_account = AsyncMock(return_value=AccountSelection(account=account, error_message=None))
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            'data: {"type":"response.failed","response":{"error":{"code":"stream_idle_timeout","message":"idle"}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "stream_idle_timeout"
    assert select_account.await_count == 1
    record_error.assert_not_awaited()
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_proxy_websocket_passes_sticky_kind_to_load_balancer(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_sticky")
    select_account = AsyncMock(return_value=AccountSelection(account=account, error_message=None))
    upstream = SimpleNamespace()

    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_open_upstream_websocket", AsyncMock(return_value=upstream))

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_1",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket = cast(WebSocket, SimpleNamespace(send_text=AsyncMock()))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key="codex-session-1",
        sticky_kind=proxy_service.StickySessionKind.CODEX_SESSION,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account == account
    assert selected_upstream is upstream
    await_args = select_account.await_args
    assert await_args is not None
    assert await_args.kwargs["sticky_key"] == "codex-session-1"
    assert await_args.kwargs["sticky_kind"] == proxy_service.StickySessionKind.CODEX_SESSION


@pytest.mark.asyncio
async def test_connect_proxy_websocket_logs_preconnect_failure(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    select_account = AsyncMock(
        return_value=AccountSelection(
            account=None, error_message="No active accounts available", error_code="no_accounts"
        )
    )

    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_no_accounts",
        model="gpt-5.1",
        service_tier="default",
        reasoning_effort="high",
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket = cast(WebSocket, SimpleNamespace(send_text=AsyncMock()))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    assert request_logs.calls[0]["request_id"] == "ws_req_no_accounts"
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] == "no_accounts"
    assert request_logs.calls[0]["transport"] == "websocket"


@pytest.mark.asyncio
async def test_connect_proxy_websocket_surfaces_retry_handshake_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_retry_error")
    first_exc = proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "expired"))
    second_exc = proxy_module.ProxyResponseError(403, openai_error("forbidden", "denied"))
    handle_connect_error = AsyncMock()

    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=[account, account]))
    monkeypatch.setattr(service, "_open_upstream_websocket", AsyncMock(side_effect=[first_exc, second_exc]))
    monkeypatch.setattr(service, "_handle_websocket_connect_error", handle_connect_error)
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_retry_error",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    await_args = handle_connect_error.await_args
    assert await_args is not None
    assert await_args.args[1] is second_exc
    sent_payload = json.loads(websocket_send.await_args.args[0])
    assert sent_payload["status"] == 403
    assert sent_payload["error"]["code"] == "forbidden"
    assert request_logs.calls[0]["error_code"] == "forbidden"


@pytest.mark.asyncio
async def test_connect_proxy_websocket_surfaces_refresh_transport_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_refresh_timeout")
    release_reservation = AsyncMock()

    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=asyncio.TimeoutError()))
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_refresh_timeout",
        model="gpt-5.1",
        service_tier="fast",
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    release_reservation.assert_awaited_once_with(None)
    sent_payload = json.loads(websocket_send.await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_unavailable"
    assert sent_payload["error"]["message"] == "Request to upstream timed out"
    assert request_logs.calls[0]["request_id"] == "ws_req_refresh_timeout"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["transport"] == "websocket"


@pytest.mark.asyncio
async def test_connect_proxy_websocket_surfaces_forced_refresh_transport_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_forced_refresh_timeout")
    initial_error = proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "expired"))
    release_reservation = AsyncMock()

    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=[account, asyncio.TimeoutError()]))
    monkeypatch.setattr(service, "_open_upstream_websocket", AsyncMock(side_effect=initial_error))
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_forced_refresh_timeout",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    release_reservation.assert_awaited_once_with(None)
    sent_payload = json.loads(websocket_send.await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_unavailable"
    assert sent_payload["error"]["message"] == "Request to upstream timed out"
    assert request_logs.calls[0]["request_id"] == "ws_req_forced_refresh_timeout"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["transport"] == "websocket"


def test_apply_api_key_enforcement_to_websocket_payload_strips_service_tier():
    payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "service_tier": "fast",
        "reasoning": {"effort": "medium"},
    }

    result = proxy_service._apply_api_key_enforcement_to_websocket_payload(payload, api_key=None)

    assert result["type"] == "response.create"
    assert result["model"] == "gpt-5.1"
    assert "service_tier" not in result
    assert payload["service_tier"] == "fast"


@pytest.mark.asyncio
async def test_finalize_websocket_request_state_updates_balancer_state(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_finalize")
    record_success = AsyncMock()
    handle_stream_error = AsyncMock()

    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    completed_payload = {
        "type": "response.completed",
        "response": {
            "id": "resp_ws_complete",
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        },
    }
    completed_event = parse_sse_event(f"data: {json.dumps(completed_payload)}\n\n")
    assert completed_event is not None
    completed_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_complete",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    completed_upstream_control = proxy_service._WebSocketUpstreamControl()

    await service._finalize_websocket_request_state(
        completed_state,
        account=account,
        account_id_value=account.id,
        event=completed_event,
        event_type="response.completed",
        payload=completed_payload,
        api_key=None,
        upstream_control=completed_upstream_control,
    )

    record_success.assert_awaited_once_with(account)
    handle_stream_error.assert_not_awaited()
    assert completed_upstream_control.reconnect_requested is False

    failed_payload = {
        "type": "response.failed",
        "response": {
            "id": "resp_ws_failed",
            "error": {"code": "rate_limit_exceeded", "message": "slow down"},
            "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        },
    }
    failed_event = parse_sse_event(f"data: {json.dumps(failed_payload)}\n\n")
    assert failed_event is not None
    failed_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_failed",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    failed_upstream_control = proxy_service._WebSocketUpstreamControl()

    await service._finalize_websocket_request_state(
        failed_state,
        account=account,
        account_id_value=account.id,
        event=failed_event,
        event_type="response.failed",
        payload=failed_payload,
        api_key=None,
        upstream_control=failed_upstream_control,
    )

    handle_args = handle_stream_error.await_args
    assert handle_args is not None
    assert handle_args.args[0] == account
    assert handle_args.args[2] == "rate_limit_exceeded"
    assert failed_upstream_control.reconnect_requested is True


@pytest.mark.asyncio
async def test_process_upstream_websocket_text_does_not_match_foreign_response_id_to_only_pending_request(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    account = _make_account("acc_ws_pending")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)

    pending_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_pending",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_a",
    )
    pending_requests = deque([pending_request])
    payload = {
        "type": "response.completed",
        "response": {
            "id": "resp_ws_b",
            "usage": {"input_tokens": 7, "output_tokens": 11, "total_tokens": 18},
        },
    }

    await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
    )

    finalize_request_state.assert_not_awaited()
    assert list(pending_requests) == [pending_request]


@pytest.mark.asyncio
async def test_stream_responses_budget_exhaustion_emits_timeout_event(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    runtime_values = dict(settings.__dict__)
    runtime_values["proxy_request_budget_seconds"] = 0.0
    runtime_settings = SimpleNamespace(**runtime_values)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["error_message"] == "Proxy request budget exhausted"
    assert request_logs.calls[0]["account_id"] is None
    assert request_logs.calls[0]["transport"] == "http"


@pytest.mark.asyncio
async def test_stream_selection_budget_exhaustion_emits_timeout_event(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(
            side_effect=proxy_module.ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "Proxy request budget exhausted"),
            )
        ),
    )

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["error_message"] == "Proxy request budget exhausted"
    assert request_logs.calls[0]["account_id"] is None


@pytest.mark.asyncio
async def test_stream_refresh_timeout_emits_upstream_unavailable_and_logs(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_refresh_timeout")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )

    async def failing_ensure_fresh(account, *, force: bool = False, timeout_seconds: float | None = None):
        raise asyncio.TimeoutError

    monkeypatch.setattr(service, "_ensure_fresh", failing_ensure_fresh)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_unavailable"
    assert event["response"]["error"]["message"] == "Request to upstream timed out"
    assert request_logs.calls[-1]["account_id"] == account.id
    assert request_logs.calls[-1]["status"] == "error"
    assert request_logs.calls[-1]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[-1]["error_message"] == "Request to upstream timed out"


@pytest.mark.asyncio
async def test_stream_forced_refresh_timeout_emits_upstream_unavailable_and_logs(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_forced_refresh_timeout")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )

    async def fake_ensure_fresh(account, *, force: bool = False, timeout_seconds: float | None = None):
        if force:
            raise asyncio.TimeoutError
        return account

    async def failing_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        raise proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "token expired"))
        if False:
            yield ""

    monkeypatch.setattr(service, "_ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(proxy_service, "core_stream_responses", failing_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_unavailable"
    assert event["response"]["error"]["message"] == "Request to upstream timed out"
    assert request_logs.calls[-1]["account_id"] == account.id
    assert request_logs.calls[-1]["status"] == "error"
    assert request_logs.calls[-1]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[-1]["error_message"] == "Request to upstream timed out"


@pytest.mark.asyncio
async def test_stream_refresh_budget_is_recomputed_after_selection(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_refresh_budget")
    captured: dict[str, float | None] = {}

    runtime_values = dict(settings.__dict__)
    runtime_values["proxy_request_budget_seconds"] = 10.0
    runtime_settings = SimpleNamespace(**runtime_values)
    monotonic_calls = {"count": 0}

    def fake_monotonic():
        monotonic_calls["count"] += 1
        return 100.0 if monotonic_calls["count"] < 4 else 107.0

    async def fake_ensure_fresh(account, *, force: bool = False, timeout_seconds: float | None = None):
        captured["timeout_seconds"] = timeout_seconds
        return account

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.completed","response":{"id":"resp_budget"}}\n\n'

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.completed"
    assert captured["timeout_seconds"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_stream_attempt_timeout_overrides_follow_remaining_budget(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_attempt_budget")
    overrides: list[dict[str, float | None]] = []

    remaining_budget_values = iter((10.0, 10.0, 3.0))

    def fake_remaining_budget(deadline: float) -> float:
        del deadline
        try:
            return next(remaining_budget_values)
        except StopIteration:
            return 3.0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.completed","response":{"id":"resp_budget"}}\n\n'

    def fake_push_stream_timeout_overrides(
        *,
        connect_timeout_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
        total_timeout_seconds: float | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        overrides.append(
            {
                "connect": connect_timeout_seconds,
                "idle": idle_timeout_seconds,
                "total": total_timeout_seconds,
            }
        )
        return (None, None, None)

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_remaining_budget_seconds", fake_remaining_budget)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))
    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_service, "push_stream_timeout_overrides", fake_push_stream_timeout_overrides)
    monkeypatch.setattr(proxy_service, "pop_stream_timeout_overrides", lambda tokens: None)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.completed"
    assert overrides == [{"connect": 3.0, "idle": 3.0, "total": 3.0}]


@pytest.mark.asyncio
async def test_stream_forced_refresh_reapplies_idle_and_total_budget_overrides(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_forced_refresh_budget")
    overrides: list[dict[str, float | None]] = []
    stream_call_count = {"count": 0}

    remaining_budget_values = iter((10.0, 10.0, 10.0, 6.0, 2.0))

    def fake_remaining_budget(deadline: float) -> float:
        del deadline
        try:
            return next(remaining_budget_values)
        except StopIteration:
            return 2.0

    async def fake_ensure_fresh(account, *, force: bool = False, timeout_seconds: float | None = None):
        del timeout_seconds
        return account

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        stream_call_count["count"] += 1
        if stream_call_count["count"] == 1:
            raise proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "token expired"))
        yield 'data: {"type":"response.completed","response":{"id":"resp_retry"}}\n\n'

    def fake_push_stream_timeout_overrides(
        *,
        connect_timeout_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
        total_timeout_seconds: float | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        overrides.append(
            {
                "connect": connect_timeout_seconds,
                "idle": idle_timeout_seconds,
                "total": total_timeout_seconds,
            }
        )
        return (None, None, None)

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_remaining_budget_seconds", fake_remaining_budget)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))
    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_service, "push_stream_timeout_overrides", fake_push_stream_timeout_overrides)
    monkeypatch.setattr(proxy_service, "pop_stream_timeout_overrides", lambda tokens: None)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.completed"
    assert len(overrides) == 2
    assert overrides[-1] == {"connect": 2.0, "idle": 2.0, "total": 2.0}
    assert all(override["connect"] == override["idle"] == override["total"] for override in overrides)


@pytest.mark.asyncio
async def test_stream_midstream_generic_failure_is_neutral_to_account_health(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_midstream_failure")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield (
            'data: {"type":"response.failed","response":{"error":{"code":"upstream_request_timeout",'
            '"message":"Proxy request budget exhausted"}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    last_event = json.loads(chunks[-1].split("data: ", 1)[1])
    assert last_event["type"] == "response.failed"
    assert last_event["response"]["error"]["code"] == "upstream_request_timeout"
    record_error.assert_not_awaited()
    record_success.assert_not_awaited()
    assert request_logs.calls[0]["account_id"] == account.id
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"


@pytest.mark.asyncio
async def test_stream_incomplete_records_success_without_account_error(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_incomplete_stream")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            'data: {"type":"response.incomplete","response":{"status":"incomplete","usage":'
            '{"input_tokens":1,"output_tokens":1},"incomplete_details":{"reason":"max_output_tokens"}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.incomplete"
    record_success.assert_awaited_once_with(account)
    record_error.assert_not_awaited()
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] is None


@pytest.mark.asyncio
async def test_compact_responses_budget_exhaustion_returns_upstream_unavailable(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_budget")

    runtime_values = dict(settings.__dict__)
    runtime_values["compact_request_budget_seconds"] = 0.0
    runtime_settings = SimpleNamespace(**runtime_values)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))

    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.compact_responses(payload, {"session_id": "sid-compact"})

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["transport"] == "http"


@pytest.mark.asyncio
async def test_compact_responses_records_transient_error_for_generic_upstream_failure(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_error")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))

    async def failing_compact(payload, headers, access_token, account_id):
        raise proxy_module.ProxyResponseError(502, openai_error("upstream_unavailable", "late"))

    monkeypatch.setattr(proxy_service, "core_compact_responses", failing_compact)

    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.compact_responses(payload, {"session_id": "sid-compact"})

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    record_error.assert_awaited_once_with(account)
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_selection_budget_exhaustion_returns_upstream_unavailable(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(side_effect=proxy_module.ProxyResponseError(502, openai_error("upstream_unavailable", "late"))),
    )

    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.compact_responses(payload, {"session_id": "sid-compact"})

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_transcribe_budget_exhaustion_blocks_401_retry(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_transcribe_budget")
    transcribe_calls = 0

    runtime_values = dict(settings.__dict__)
    runtime_values["transcription_request_budget_seconds"] = 1.0
    runtime_settings = SimpleNamespace(**runtime_values)
    monotonic_calls = {"count": 0}

    def fake_monotonic():
        monotonic_calls["count"] += 1
        return 100.0 if monotonic_calls["count"] < 7 else 102.0

    async def fake_transcribe(
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        access_token: str,
        account_id: str | None,
        base_url=None,
        session=None,
    ):
        nonlocal transcribe_calls
        transcribe_calls += 1
        raise proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "token expired"))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(proxy_service, "core_transcribe_audio", fake_transcribe)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.transcribe(
            audio_bytes=b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"session_id": "sid-transcribe"},
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    assert transcribe_calls == 1
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["transport"] == "http"


@pytest.mark.asyncio
async def test_transcribe_selection_budget_exhaustion_returns_upstream_unavailable(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(side_effect=proxy_module.ProxyResponseError(502, openai_error("upstream_unavailable", "late"))),
    )

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.transcribe(
            audio_bytes=b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"session_id": "sid-transcribe"},
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_transcribe_records_transient_error_for_generic_upstream_failure(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_transcribe_error")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))

    async def failing_transcribe(
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        access_token: str,
        account_id: str | None,
        base_url=None,
        session=None,
    ):
        raise proxy_module.ProxyResponseError(502, openai_error("upstream_unavailable", "late"))

    monkeypatch.setattr(proxy_service, "core_transcribe_audio", failing_transcribe)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.transcribe(
            audio_bytes=b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"session_id": "sid-transcribe"},
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    record_error.assert_awaited_once_with(account)
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_responses_propagates_selection_error_code(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(
            return_value=AccountSelection(
                account=None,
                error_message="No accounts with available additional quota for model 'gpt-5.3-codex-spark'",
                error_code="no_additional_quota_eligible_accounts",
            )
        ),
    )

    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.3-codex-spark",
            "instructions": "summarize",
            "input": [],
        }
    )

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.compact_responses(payload, {"session_id": "sid-compact"})

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 503
    assert exc.payload["error"]["code"] == "no_additional_quota_eligible_accounts"
    assert request_logs.calls[0]["error_code"] == "no_additional_quota_eligible_accounts"


def test_settings_parses_image_inline_allowlist_from_csv(monkeypatch):
    monkeypatch.setenv("CODEX_LB_IMAGE_INLINE_ALLOWED_HOSTS", "a.example, b.example ,,C.Example")
    from app.core.config.settings import Settings

    settings = Settings()

    assert settings.image_inline_allowed_hosts == ["a.example", "b.example", "c.example"]


@pytest.mark.asyncio
async def test_transcribe_audio_strips_content_type_case_insensitively():
    response = _TranscribeResponse({"text": "ok"})
    session = _TranscribeSession(response)

    result = await proxy_module.transcribe_audio(
        b"\x01\x02",
        filename="sample.wav",
        content_type="audio/wav",
        prompt="hello",
        headers={
            "content-type": "multipart/form-data; boundary=legacy",
            "X-Request-Id": "req_transcribe_1",
        },
        access_token="token-1",
        account_id="acc_transcribe_1",
        base_url="https://upstream.example",
        session=cast(proxy_module.aiohttp.ClientSession, session),
    )

    assert result == {"text": "ok"}
    assert session.calls
    raw_headers = session.calls[0]["headers"]
    assert isinstance(raw_headers, dict)
    sent_headers = cast(dict[str, str], raw_headers)
    assert all(name.lower() != "content-type" for name in sent_headers)
    assert sent_headers["Authorization"] == "Bearer token-1"
    assert sent_headers["chatgpt-account-id"] == "acc_transcribe_1"


@pytest.mark.asyncio
async def test_transcribe_audio_wraps_timeout_as_upstream_unavailable():
    session = _TimeoutTranscribeSession()

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await proxy_module.transcribe_audio(
            b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"X-Request-Id": "req_transcribe_timeout"},
            access_token="token-1",
            account_id="acc_transcribe_1",
            base_url="https://upstream.example",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    assert exc.payload["error"]["message"] == "Request to upstream timed out"


@pytest.mark.asyncio
async def test_transcribe_audio_honors_timeout_overrides():
    response = _TranscribeResponse({"text": "ok"})
    session = _TranscribeSession(response)

    tokens = proxy_module.push_transcribe_timeout_overrides(connect_timeout_seconds=4.0, total_timeout_seconds=12.0)
    try:
        result = await proxy_module.transcribe_audio(
            b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"X-Request-Id": "req_transcribe_override"},
            access_token="token-1",
            account_id="acc_transcribe_1",
            base_url="https://upstream.example",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    finally:
        proxy_module.pop_transcribe_timeout_overrides(tokens)

    assert result == {"text": "ok"}
    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(12.0)
    assert timeout.sock_connect == pytest.approx(4.0)
    assert timeout.sock_read == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_transcribe_audio_uses_configured_budget_when_no_override(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 5.0
        transcription_request_budget_seconds = 240.0
        log_upstream_request_payload = False

    response = _TranscribeResponse({"text": "ok"})
    session = _TranscribeSession(response)

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    result = await proxy_module.transcribe_audio(
        b"\x01\x02",
        filename="sample.wav",
        content_type="audio/wav",
        prompt=None,
        headers={"X-Request-Id": "req_transcribe_budget"},
        access_token="token-1",
        account_id="acc_transcribe_1",
        base_url="https://upstream.example",
        session=cast(proxy_module.aiohttp.ClientSession, session),
    )

    assert result == {"text": "ok"}
    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(240.0)
    assert timeout.sock_connect == pytest.approx(5.0)
    assert timeout.sock_read == pytest.approx(240.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("json_error", "expected_message"),
    [
        (asyncio.TimeoutError(), "Request to upstream timed out"),
        (proxy_module.aiohttp.ClientPayloadError("payload read failed"), "payload read failed"),
    ],
)
async def test_transcribe_audio_maps_body_read_transport_errors_to_upstream_unavailable(
    json_error: Exception,
    expected_message: str,
):
    response = _TranscribeResponse({"text": "ignored"}, json_error=json_error)
    session = _TranscribeSession(response)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await proxy_module.transcribe_audio(
            b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"X-Request-Id": "req_transcribe_body_read"},
            access_token="token-1",
            account_id="acc_transcribe_1",
            base_url="https://upstream.example",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert exc.payload["error"]["code"] == "upstream_unavailable"
    assert exc.payload["error"]["message"] == expected_message
