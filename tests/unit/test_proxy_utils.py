from __future__ import annotations

import json
import logging

import pytest

import app.core.clients.proxy as proxy_module
from app.core.clients.proxy import _build_upstream_headers, filter_inbound_headers
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesRequest
from app.core.utils.request_id import reset_request_id, set_request_id
from app.modules.proxy import service as proxy_service

pytestmark = pytest.mark.unit


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


def test_settings_parses_image_inline_allowlist_from_csv(monkeypatch):
    monkeypatch.setenv("CODEX_LB_IMAGE_INLINE_ALLOWED_HOSTS", "a.example, b.example ,,C.Example")
    from app.core.config.settings import Settings

    settings = Settings()

    assert settings.image_inline_allowed_hosts == ["a.example", "b.example", "c.example"]
