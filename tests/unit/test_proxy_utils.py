from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Protocol, Self, cast
from unittest.mock import AsyncMock

import anyio
import pytest
from aiohttp.client_reqrep import RequestInfo
from fastapi import WebSocket
from starlette.requests import Request

import app.core.clients.proxy as proxy_module
from app.core.clients.proxy import _build_upstream_headers, filter_inbound_headers
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error
from app.core.openai.models import OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.resilience.circuit_breaker import CircuitState
from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id, reset_request_id, set_request_id
from app.core.utils.sse import parse_sse_data_json
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy import api as proxy_api
from app.modules.proxy import request_policy as proxy_request_policy
from app.modules.proxy import service as proxy_service
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.unit


def _assert_proxy_response_error(exc: BaseException) -> proxy_module.ProxyResponseError:
    assert isinstance(exc, proxy_module.ProxyResponseError)
    return exc


def _proxy_error_code(exc: proxy_module.ProxyResponseError) -> str | None:
    return exc.payload["error"].get("code")


def _proxy_error_message(exc: proxy_module.ProxyResponseError) -> str | None:
    return exc.payload["error"].get("message")


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


def test_apply_api_key_enforcement_overrides_service_tier_aliases_to_priority():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hello",
            "input": [],
            "service_tier": "default",
        }
    )
    api_key = proxy_service.ApiKeyData(
        id="key_1",
        name="service-tier-key",
        key_prefix="sk-clb-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier="priority",
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    proxy_request_policy.apply_api_key_enforcement(payload, api_key)

    assert payload.service_tier == "priority"


class _RingMembershipStub:
    def __init__(self, members: list[str]) -> None:
        self.members = members

    async def list_active(self, stale_threshold_seconds: int = 120) -> list[str]:
        del stale_threshold_seconds
        return list(self.members)


@pytest.mark.anyio
async def test_owner_instance_uses_rendezvous_hash() -> None:
    settings = SimpleNamespace(
        http_responses_session_bridge_instance_id="pod-a",
        http_responses_session_bridge_instance_ring=["pod-a", "pod-b", "pod-c", "pod-d", "pod-e"],
    )
    ring_membership = _RingMembershipStub(["pod-a", "pod-b", "pod-c", "pod-d", "pod-e"])

    owners_before: dict[str, str | None] = {}
    for index in range(1000):
        key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", f"k-{index}", None)
        owners_before[key.affinity_key] = await proxy_service._http_bridge_owner_instance(
            key,
            settings,
            cast(proxy_service.RingMembershipService, ring_membership),
        )

    ring_membership.members = ["pod-a", "pod-b", "pod-c", "pod-d", "pod-e", "pod-f"]
    moved = 0
    for index in range(1000):
        key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", f"k-{index}", None)
        owner_after = await proxy_service._http_bridge_owner_instance(
            key,
            settings,
            cast(proxy_service.RingMembershipService, ring_membership),
        )
        if owners_before[key.affinity_key] != owner_after:
            moved += 1

    assert moved / 1000 <= 0.2


@pytest.mark.anyio
async def test_ring_raises_on_db_error() -> None:
    settings = SimpleNamespace(
        http_responses_session_bridge_instance_id="pod-a",
        http_responses_session_bridge_instance_ring=["pod-b", "pod-c"],
    )
    ring_membership = AsyncMock()
    ring_membership.list_active.side_effect = RuntimeError("db unavailable")

    key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", "k-fallback", None)
    with pytest.raises(RuntimeError, match="db unavailable"):
        await proxy_service._http_bridge_owner_instance(
            key,
            settings,
            cast(proxy_service.RingMembershipService, ring_membership),
        )


def test_build_upstream_websocket_headers_strip_accept_and_content_type_case_insensitively():
    headers = proxy_module._build_upstream_websocket_headers(
        {
            "accept": "text/event-stream",
            "content-type": "application/json",
            "User-Agent": "codex-test",
        },
        "token",
        "acc_2",
    )

    assert all(key.lower() != "accept" for key in headers)
    assert all(key.lower() != "content-type" for key in headers)
    assert headers["Authorization"] == "Bearer token"
    assert headers["chatgpt-account-id"] == "acc_2"
    assert headers["User-Agent"] == "codex-test"


def test_build_upstream_websocket_headers_strip_hop_by_hop_headers_and_connection_tokens():
    headers = proxy_module._build_upstream_websocket_headers(
        {
            "Connection": "keep-alive, Upgrade, X-Handshake-Debug",
            "Keep-Alive": "timeout=5",
            "Upgrade": "websocket",
            "Transfer-Encoding": "chunked",
            "Proxy-Connection": "keep-alive",
            "X-Handshake-Debug": "1",
            "User-Agent": "codex-test",
        },
        "token",
        "acc_2",
    )

    assert "Connection" not in headers
    assert "Keep-Alive" not in headers
    assert "Upgrade" not in headers
    assert "Transfer-Encoding" not in headers
    assert "Proxy-Connection" not in headers
    assert "X-Handshake-Debug" not in headers
    assert headers["Authorization"] == "Bearer token"
    assert headers["chatgpt-account-id"] == "acc_2"
    assert headers["User-Agent"] == "codex-test"


def test_has_native_codex_transport_headers_requires_allowlisted_originator():
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_cli_rs"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_exec"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_vscode"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_atlas"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "Codex Desktop"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_chatgpt_desktop"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "Codex Chat"}) is False
    assert proxy_module._has_native_codex_transport_headers({"originator": "Codex QA"}) is False
    assert proxy_module._has_native_codex_transport_headers({"originator": "other-client"}) is False


def test_resolve_stream_transport_does_not_force_websocket_for_custom_codex_originator(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda _model: False),
    )

    transport = proxy_module._resolve_stream_transport(
        transport="auto",
        transport_override=None,
        model="gpt-5.1",
        headers={"originator": "Codex QA"},
    )

    assert transport == "http"


def test_response_create_client_metadata_preserves_existing_json_values_and_turn_metadata():
    payload = {
        "client_metadata": {
            "bool_flag": True,
            "count": 2,
            "nested": {"enabled": False},
            "x-codex-turn-metadata": '{"turn_id":"payload-turn"}',
        }
    }

    metadata = proxy_service._response_create_client_metadata(
        payload,
        headers={"x-codex-turn-metadata": '{"turn_id":"header-turn"}'},
    )

    assert metadata == {
        "bool_flag": True,
        "count": 2,
        "nested": {"enabled": False},
        "x-codex-turn-metadata": '{"turn_id":"payload-turn"}',
    }


def test_response_create_client_metadata_reads_turn_metadata_case_insensitively():
    metadata = proxy_service._response_create_client_metadata(
        {},
        headers={"X-Codex-Turn-Metadata": '{"turn_id":"header-turn"}'},
    )

    assert metadata == {"x-codex-turn-metadata": '{"turn_id":"header-turn"}'}


def test_has_native_codex_transport_headers_does_not_treat_session_id_as_websocket_signal():
    assert proxy_module._has_native_codex_transport_headers({"session_id": "sid_123"}) is False


def test_has_native_codex_transport_headers_still_accepts_explicit_native_stream_headers_without_originator():
    assert proxy_module._has_native_codex_transport_headers({"x-codex-turn-metadata": "1"}) is True
    assert proxy_module._has_native_codex_transport_headers({"x-codex-beta-features": "repl"}) is True


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


class _DummyChunkIterator:
    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _DummyContent(proxy_module.SSEContentProtocol):
    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = list(chunks)

    def iter_chunked(self, size: int) -> _DummyChunkIterator:
        del size
        return _DummyChunkIterator(self._chunks)


class _DummyResponse(proxy_module.SSEResponseProtocol):
    content: proxy_module.SSEContentProtocol

    def __init__(self, chunks: Sequence[bytes]) -> None:
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
        self._repos = ProxyRepositories(
            accounts=cast(AccountsRepository, AsyncMock()),
            usage=cast(UsageRepository, AsyncMock()),
            request_logs=cast(RequestLogsRepository, request_logs),
            sticky_sessions=cast(StickySessionsRepository, AsyncMock()),
            api_keys=cast(ApiKeysRepository, AsyncMock()),
            additional_usage=cast(AdditionalUsageRepository, AsyncMock()),
        )

    async def __aenter__(self) -> ProxyRepositories:
        return self._repos

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _repo_factory(request_logs: _RequestLogsRecorder) -> proxy_service.ProxyRepoFactory:
    def factory() -> _RepoContext:
        return _RepoContext(request_logs)

    return factory


def _make_proxy_settings(*, log_proxy_service_tier_trace: bool) -> object:
    return SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        sticky_reallocation_budget_threshold_pct=95.0,
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
        log_proxy_service_tier_trace=log_proxy_service_tier_trace,
        request_visibility_mode="off",
        request_visibility_expires_at=None,
    )


@pytest.fixture(autouse=True)
def _install_default_proxy_runtime_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)


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
        status: int

        async def __aenter__(self) -> Self: ...

        async def __aexit__(self, exc_type: object | None, exc: BaseException | None, tb: object | None) -> bool: ...

        async def json(self, *, content_type: str | None = None) -> dict[str, object]: ...

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


class _TimeoutCompactSession:
    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        raise asyncio.TimeoutError


class _WsConnection:
    def __init__(self, messages: Sequence[object]) -> None:
        self._messages = list(messages)
        self.sent_json: list[dict[str, object]] = []
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object | None, exc: BaseException | None, tb: object | None) -> bool:
        self.closed = True
        return False

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent_json.append(payload)

    async def receive(self):
        if self._messages:
            return self._messages.pop(0)
        return SimpleNamespace(type=proxy_module.aiohttp.WSMsgType.CLOSE, data=None, extra=None)

    async def close(self) -> None:
        self.closed = True


def _ws_text_message(payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        type=proxy_module.aiohttp.WSMsgType.TEXT,
        data=json.dumps(payload, separators=(",", ":")),
        extra=None,
    )


class _WsResponse:
    def __init__(self, messages: Sequence[object], *, status: int = 101) -> None:
        self._messages = messages
        self._index = 0
        self._response = SimpleNamespace(status=status)
        self.closed = False
        self.sent_json: list[dict[str, object]] = []
        self.sent: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object | None, exc: BaseException | None, tb: object | None) -> bool:
        self.closed = True
        return False

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self):
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        message = self._messages[self._index]
        self._index += 1
        return message

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent_json.append(payload)

    async def send_str(self, data: str) -> None:
        self.sent.append(data)
        self.sent_json.append(json.loads(data))

    async def receive(self):
        if self._index >= len(self._messages):
            return _WsMessage(proxy_module.aiohttp.WSMsgType.CLOSED)
        message = self._messages[self._index]
        self._index += 1
        return message

    async def close(self) -> None:
        self.closed = True

    def exception(self):
        return None


class _WsMessage:
    def __init__(self, msg_type, data=None) -> None:
        self.type = msg_type
        self.data = data


class _WsSession:
    def __init__(
        self,
        response: _WsResponse | _WsConnection,
        sse_response: _SsePostResponse | None = None,
    ) -> None:
        self._response = response
        self._sse_response = sse_response
        self.ws_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    def ws_connect(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout=None,
        receive_timeout=None,
        heartbeat=None,
        autoclose=True,
        autoping=True,
        max_msg_size=None,
    ):
        self.ws_calls.append(
            {
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "receive_timeout": receive_timeout,
                "heartbeat": heartbeat,
                "autoclose": autoclose,
                "autoping": autoping,
                "max_msg_size": max_msg_size,
            }
        )
        return self._response

    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.post_calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self._sse_response is None:
            raise AssertionError("HTTP POST path should not be used in websocket mode")
        return self._sse_response


@pytest.mark.asyncio
async def test_iter_sse_events_handles_large_single_line_without_chunk_too_big():
    large_data = "A" * (200 * 1024)
    event = f'data: {{"type":"response.output_text.delta","delta":"{large_data}"}}\n\n'.encode("utf-8")
    response = _DummyResponse([event[:4096], event[4096:]])
    stream = proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, response), 1.0, 512 * 1024)

    chunks = [chunk async for chunk in stream]

    assert len(chunks) == 1
    assert chunks[0].startswith("data: ")
    assert chunks[0].endswith("\n\n")


@pytest.mark.asyncio
async def test_iter_sse_events_raises_on_event_size_limit():
    large_data = b"A" * 1024
    response = _DummyResponse([b"data: ", large_data])

    with pytest.raises(proxy_module.StreamEventTooLargeError):
        async for _ in proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, response), 1.0, 256):
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
        async for _ in proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, response), 1.0, 1024):
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
        async for _ in proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, _TimeoutResponse()), 1.0, 1024):
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
        async for _ in proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, response), 10.0, 1024):
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


def test_log_proxy_request_shape_includes_affinity_metadata(monkeypatch, caplog):
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "tools": [{"type": "function", "name": "b_tool"}, {"type": "function", "name": "a_tool"}],
        }
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = True
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_shape_1")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_request_shape(
            "stream",
            payload,
            {"session_id": "sid_1"},
            sticky_kind="codex_session",
            sticky_key_source="session_header",
            prompt_cache_key_set=True,
        )
    finally:
        reset_request_id(token)

    assert "proxy_request_shape" in caplog.text
    assert "sticky_kind=codex_session" in caplog.text
    assert "sticky_key_source=session_header" in caplog.text
    assert "prompt_cache_key_set=True" in caplog.text
    assert "session_header_present=True" in caplog.text
    assert "tools_hash=sha256:" in caplog.text


def test_log_proxy_request_shape_hashes_prompt_cache_key_without_raw_value(monkeypatch, caplog):
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "prompt_cache_key": "thread_secret_123",
        }
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = True
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_shape_2")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_request_shape(
            "stream",
            payload,
            {},
            sticky_kind="prompt_cache",
            sticky_key_source="payload",
            prompt_cache_key_set=True,
        )
    finally:
        reset_request_id(token)

    assert "prompt_cache_key=sha256:" in caplog.text
    assert "thread_secret_123" not in caplog.text


def test_log_proxy_request_shape_reports_derived_key_after_affinity_resolution(monkeypatch, caplog):
    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = True
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False
        openai_prompt_cache_key_derivation_enabled = True

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
        }
    )
    proxy_service._sticky_key_for_responses_request(
        payload,
        headers={"session_id": "sid_1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    token = set_request_id("req_shape_3")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_request_shape(
            "stream",
            payload,
            {"session_id": "sid_1"},
            sticky_kind="codex_session",
            sticky_key_source="session_header",
            prompt_cache_key_set=True,
        )
    finally:
        reset_request_id(token)

    assert "prompt_cache_key=sha256:" in caplog.text
    assert "prompt_cache_key_raw=None" in caplog.text


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
        payload_json = '{"model":"gpt-5.4","input":"hi"}'
        proxy_module._maybe_log_upstream_request_start(
            kind="responses",
            url="https://chatgpt.com/backend-api/codex/responses",
            headers=headers,
            method="POST",
            payload_summary="model=gpt-5.4 stream=True input=str keys=['input','model','stream']",
            payload_json=payload_json,
        )
        proxy_module._maybe_log_upstream_request_complete(
            kind="responses",
            url="https://chatgpt.com/backend-api/codex/responses",
            headers=headers,
            method="POST",
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
        upstream_stream_transport = "http"

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
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
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
        proxy_request_budget_seconds = 75.0
        upstream_stream_transport = "http"

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
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
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
        upstream_stream_transport = "http"

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
        upstream_stream_transport = "http"

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
async def test_stream_responses_uses_native_websocket_upstream_for_codex_headers(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024 * 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 15.0
        upstream_stream_transport = "auto"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "stream": True,
            "service_tier": "priority",
        }
    )
    websocket = _WsConnection(
        [
            _ws_text_message(
                {
                    "type": "response.created",
                    "response": {"id": "resp_ws_1", "status": "in_progress", "service_tier": "auto"},
                }
            ),
            _ws_text_message(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_ws_1", "status": "completed", "service_tier": "default"},
                }
            ),
        ]
    )
    session = _WsSession(websocket)

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={
                "originator": "codex_cli_rs",
                "session_id": "sid-native",
                "x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"none"}',
                "x-codex-beta-features": "js_repl,multi_agent",
                "user-agent": "codex_cli_rs/0.114.0",
            },
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert len(session.ws_calls) == 1
    assert session.post_calls == []
    assert session.ws_calls[0]["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    headers = cast(dict[str, str], session.ws_calls[0]["headers"])
    assert headers is not None
    assert headers["Authorization"] == "Bearer token"
    assert headers["chatgpt-account-id"] == "acc_1"
    assert headers["originator"] == "codex_cli_rs"
    assert "Content-Type" not in headers
    assert "Accept" not in headers
    expected_request_payload = {
        "type": "response.create",
        **{k: v for k, v in payload.to_payload().items() if k != "stream"},
    }
    assert websocket.sent_json == [expected_request_payload]
    assert len(events) == 2
    created = parse_sse_event(events[0])
    completed = parse_sse_event(events[1])
    created_payload = parse_sse_data_json(events[0])
    completed_payload = parse_sse_data_json(events[1])
    assert created is not None
    assert completed is not None
    assert created.response is not None
    assert completed.response is not None
    created_response = cast(dict[str, object], cast(dict[str, object], created_payload)["response"])
    completed_response = cast(dict[str, object], cast(dict[str, object], completed_payload)["response"])
    assert created_response["service_tier"] == "auto"
    assert completed_response["service_tier"] == "default"


@pytest.mark.asyncio
async def test_stream_responses_falls_back_to_http_post_without_native_codex_headers(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 15.0
        upstream_stream_transport = "http"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _WsSession(
        _WsConnection([]),
        sse_response=_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']),
    )

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

    assert session.ws_calls == []
    assert len(session.post_calls) == 1
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']


@pytest.mark.asyncio
async def test_stream_responses_uses_websocket_transport(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    messages = [
        SimpleNamespace(
            type=proxy_module.aiohttp.WSMsgType.TEXT,
            data='{"type":"response.created","response":{"id":"resp_ws","service_tier":"auto"}}',
        ),
        SimpleNamespace(
            type=proxy_module.aiohttp.WSMsgType.TEXT,
            data='{"type":"response.completed","response":{"id":"resp_ws","service_tier":"default"}}',
        ),
    ]
    websocket = _WsResponse(messages)
    session = _WsSession(websocket)
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={"originator": "codex_cli_rs", "session_id": "sid_ws"},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.ws_calls[0]["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    request_payload = websocket.sent_json[0]
    expected_request_payload = {
        "type": "response.create",
        **{k: v for k, v in payload.to_payload().items() if k != "stream"},
    }
    assert request_payload == expected_request_payload
    expected_created = (
        "event: response.created\ndata: "
        '{"type":"response.created","response":{"id":"resp_ws","service_tier":"auto"}}\n\n'
    )
    expected_completed = (
        "event: response.completed\ndata: "
        '{"type":"response.completed","response":{"id":"resp_ws","service_tier":"default"}}\n\n'
    )
    assert events == [
        expected_created,
        expected_completed,
    ]


@pytest.mark.asyncio
async def test_stream_responses_websocket_forces_response_create_event_type(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "type": "response.cancel",
            "custom_flag": "x",
        }
    )
    websocket = _WsResponse(
        [
            _WsMessage(
                proxy_module.aiohttp.WSMsgType.TEXT,
                json.dumps({"type": "response.completed", "response": {"id": "resp_ws"}}),
            )
        ]
    )
    session = _WsSession(websocket)

    _ = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    request_payload = websocket.sent_json[0]
    assert payload.to_payload()["type"] == "response.cancel"
    assert request_payload["type"] == "response.create"
    assert request_payload["custom_flag"] == "x"


@pytest.mark.asyncio
async def test_stream_responses_websocket_omits_http_only_transport_fields(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "stream": True,
            "background": True,
            "custom_flag": "x",
        }
    )
    websocket = _WsResponse(
        [
            _WsMessage(
                proxy_module.aiohttp.WSMsgType.TEXT,
                json.dumps({"type": "response.completed", "response": {"id": "resp_ws"}}),
            )
        ]
    )
    session = _WsSession(websocket)

    _ = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    request_payload = websocket.sent_json[0]
    assert request_payload["type"] == "response.create"
    assert request_payload["custom_flag"] == "x"
    assert "stream" not in request_payload
    assert "background" not in request_payload


@pytest.mark.asyncio
async def test_stream_responses_via_websocket_counts_connect_and_send_against_total_timeout(monkeypatch):
    recorded: dict[str, float | None] = {}
    websocket = _WsResponse([])
    monotonic_values = iter([100.0, 100.0, 104.75, 104.75, 104.75, 104.75])

    def fake_monotonic() -> float:
        return next(monotonic_values, 104.75)

    async def fake_open_upstream_websocket(
        *,
        session,
        url: str,
        headers,
        connect_timeout_seconds: float,
        max_msg_size: int,
        account_id: str | None = None,
        hold_half_open_probe: bool = False,
    ):
        del session, url, headers, max_msg_size, account_id, hold_half_open_probe
        recorded["connect_timeout_seconds"] = connect_timeout_seconds
        return websocket, websocket

    async def fake_stream_websocket_events(
        websocket_obj,
        *,
        idle_timeout_seconds: float,
        total_timeout_seconds: float | None,
        max_event_bytes: int,
    ):
        recorded["total_timeout_seconds"] = total_timeout_seconds
        if False:
            yield ""

    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_stream_websocket_events", fake_stream_websocket_events)
    monkeypatch.setattr(proxy_module.time, "monotonic", fake_monotonic)

    events = [
        event
        async for event in proxy_module._stream_responses_via_websocket(
            payload_dict={"model": "gpt-5.1", "type": "response.cancel"},
            url="https://chatgpt.com/backend-api/codex/responses",
            headers={"originator": "codex_cli_rs"},
            client_session=cast(proxy_module.aiohttp.ClientSession, SimpleNamespace()),
            effective_total_timeout=5.0,
            effective_connect_timeout=8.0,
            effective_idle_timeout=45.0,
            max_event_bytes=1024,
            raise_for_status=True,
        )
    ]

    assert events == []
    assert recorded["connect_timeout_seconds"] == pytest.approx(5.0)
    assert recorded["total_timeout_seconds"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_open_upstream_websocket_preserves_error_body_on_handshake_failure():
    error_body = json.dumps(
        {"error": {"message": "quota exhausted", "type": "server_error", "code": "insufficient_quota"}}
    )

    class _HandshakeFailureResponse:
        def __init__(self) -> None:
            self.status = 403
            self.headers = {}
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = None
            self.closed = False

        async def text(self) -> str:
            return error_body

        def close(self) -> None:
            self.closed = True

    class _HandshakeFailureSession:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()
            self._ws_response_class = proxy_module.aiohttp.ClientWebSocketResponse

        async def request(self, method, url, **kwargs):
            del method, url, kwargs
            return _HandshakeFailureResponse()

    with pytest.raises(proxy_module.aiohttp.WSServerHandshakeError) as exc_info:
        await proxy_module._open_upstream_websocket(
            session=cast(proxy_module.aiohttp.ClientSession, _HandshakeFailureSession()),
            url="wss://chatgpt.com/backend-api/codex/responses",
            headers={"Authorization": "Bearer token"},
            connect_timeout_seconds=8.0,
            max_msg_size=1024,
        )

    assert "insufficient_quota" in exc_info.value.message


@pytest.mark.asyncio
async def test_open_upstream_websocket_records_circuit_breaker_failure_on_5xx_handshake(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.CLOSED
            self.failures: list[Exception] = []
            self.successes = 0

        async def pre_call_check(self) -> bool:
            return False

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    class _HandshakeFailureResponse:
        def __init__(self) -> None:
            self.status = 503
            self.headers = {}
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = None

        async def text(self) -> str:
            return "upstream unavailable"

        def close(self) -> None:
            return None

    class _HandshakeFailureSession:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()
            self._ws_response_class = proxy_module.aiohttp.ClientWebSocketResponse

        async def request(self, method, url, **kwargs):
            del method, url, kwargs
            return _HandshakeFailureResponse()

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)

    with pytest.raises(proxy_module.aiohttp.WSServerHandshakeError):
        await proxy_module._open_upstream_websocket(
            session=cast(proxy_module.aiohttp.ClientSession, _HandshakeFailureSession()),
            url="wss://chatgpt.com/backend-api/codex/responses",
            headers={"Authorization": "Bearer token"},
            connect_timeout_seconds=8.0,
            max_msg_size=1024,
            account_id="acc_test",
        )

    assert cb.successes == 0
    assert len(cb.failures) == 1
    assert str(cb.failures[0]) == "WebSocket handshake failed: HTTP 503"


@pytest.mark.asyncio
async def test_open_upstream_websocket_records_circuit_breaker_success_after_valid_handshake(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.CLOSED
            self.failures: list[Exception] = []
            self.successes = 0

        async def pre_call_check(self) -> bool:
            return False

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    class _ProtocolStub:
        def __init__(self) -> None:
            self.read_timeout: float | None = 10.0

        def set_parser(self, parser, reader) -> None:
            del parser, reader

    class _ConnectionStub:
        def __init__(self) -> None:
            self.protocol = _ProtocolStub()
            self.transport = object()

    class _HandshakeSuccessResponse:
        def __init__(self, headers: dict[str, str]) -> None:
            self.status = 101
            self.headers = headers
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = _ConnectionStub()

        async def text(self) -> str:
            return ""

        def close(self) -> None:
            return None

    class _HandshakeSuccessSession:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()

            def _build_ws(*args, **kwargs):
                del args, kwargs
                return SimpleNamespace(tag="ws")

            self._ws_response_class = _build_ws

        async def request(self, method, url, **kwargs):
            del method, url
            sec_key = kwargs["headers"][proxy_module.hdrs.SEC_WEBSOCKET_KEY]
            response_key = proxy_module.base64.b64encode(
                proxy_module.hashlib.sha1(sec_key.encode() + proxy_module.WS_KEY).digest()
            ).decode()
            return _HandshakeSuccessResponse(
                {
                    proxy_module.hdrs.UPGRADE: "websocket",
                    proxy_module.hdrs.CONNECTION: "upgrade",
                    proxy_module.hdrs.SEC_WEBSOCKET_ACCEPT: response_key,
                }
            )

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)
    monkeypatch.setattr(proxy_module.aiohttp.client_ws, "WebSocketDataQueue", lambda *args, **kwargs: object())
    monkeypatch.setattr(proxy_module, "WebSocketReader", lambda *args, **kwargs: object())
    monkeypatch.setattr(proxy_module, "WebSocketWriter", lambda *args, **kwargs: object())

    websocket_cm, websocket = await proxy_module._open_upstream_websocket(
        session=cast(proxy_module.aiohttp.ClientSession, _HandshakeSuccessSession()),
        url="wss://chatgpt.com/backend-api/codex/responses",
        headers={"Authorization": "Bearer token"},
        connect_timeout_seconds=8.0,
        max_msg_size=1024,
        account_id="acc_test",
    )

    assert websocket_cm == websocket
    assert cb.failures == []
    assert cb.successes == 0


@pytest.mark.asyncio
async def test_open_upstream_websocket_holds_half_open_probe_until_lifecycle_finishes(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.CLOSED
            self.failures: list[Exception] = []
            self.successes = 0
            self.release_calls = 0

        async def pre_call_check(self) -> bool:
            return True

        async def release_half_open_probe(self) -> None:
            self.release_calls += 1

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    class _ProtocolStub:
        def __init__(self) -> None:
            self.read_timeout: float | None = 10.0

        def set_parser(self, parser, reader) -> None:
            del parser, reader

    class _ConnectionStub:
        def __init__(self) -> None:
            self.protocol = _ProtocolStub()
            self.transport = object()

    class _HandshakeSuccessResponse:
        def __init__(self, headers: dict[str, str]) -> None:
            self.status = 101
            self.headers = headers
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = _ConnectionStub()

        async def text(self) -> str:
            return ""

        def close(self) -> None:
            return None

    class _HandshakeSuccessSession:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()

            def _build_ws(*args, **kwargs):
                del args, kwargs
                return SimpleNamespace(tag="ws")

            self._ws_response_class = _build_ws

        async def request(self, method, url, **kwargs):
            del method, url
            sec_key = kwargs["headers"][proxy_module.hdrs.SEC_WEBSOCKET_KEY]
            response_key = proxy_module.base64.b64encode(
                proxy_module.hashlib.sha1(sec_key.encode() + proxy_module.WS_KEY).digest()
            ).decode()
            return _HandshakeSuccessResponse(
                {
                    proxy_module.hdrs.UPGRADE: "websocket",
                    proxy_module.hdrs.CONNECTION: "upgrade",
                    proxy_module.hdrs.SEC_WEBSOCKET_ACCEPT: response_key,
                }
            )

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)
    monkeypatch.setattr(proxy_module.aiohttp.client_ws, "WebSocketDataQueue", lambda *args, **kwargs: object())
    monkeypatch.setattr(proxy_module, "WebSocketReader", lambda *args, **kwargs: object())
    monkeypatch.setattr(proxy_module, "WebSocketWriter", lambda *args, **kwargs: object())

    _, websocket = await proxy_module._open_upstream_websocket(
        session=cast(proxy_module.aiohttp.ClientSession, _HandshakeSuccessSession()),
        url="wss://chatgpt.com/backend-api/codex/responses",
        headers={"Authorization": "Bearer token"},
        connect_timeout_seconds=8.0,
        max_msg_size=1024,
        account_id="acc_test",
        hold_half_open_probe=True,
    )

    assert cb.release_calls == 0
    assert getattr(websocket, "_codex_lb_half_open_probe_held", False) is True


@pytest.mark.asyncio
async def test_stream_responses_websocket_records_circuit_breaker_success_after_terminal_event(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False
        circuit_breaker_enabled = True

    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.failures: list[Exception] = []
            self.successes = 0

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    websocket = _WsResponse(
        [
            _WsMessage(
                proxy_module.aiohttp.WSMsgType.TEXT,
                json.dumps({"type": "response.completed", "response": {"id": "resp_ws"}}),
            )
        ]
    )
    breaker = _CircuitBreakerStub()
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    async def fake_open_upstream_websocket(**kwargs):
        del kwargs
        return websocket, websocket

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: breaker)
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    _ = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _WsSession(websocket)),
        )
    ]

    assert breaker.successes == 1
    assert breaker.failures == []


@pytest.mark.asyncio
async def test_stream_responses_websocket_records_circuit_breaker_failure_when_stream_closes_without_terminal(
    monkeypatch,
):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False
        circuit_breaker_enabled = True

    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.failures: list[Exception] = []
            self.successes = 0

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    websocket = _WsResponse([])
    breaker = _CircuitBreakerStub()
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    async def fake_open_upstream_websocket(**kwargs):
        del kwargs
        return websocket, websocket

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: breaker)
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _WsSession(websocket)),
        )
    ]

    assert breaker.successes == 0
    assert len(breaker.failures) == 1
    assert any("stream_incomplete" in event for event in events)


@pytest.mark.asyncio
async def test_open_upstream_websocket_raises_when_circuit_breaker_is_open(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.OPEN
            self.failures: list[Exception] = []
            self.successes = 0

        async def pre_call_check(self) -> bool:
            from app.core.resilience.circuit_breaker import CircuitBreakerOpenError

            raise CircuitBreakerOpenError("Circuit breaker is OPEN")

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    called = False

    class _Session:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()
            self._ws_response_class = proxy_module.aiohttp.ClientWebSocketResponse

        async def request(self, method, url, **kwargs):
            nonlocal called
            called = True
            del method, url, kwargs
            raise AssertionError("request should not be called when circuit is open")

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)

    with pytest.raises(proxy_module.CircuitBreakerOpenError):
        await proxy_module._open_upstream_websocket(
            session=cast(proxy_module.aiohttp.ClientSession, _Session()),
            url="wss://chatgpt.com/backend-api/codex/responses",
            headers={"Authorization": "Bearer token"},
            connect_timeout_seconds=8.0,
            max_msg_size=1024,
            account_id="acc_test",
        )

    assert called is False
    assert cb.failures == []
    assert cb.successes == 0


@pytest.mark.asyncio
async def test_open_upstream_websocket_malformed_101_records_failure(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.CLOSED
            self.failures: list[Exception] = []
            self.successes = 0

        async def pre_call_check(self) -> bool:
            return False

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    class _Malformed101Response:
        def __init__(self) -> None:
            self.status = 101
            self.headers = {"Upgrade": "WRONG", "Connection": "Upgrade"}
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = None

        async def text(self) -> str:
            return ""

        def close(self) -> None:
            return None

    class _Malformed101Session:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()
            self._ws_response_class = proxy_module.aiohttp.ClientWebSocketResponse

        async def request(self, method, url, **kwargs):
            del method, url, kwargs
            return _Malformed101Response()

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)

    with pytest.raises(proxy_module.aiohttp.WSServerHandshakeError):
        await proxy_module._open_upstream_websocket(
            session=cast(proxy_module.aiohttp.ClientSession, _Malformed101Session()),
            url="wss://chatgpt.com/backend-api/codex/responses",
            headers={"Authorization": "Bearer token"},
            connect_timeout_seconds=8.0,
            max_msg_size=1024,
            account_id="acc_test",
        )

    assert cb.successes == 0
    assert len(cb.failures) == 1


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_uses_model_preference(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    registry = SimpleNamespace(
        get_snapshot=lambda: SimpleNamespace(models={"gpt-5.4": SimpleNamespace(prefer_websockets=True)})
    )

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: registry)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    websocket = _WsResponse(
        [
            SimpleNamespace(
                type=proxy_module.aiohttp.WSMsgType.TEXT,
                data='{"type":"response.completed","response":{"id":"resp_auto"}}',
            )
        ]
    )
    session = _WsSession(websocket)
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

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

    assert session.ws_calls
    assert events == [
        'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_auto"}}\n\n'
    ]


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_uses_bootstrap_model_preference_when_registry_unloaded(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda model: model == "gpt-5.4"),
    )
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    websocket = _WsResponse(
        [
            SimpleNamespace(
                type=proxy_module.aiohttp.WSMsgType.TEXT,
                data='{"type":"response.completed","response":{"id":"resp_auto_bootstrap"}}',
            )
        ]
    )
    session = _WsSession(websocket)
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

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

    assert session.ws_calls
    assert not getattr(session, "post_calls", [])
    assert events == [
        'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_auto_bootstrap"}}\n\n'
    ]


@pytest.mark.asyncio
async def test_stream_responses_http_transport_keeps_http(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False
        upstream_stream_transport = "http"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda model: model == "gpt-5.4"),
    )
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(
        _SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http_legacy"}}\n\n'])
    )
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

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

    assert session.calls
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_http_legacy"}}\n\n']


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_keeps_http_for_bare_session_affinity(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    registry = SimpleNamespace(get_snapshot=lambda: SimpleNamespace(models={}))

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: registry)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']))
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={"session_id": "sid-affinity-only"},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.calls
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_falls_back_to_http_when_websocket_upgrade_required(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    registry = SimpleNamespace(
        get_snapshot=lambda: SimpleNamespace(models={"gpt-5.4": SimpleNamespace(prefer_websockets=True)})
    )
    attempts = {"websocket": 0}
    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))

    async def fake_open_upstream_websocket(**kwargs):
        attempts["websocket"] += 1
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=426, message="Upgrade Required")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: registry)
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']))
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

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

    assert attempts["websocket"] == 1
    assert session.calls
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_does_not_hide_forbidden_websocket_handshake(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    registry = SimpleNamespace(
        get_snapshot=lambda: SimpleNamespace(models={"gpt-5.4": SimpleNamespace(prefer_websockets=True)})
    )
    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))

    async def fake_open_upstream_websocket(**kwargs):
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=403, message="Forbidden")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: registry)
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']))
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

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

    assert not session.calls
    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_stream_responses_uses_websocket_upstream_when_forced(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0
        upstream_stream_transport = "websocket"
        upstream_websocket_mode = "force"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "service_tier": "priority",
        }
    )
    messages = [
        _WsMessage(
            proxy_module.aiohttp.WSMsgType.TEXT,
            json.dumps({"type": "response.created", "response": {"id": "resp_ws", "service_tier": "auto"}}),
        ),
        _WsMessage(
            proxy_module.aiohttp.WSMsgType.TEXT,
            json.dumps({"type": "response.completed", "response": {"id": "resp_ws", "service_tier": "default"}}),
        ),
    ]
    response = _WsResponse(messages)
    session = _WsSession(response)

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={"originator": "Codex Desktop", "session_id": "sid-1"},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert not session.post_calls
    assert session.ws_calls
    assert session.ws_calls[0]["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    expected_payload = {"type": "response.create", **payload.to_payload()}
    expected_payload.pop("stream", None)
    assert response.sent_json == [expected_payload]
    expected_created = (
        "event: response.created\ndata: "
        '{"type":"response.created","response":{"id":"resp_ws","service_tier":"auto"}}\n\n'
    )
    expected_completed = (
        "event: response.completed\ndata: "
        '{"type":"response.completed","response":{"id":"resp_ws","service_tier":"default"}}\n\n'
    )
    assert events == [
        expected_created,
        expected_completed,
    ]


@pytest.mark.asyncio
async def test_stream_responses_forced_websocket_does_not_fallback_on_handshake_rejection(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0

    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))

    async def fake_open_upstream_websocket(**kwargs):
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=403, message="Forbidden")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']))
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

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

    assert not session.calls
    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_stream_responses_forced_websocket_preserves_rate_limit_code_on_handshake_rejection(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0

    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))

    async def fake_open_upstream_websocket(**kwargs):
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=429, message="Too Many Requests")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        _ = [
            event
            async for event in proxy_module.stream_responses(
                payload,
                headers={},
                access_token="token",
                account_id="acc_1",
                session=cast(proxy_module.aiohttp.ClientSession, _SseSession(_SsePostResponse([]))),
                raise_for_status=True,
            )
        ]

    assert _proxy_error_code(exc_info.value) == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_stream_responses_forced_websocket_preserves_quota_code_from_handshake_error_payload(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0

    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))
    error_payload = json.dumps(
        {"error": {"message": "quota exhausted", "type": "server_error", "code": "insufficient_quota"}}
    )

    async def fake_open_upstream_websocket(**kwargs):
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=403, message=error_payload)

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _SseSession(_SsePostResponse([]))),
        )
    ]

    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "insufficient_quota"


@pytest.mark.asyncio
async def test_stream_responses_uses_websocket_upstream_in_auto_mode_for_preferred_model(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0
        upstream_stream_transport = "auto"
        upstream_websocket_mode = "auto"

    snapshot = SimpleNamespace(
        models={
            "gpt-5.4": SimpleNamespace(prefer_websockets=True),
        }
    )

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: SimpleNamespace(get_snapshot=lambda: snapshot))
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
        }
    )
    response = _WsResponse(
        [
            _WsMessage(
                proxy_module.aiohttp.WSMsgType.TEXT,
                json.dumps({"type": "response.completed", "response": {"id": "resp_auto"}}),
            )
        ]
    )
    session = _WsSession(response)

    _ = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.ws_calls
    assert not session.post_calls


@pytest.mark.asyncio
async def test_stream_responses_websocket_emits_incomplete_when_upstream_closes_without_terminal(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0
        upstream_stream_transport = "websocket"
        upstream_websocket_mode = "force"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _WsSession(
        _WsResponse(
            [
                _WsMessage(
                    proxy_module.aiohttp.WSMsgType.TEXT,
                    json.dumps({"type": "response.created", "response": {"id": "resp_ws"}}),
                )
            ]
        )
    )

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

    terminal = json.loads(events[-1].split("data: ", 1)[1])
    assert terminal["response"]["error"]["code"] == "stream_incomplete"


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
    session = _CompactSession(
        _JsonCompactResponse(
            {"object": "response.compaction", "compaction_summary": {"encrypted_content": "enc_summary_1"}}
        )
    )

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
    dumped = result.model_dump(mode="json", exclude_none=True)
    assert dumped["object"] == "response.compaction"
    assert dumped["compaction_summary"]["encrypted_content"] == "enc_summary_1"
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
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert _proxy_error_message(exc) == "Timeout on reading data from socket"


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
    session = _CompactSession(
        _JsonCompactResponse(
            {"object": "response.compaction", "compaction_summary": {"encrypted_content": "enc_summary_2"}}
        )
    )

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
    dumped = result.model_dump(mode="json", exclude_none=True)
    assert dumped["object"] == "response.compaction"
    assert dumped["compaction_summary"]["encrypted_content"] == "enc_summary_2"


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


def test_sticky_key_from_session_header_accepts_aliases_in_priority_order():
    assert proxy_service._sticky_key_from_session_header({"session_id": "sid_1"}) == "sid_1"
    assert proxy_service._sticky_key_from_session_header({"x-codex-session-id": "sid_2"}) == "sid_2"
    assert proxy_service._sticky_key_from_session_header({"x-codex-conversation-id": "sid_3"}) == "sid_3"
    assert (
        proxy_service._sticky_key_from_session_header(
            {
                "x-codex-conversation-id": "sid_3",
                "x-codex-session-id": "sid_2",
                "session_id": "sid_1",
            }
        )
        == "sid_1"
    )


def test_sticky_key_for_responses_request_derives_prompt_cache_before_codex_session_return():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.key == "codex-session-1"
    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert isinstance(payload.prompt_cache_key, str)
    assert payload.prompt_cache_key


def test_sticky_key_for_compact_request_derives_prompt_cache_before_codex_session_return():
    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        }
    )

    policy = proxy_service._sticky_key_for_compact_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.key == "codex-session-1"
    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert isinstance(payload.prompt_cache_key, str)
    assert payload.prompt_cache_key


def test_sticky_key_for_responses_request_respects_prompt_cache_derivation_flag(monkeypatch):
    class Settings:
        openai_prompt_cache_key_derivation_enabled = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert payload.prompt_cache_key is None


def test_sticky_key_for_responses_request_preserves_client_supplied_prompt_cache_key_when_flag_off(monkeypatch):
    class Settings:
        openai_prompt_cache_key_derivation_enabled = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
            "prompt_cache_key": "thread_123",
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert payload.prompt_cache_key == "thread_123"


def test_sticky_key_for_responses_request_strips_whitespace_before_accepting_payload_key():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
            "prompt_cache_key": "  thread_123  ",
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.kind == proxy_service.StickySessionKind.PROMPT_CACHE
    assert policy.key == "thread_123"
    assert payload.prompt_cache_key == "thread_123"


def test_sticky_key_for_responses_request_derives_when_payload_key_is_whitespace_only():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
            "prompt_cache_key": "   ",
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.kind == proxy_service.StickySessionKind.PROMPT_CACHE
    assert isinstance(policy.key, str)
    assert policy.key
    assert payload.prompt_cache_key == policy.key


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


@pytest.mark.asyncio
async def test_compact_request_log_omits_visibility_when_flag_disabled(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_visibility_off")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_compact_api_key_usage", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "core_compact_responses",
        AsyncMock(return_value=OpenAIResponsePayload.model_validate({"output": []})),
    )

    payload = ResponsesCompactRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hello"}]}
    )

    await service.compact_responses(payload, {"Authorization": "Bearer secret", "User-Agent": "codex-test"})

    assert request_logs.calls[0]["request_visibility"] is None


@pytest.mark.asyncio
async def test_compact_request_log_persists_redacted_visibility_when_flag_enabled(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.request_visibility_mode = "persistent"
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_visibility_on")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_compact_api_key_usage", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "core_compact_responses",
        AsyncMock(return_value=OpenAIResponsePayload.model_validate({"output": []})),
    )

    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hello"}],
            "service_tier": "priority",
            "reasoning": {"effort": "high", "summary": "detailed"},
            "metadata": {"apiKey": "sk-live", "nested": {"sessionToken": "secret-token"}},
        }
    )

    await service.compact_responses(
        payload,
        {
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "Content-Type": "application/json",
            "Session_Id": "sid-compact",
            "User-Agent": "codex-test",
            "X-Codex-Conversation-Id": "conv_123",
        },
    )

    visibility = json.loads(cast(str, request_logs.calls[0]["request_visibility"]))
    assert visibility["headers"] == {
        "content-type": "application/json",
        "user-agent": "codex-test",
    }
    assert visibility["body"]["metadata"] == {
        "apiKey": "[REDACTED]",
        "nested": {"sessionToken": "[REDACTED]"},
    }
    assert visibility["body"]["instructions"] == "hi"
    assert visibility["body"]["input"] == [{"role": "user", "content": "hello"}]
    assert visibility["body"]["service_tier"] == "priority"
    assert visibility["body"]["reasoning"] == {"effort": "high", "summary": "detailed"}
    assert "authorization" not in visibility["headers"]
    assert "cookie" not in visibility["headers"]
    assert "session_id" not in visibility["headers"]
    assert "x-codex-conversation-id" not in visibility["headers"]


@pytest.mark.asyncio
async def test_stream_request_log_persists_redacted_visibility_when_flag_enabled(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.request_visibility_mode = "persistent"
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_visibility_on")

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
        yield 'data: {"type":"response.completed","response":{"id":"resp_stream_visibility","service_tier":"priority"}}\n\n'

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hello there",
            "input": [{"role": "user", "content": "keep me hidden"}],
            "service_tier": "priority",
            "reasoning": {"effort": "high", "summary": "detailed"},
            "metadata": {"apiKey": "sk-live", "nested": {"sessionToken": "tok_123"}},
            "stream": True,
        }
    )

    chunks = [
        chunk
        async for chunk in service.stream_responses(
            payload,
            {
                "Authorization": "Bearer secret",
                "Content-Type": "application/json",
                "User-Agent": "codex-test",
                "Session_Id": "sid-stream",
            },
        )
    ]

    assert chunks
    visibility = json.loads(cast(str, request_logs.calls[0]["request_visibility"]))
    assert visibility["headers"] == {
        "content-type": "application/json",
        "user-agent": "codex-test",
    }
    assert visibility["body"]["instructions"] == "hello there"
    assert visibility["body"]["input"] == [{"role": "user", "content": "keep me hidden"}]
    assert visibility["body"]["service_tier"] == "priority"
    assert visibility["body"]["reasoning"] == {"effort": "high", "summary": "detailed"}
    assert visibility["body"]["metadata"] == {
        "apiKey": "[REDACTED]",
        "nested": {"sessionToken": "[REDACTED]"},
    }


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
async def test_stream_responses_logs_actual_service_tier_and_requested_tier_trace(monkeypatch, caplog):
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
    assert request_logs.calls[0]["requested_service_tier"] == "priority"
    assert request_logs.calls[0]["actual_service_tier"] == "default"
    assert f"request_id={request_id}" in caplog.text
    assert "kind=stream" in caplog.text
    assert "requested_service_tier=priority" in caplog.text
    assert "actual_service_tier=default" in caplog.text


@pytest.mark.asyncio
async def test_service_stream_responses_uses_dashboard_upstream_transport_override(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    setattr(settings, "upstream_stream_transport", "websocket")
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_transport_override")
    captured: dict[str, object] = {}

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(
        payload,
        headers,
        access_token,
        account_id,
        base_url=None,
        raise_for_status=False,
        upstream_stream_transport_override=None,
    ):
        captured["override"] = upstream_stream_transport_override
        yield 'data: {"type":"response.completed","response":{"id":"resp_transport_override"}}\n\n'

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            "stream": True,
        }
    )

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    assert chunks
    assert captured["override"] == "websocket"


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
    assert request_logs.calls[0]["service_tier"] == "default"
    assert request_logs.calls[0]["requested_service_tier"] == "priority"
    assert request_logs.calls[0]["actual_service_tier"] == "default"
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
async def test_connect_proxy_websocket_maps_budget_exhaustion_to_timeout_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

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
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_budget_timeout",
        model="gpt-5.1",
        service_tier="priority",
        reasoning_effort="high",
        api_key_reservation=None,
        started_at=100.0,
    )

    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

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
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_request_timeout"
    assert sent_payload["error"]["message"] == "Proxy request budget exhausted"
    assert request_logs.calls[0]["request_id"] == "ws_req_budget_timeout"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["service_tier"] == "priority"


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
    websocket_await_args = websocket_send.await_args
    assert websocket_await_args is not None
    sent_payload = json.loads(websocket_await_args.args[0])
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
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
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
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_unavailable"
    assert sent_payload["error"]["message"] == "Request to upstream timed out"
    assert request_logs.calls[0]["request_id"] == "ws_req_forced_refresh_timeout"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["transport"] == "websocket"


@pytest.mark.asyncio
async def test_connect_proxy_websocket_maps_handshake_budget_exhaustion_to_timeout_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_handshake_budget")
    handle_connect_error = AsyncMock()

    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(
        service,
        "_open_upstream_websocket",
        AsyncMock(
            side_effect=proxy_module.ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "Proxy request budget exhausted"),
            )
        ),
    )
    monkeypatch.setattr(service, "_handle_websocket_connect_error", handle_connect_error)
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_handshake_budget",
        model="gpt-5.1",
        service_tier="priority",
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=100.0,
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
    handle_connect_error.assert_not_awaited()
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_request_timeout"
    assert sent_payload["error"]["message"] == "Proxy request budget exhausted"
    assert request_logs.calls[0]["request_id"] == "ws_req_handshake_budget"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"


@pytest.mark.asyncio
async def test_prepare_websocket_response_create_request_normalizes_payload_and_reserves_forwarded_tier(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    reserve_usage = AsyncMock(return_value=None)
    stale_api_key = ApiKeyData(
        id="key_stale",
        name="stale",
        key_prefix="sk-stale",
        allowed_models=["gpt-5.1"],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    refreshed_api_key = ApiKeyData(
        id="key_stale",
        name="refreshed",
        key_prefix="sk-fresh",
        allowed_models=["gpt-5.2"],
        enforced_model="gpt-5.2",
        enforced_reasoning_effort="high",
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve_usage)
    monkeypatch.setattr(
        service,
        "_refresh_websocket_api_key_policy",
        AsyncMock(return_value=refreshed_api_key),
    )

    prepared = await service._prepare_websocket_response_create_request(
        {
            "type": "response.create",
            "model": "gpt-5.1",
            "input": "hello",
            "promptCacheKey": "thread_123",
            "promptCacheRetention": "12h",
            "tools": [{"type": "web_search_preview"}],
            "serviceTier": "fast",
            "reasoning": {"effort": "low"},
        },
        headers={"session_id": "sid-ignored"},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        api_key=stale_api_key,
    )

    reserve_usage.assert_awaited_once_with(
        refreshed_api_key,
        request_model="gpt-5.2",
        request_service_tier="priority",
    )
    assert prepared.request_state.model == "gpt-5.2"
    assert prepared.request_state.service_tier == "priority"
    assert prepared.request_state.reasoning_effort == "high"
    assert prepared.affinity_policy.key == "thread_123"
    assert prepared.affinity_policy.kind == proxy_service.StickySessionKind.PROMPT_CACHE
    normalized_payload = json.loads(prepared.text_data)
    assert normalized_payload["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]
    assert normalized_payload["prompt_cache_key"] == "thread_123"
    assert "promptCacheKey" not in normalized_payload
    assert "promptCacheRetention" not in normalized_payload
    assert "prompt_cache_retention" not in normalized_payload
    assert normalized_payload["tools"] == [{"type": "web_search"}]
    assert normalized_payload["model"] == "gpt-5.2"
    assert normalized_payload["reasoning"] == {"effort": "high"}
    assert normalized_payload["service_tier"] == "priority"
    assert "serviceTier" not in normalized_payload


@pytest.mark.asyncio
async def test_prepare_websocket_response_create_request_logs_affinity_metadata(monkeypatch, caplog):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    reserve_usage = AsyncMock(return_value=None)
    api_key = ApiKeyData(
        id="key_ws_shape",
        name="shape",
        key_prefix="sk-shape",
        allowed_models=["gpt-5.1"],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = True
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False
        openai_prompt_cache_key_derivation_enabled = True

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve_usage)
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=api_key))

    token = set_request_id("req_ws_shape_1")
    try:
        caplog.set_level(logging.WARNING)
        prepared = await service._prepare_websocket_response_create_request(
            {
                "type": "response.create",
                "model": "gpt-5.1",
                "input": "hello",
            },
            headers={"session_id": "ws-session-1"},
            codex_session_affinity=True,
            openai_cache_affinity=True,
            sticky_threads_enabled=False,
            openai_cache_affinity_max_age_seconds=300,
            api_key=api_key,
        )
    finally:
        reset_request_id(token)

    assert prepared.affinity_policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert "proxy_request_shape" in caplog.text
    assert "kind=websocket" in caplog.text
    assert "sticky_kind=codex_session" in caplog.text
    assert "sticky_key_source=session_header" in caplog.text
    assert "prompt_cache_key_set=True" in caplog.text


def test_websocket_receive_timeout_prefers_idle_timeout_when_budget_allows(monkeypatch):
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    timeout = proxy_service._websocket_receive_timeout_for_pending_requests(
        [90.0, 95.0],
        proxy_request_budget_seconds=20.0,
        stream_idle_timeout_seconds=5.0,
    )

    assert timeout is not None
    assert timeout.timeout_seconds == 5.0
    assert timeout.error_code == "stream_idle_timeout"
    assert timeout.error_message == "Upstream stream idle timeout"


def test_websocket_receive_timeout_prefers_request_budget_when_sooner(monkeypatch):
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    timeout = proxy_service._websocket_receive_timeout_for_pending_requests(
        [90.0],
        proxy_request_budget_seconds=11.0,
        stream_idle_timeout_seconds=5.0,
    )

    assert timeout is not None
    assert timeout.timeout_seconds == 1.0
    assert timeout.error_code == "upstream_request_timeout"
    assert timeout.error_message == "Proxy request budget exhausted"


@pytest.mark.asyncio
async def test_fail_expired_pending_websocket_requests_keeps_newer_requests(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    emit_terminal_error = AsyncMock()
    release_reservation = AsyncMock()

    monkeypatch.setattr(service, "_emit_websocket_terminal_error", emit_terminal_error)
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    expired_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_expired",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=90.0,
        response_id="resp_expired",
    )
    newer_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_newer",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=99.5,
        response_id="resp_newer",
    )
    pending_requests = deque([expired_request, newer_request])

    await service._fail_expired_pending_websocket_requests(
        account_id_value="acc_ws_budget",
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        request_budget_seconds=5.0,
        error_code="upstream_request_timeout",
        error_message="Proxy request budget exhausted",
        api_key=None,
        websocket=cast(WebSocket, SimpleNamespace()),
        client_send_lock=anyio.Lock(),
    )

    assert list(pending_requests) == [newer_request]
    emit_terminal_error.assert_awaited_once()
    release_reservation.assert_awaited_once_with(None)
    assert len(request_logs.calls) == 1
    assert request_logs.calls[0]["request_id"] == "resp_expired"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"


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

    completed_payload: dict[str, JsonValue] = {
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
        response_create_gate=asyncio.Semaphore(1),
    )

    record_success.assert_awaited_once_with(account)
    handle_stream_error.assert_not_awaited()
    assert completed_upstream_control.reconnect_requested is False

    failed_payload: dict[str, JsonValue] = {
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
        response_create_gate=asyncio.Semaphore(1),
    )

    handle_args = handle_stream_error.await_args
    assert handle_args is not None
    assert handle_args.args[0] == account
    assert handle_args.args[2] == "rate_limit_exceeded"
    assert failed_upstream_control.reconnect_requested is True

    record_success.reset_mock()
    handle_stream_error.reset_mock()
    incomplete_payload: dict[str, JsonValue] = {
        "type": "response.incomplete",
        "response": {
            "id": "resp_ws_incomplete",
            "status": "incomplete",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        },
    }
    incomplete_event = parse_sse_event(f"data: {json.dumps(incomplete_payload)}\n\n")
    assert incomplete_event is not None
    incomplete_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_incomplete",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    incomplete_upstream_control = proxy_service._WebSocketUpstreamControl()

    await service._finalize_websocket_request_state(
        incomplete_state,
        account=account,
        account_id_value=account.id,
        event=incomplete_event,
        event_type="response.incomplete",
        payload=incomplete_payload,
        api_key=None,
        upstream_control=incomplete_upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    record_success.assert_not_awaited()
    handle_stream_error.assert_not_awaited()
    assert incomplete_upstream_control.reconnect_requested is False
    assert request_logs.calls[-1]["status"] == "error"


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
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        response_create_gate=asyncio.Semaphore(1),
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
    assert _proxy_error_code(exc) == "upstream_unavailable"
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
    assert _proxy_error_code(exc) == "upstream_unavailable"
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
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_select_account_with_budget_times_out_during_settings_fetch(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    select_account = AsyncMock(return_value=AccountSelection(account=_make_account("acc_budget"), error_message=None))

    class _SlowSettingsCache:
        async def get(self) -> object:
            await anyio.sleep(0.05)
            return settings

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SlowSettingsCache())
    monkeypatch.setattr(proxy_service, "_remaining_budget_seconds", lambda _deadline: 0.01)
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._select_account_with_budget(deadline=123.0, request_id="req-budget", kind="compact")

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert _proxy_error_message(exc) == "Proxy request budget exhausted"
    select_account.assert_not_awaited()


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
    assert _proxy_error_code(exc) == "upstream_unavailable"
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
    assert _proxy_error_code(exc) == "upstream_unavailable"
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
    assert _proxy_error_code(exc) == "upstream_unavailable"
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
    assert _proxy_error_code(exc) == "no_additional_quota_eligible_accounts"
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
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert _proxy_error_message(exc) == "Request to upstream timed out"


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
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert _proxy_error_message(exc) == expected_message


class _CBStub:
    def __init__(self) -> None:
        self.failures: list[Exception] = []
        self.successes: int = 0

    async def pre_call_check(self) -> bool:
        return False

    async def release_half_open_probe(self) -> None:
        pass

    async def _record_failure(self, exc: Exception) -> None:
        self.failures.append(exc)

    async def _record_success(self) -> None:
        self.successes += 1


@pytest.mark.asyncio
async def test_cb_context_normal_200_records_success(monkeypatch):
    cb = _CBStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    resp = SimpleNamespace(status=200)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cm():
        yield resp

    async with proxy_module._service_circuit_breaker_context(_fake_cm(), account_id="acc_test") as r:
        assert r.status == 200

    assert cb.successes == 1
    assert cb.failures == []


@pytest.mark.asyncio
async def test_cb_context_4xx_caller_raises_records_success(monkeypatch):
    cb = _CBStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    resp = SimpleNamespace(status=429)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cm():
        yield resp

    class _ClientError(Exception):
        status_code = 429

    with pytest.raises(_ClientError):
        async with proxy_module._service_circuit_breaker_context(_fake_cm(), account_id="acc_test"):
            raise _ClientError("rate limited")

    assert cb.successes == 1
    assert cb.failures == []


@pytest.mark.asyncio
async def test_cb_context_200_body_timeout_records_failure(monkeypatch):
    cb = _CBStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    resp = SimpleNamespace(status=200)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cm():
        yield resp

    with pytest.raises(asyncio.TimeoutError):
        async with proxy_module._service_circuit_breaker_context(_fake_cm(), account_id="acc_test"):
            raise asyncio.TimeoutError("body read timeout")

    assert cb.successes == 0
    assert len(cb.failures) == 1


@pytest.mark.asyncio
async def test_cb_context_connection_failure_records_failure(monkeypatch):
    cb = _CBStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cm():
        raise ConnectionError("upstream unreachable")
        yield  # pragma: no cover

    with pytest.raises(ConnectionError):
        async with proxy_module._service_circuit_breaker_context(_fake_cm(), account_id="acc_test"):
            pass  # pragma: no cover

    assert cb.successes == 0
    assert len(cb.failures) == 1


@pytest.mark.asyncio
async def test_cb_context_open_circuit_closes_request_context_manager(monkeypatch):
    class _OpenCircuitCB:
        async def pre_call_check(self) -> bool:
            raise proxy_module.CircuitBreakerOpenError("open")

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            del exc

        async def _record_success(self) -> None:
            pass

    class _RequestCM:
        def __init__(self) -> None:
            self.close_called = False

        def close(self) -> None:
            self.close_called = True

        async def __aenter__(self):
            raise AssertionError("context manager should not be entered")

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

    cb = _OpenCircuitCB()
    cm = _RequestCM()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    with pytest.raises(proxy_module.CircuitBreakerOpenError):
        async with proxy_module._service_circuit_breaker_context(cm, account_id="acc_test"):
            pass  # pragma: no cover

    assert cm.close_called is True
