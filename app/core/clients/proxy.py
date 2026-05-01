from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import hashlib
import ipaddress
import json
import logging
import os
import socket
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import (
    AsyncContextManager,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
)
from urllib.parse import ParseResult, urlparse, urlunparse

import aiohttp
from aiohttp import hdrs
from aiohttp.client_ws import DEFAULT_WS_CLIENT_TIMEOUT, WebSocketDataQueue
from aiohttp.http_websocket import WS_KEY, WebSocketReader, WebSocketWriter
from multidict import CIMultiDict

from app.core.clients.http import get_http_client
from app.core.config.settings import Settings, get_settings
from app.core.conversation_archive import archive_json, archive_text
from app.core.errors import (
    OpenAIErrorDetail,
    OpenAIErrorEnvelope,
    ResponseFailedEvent,
    openai_error,
    response_failed_event,
)
from app.core.openai.model_registry import get_model_registry
from app.core.openai.models import CompactResponsePayload, OpenAIError
from app.core.openai.parsing import (
    parse_compact_response_payload,
    parse_error_payload,
    parse_sse_event,
)
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    _is_server_error,
    get_circuit_breaker_for_account,
)
from app.core.types import JsonObject, JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import format_sse_event

IGNORE_INBOUND_HEADERS = {
    "authorization",
    "chatgpt-account-id",
    "content-length",
    "host",
    "forwarded",
    "x-real-ip",
    "true-client-ip",
}

_ERROR_TYPE_CODE_MAP = {
    "rate_limit_exceeded": "rate_limit_exceeded",
    "usage_not_included": "usage_not_included",
    "insufficient_quota": "insufficient_quota",
    "quota_exceeded": "quota_exceeded",
}

_SSE_EVENT_TYPE_ALIASES = {
    "response.text.delta": "response.output_text.delta",
    "response.audio.delta": "response.output_audio.delta",
    "response.audio_transcript.delta": "response.output_audio_transcript.delta",
}

_SSE_READ_CHUNK_SIZE = 1 * 1024
_IMAGE_INLINE_MAX_BYTES = 8 * 1024 * 1024
_IMAGE_INLINE_CHUNK_SIZE = 64 * 1024
_IMAGE_INLINE_TIMEOUT_SECONDS = 8.0
_BLOCKED_LITERAL_HOSTS = {"localhost", "localhost.localdomain"}
_UPSTREAM_RESPONSE_CREATE_MAX_BYTES = get_settings().upstream_response_create_max_bytes
_UPSTREAM_RESPONSE_CREATE_WARN_BYTES = int(_UPSTREAM_RESPONSE_CREATE_MAX_BYTES * 0.8)
_RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE = (
    "[codex-lb omitted historical tool output ({bytes} bytes) to fit upstream websocket budget]"
)
_RESPONSE_CREATE_IMAGE_OMISSION_NOTICE = "[codex-lb omitted historical inline image to fit upstream websocket budget]"
_UPSTREAM_TRACE_HEADER_ALLOWLIST = frozenset(
    {
        "accept",
        "chatgpt-account-id",
        "content-type",
        "request-id",
        "session_id",
        "user-agent",
        "x-codex-conversation-id",
        "x-codex-session-id",
        "x-openai-client-arch",
        "x-openai-client-id",
        "x-openai-client-os",
        "x-openai-client-user-agent",
        "x-openai-client-version",
        "x-request-id",
    }
)
_NATIVE_CODEX_ORIGINATORS = frozenset(
    {
        "Codex Desktop",
        "codex_atlas",
        "codex_chatgpt_desktop",
        "codex_cli_rs",
        "codex_exec",
        "codex_vscode",
    }
)
_NATIVE_CODEX_STREAM_HEADER_KEYS = frozenset(
    {
        "x-codex-turn-state",
        "x-codex-turn-metadata",
        "x-codex-beta-features",
    }
)
_HOP_BY_HOP_HEADER_NAMES = frozenset(
    {
        "accept",
        "connection",
        "content-type",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_AUTO_WEBSOCKET_HANDSHAKE_FALLBACK_STATUSES = frozenset({426})
_WEBSOCKET_RESPONSE_CREATE_EXCLUDED_FIELDS = frozenset({"background", "stream"})
_WEBSOCKET_HANDSHAKE_ERROR_HINTS = (
    ("account_deactivated", "account has been deactivated"),
    ("usage_not_included", "usage not included"),
    ("insufficient_quota", "insufficient quota"),
    ("quota_exceeded", "quota exceeded"),
    ("usage_limit_reached", "usage limit reached"),
    ("rate_limit_exceeded", "rate limit"),
)

logger = logging.getLogger(__name__)
_STREAM_CONNECT_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "stream_connect_timeout_override",
    default=None,
)
_STREAM_IDLE_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "stream_idle_timeout_override",
    default=None,
)
_STREAM_TOTAL_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "stream_total_timeout_override",
    default=None,
)
_COMPACT_CONNECT_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "compact_connect_timeout_override",
    default=None,
)
_COMPACT_TOTAL_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "compact_total_timeout_override",
    default=None,
)
_TRANSCRIBE_CONNECT_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "transcribe_connect_timeout_override",
    default=None,
)
_TRANSCRIBE_TOTAL_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "transcribe_total_timeout_override",
    default=None,
)

R = TypeVar("R")


async def _call_with_service_circuit_breaker(
    request: Awaitable[R],
    *,
    settings: Settings | None = None,
    account_id: str | None = None,
) -> R:
    if not account_id:
        return await request
    effective_settings = settings or get_settings()
    circuit_breaker = get_circuit_breaker_for_account(account_id, effective_settings)
    if circuit_breaker is None:
        return await request
    return await circuit_breaker.call(request)


@asynccontextmanager
async def _service_circuit_breaker_context(
    cm: AsyncContextManager[aiohttp.ClientResponse],
    *,
    settings: Settings | None = None,
    account_id: str | None = None,
) -> AsyncIterator[aiohttp.ClientResponse]:
    """Wrap an async context manager with circuit breaker protection."""
    effective_settings = settings or get_settings()
    cb = get_circuit_breaker_for_account(account_id, effective_settings) if account_id else None
    is_probe = False
    if cb is not None:
        try:
            is_probe = await cb.pre_call_check()
        except BaseException:
            close = getattr(cm, "close", None)
            if callable(close):
                close()
            raise
    resp_ref: aiohttp.ClientResponse | None = None
    try:
        async with cm as resp:
            resp_ref = resp
            yield resp
        if cb is not None:
            if hasattr(resp, "status") and resp.status >= 500:
                await cb._record_failure(Exception(f"HTTP {resp.status}"))
            else:
                await cb._record_success()
    except CircuitBreakerOpenError:
        raise
    except Exception as e:
        if cb is not None:
            if (
                resp_ref is not None
                and hasattr(resp_ref, "status")
                and resp_ref.status < 500
                and not _is_server_error(e)
            ):
                await cb._record_success()
            else:
                await cb._record_failure(e)
        raise
    finally:
        if is_probe and cb is not None:
            await cb.release_half_open_probe()


_HELD_HALF_OPEN_PROBE_FLAG = "_codex_lb_half_open_probe_held"
_HELD_HALF_OPEN_PROBE_BREAKER = "_codex_lb_half_open_probe_breaker"


def _bind_half_open_probe(
    websocket: aiohttp.ClientWebSocketResponse,
    circuit_breaker: "CircuitBreaker",
) -> None:
    setattr(websocket, _HELD_HALF_OPEN_PROBE_FLAG, True)
    setattr(websocket, _HELD_HALF_OPEN_PROBE_BREAKER, circuit_breaker)


async def _release_bound_half_open_probe(websocket: aiohttp.ClientWebSocketResponse | None) -> None:
    if websocket is None or not getattr(websocket, _HELD_HALF_OPEN_PROBE_FLAG, False):
        return
    circuit_breaker = cast("CircuitBreaker | None", getattr(websocket, _HELD_HALF_OPEN_PROBE_BREAKER, None))
    setattr(websocket, _HELD_HALF_OPEN_PROBE_FLAG, False)
    setattr(websocket, _HELD_HALF_OPEN_PROBE_BREAKER, None)
    if circuit_breaker is not None:
        await circuit_breaker.release_half_open_probe()


class StreamIdleTimeoutError(Exception):
    pass


class StreamEventTooLargeError(Exception):
    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(f"SSE event exceeded {limit_bytes} bytes (received {size_bytes} bytes)")
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class ErrorResponseProtocol(Protocol):
    status: int
    reason: str | None

    async def json(self, *, content_type: str | None = None) -> JsonValue: ...

    async def text(self, *, encoding: str | None = None, errors: str = "strict") -> str: ...


ErrorResponse: TypeAlias = aiohttp.ClientResponse | ErrorResponseProtocol


class SSEContentProtocol(Protocol):
    def iter_chunked(self, size: int) -> "SSEChunkIteratorProtocol": ...


class SSEChunkIteratorProtocol(Protocol):
    def __aiter__(self) -> "SSEChunkIteratorProtocol": ...

    def __anext__(self) -> Awaitable[bytes]: ...


class SSEResponseProtocol(Protocol):
    content: SSEContentProtocol


SSEResponse: TypeAlias = aiohttp.ClientResponse | SSEResponseProtocol


class ProxyResponseError(Exception):
    status_code: int
    payload: OpenAIErrorEnvelope

    def __init__(
        self,
        status_code: int,
        payload: OpenAIErrorEnvelope,
        *,
        failure_phase: str | None = None,
        retryable_same_contract: bool = False,
        failure_detail: str | None = None,
        failure_exception_type: str | None = None,
        upstream_status_code: int | None = None,
    ) -> None:
        super().__init__(f"Proxy response error ({status_code})")
        self.status_code = status_code
        self.payload = payload
        self.failure_phase = failure_phase
        self.retryable_same_contract = retryable_same_contract
        self.failure_detail = failure_detail
        self.failure_exception_type = failure_exception_type
        self.upstream_status_code = upstream_status_code


def _should_drop_inbound_header(name: str) -> bool:
    normalized = name.lower()
    if normalized in IGNORE_INBOUND_HEADERS:
        return True
    if normalized.startswith("x-forwarded-"):
        return True
    if normalized.startswith("cf-"):
        return True
    return False


def filter_inbound_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if not _should_drop_inbound_header(key)}


def _build_upstream_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    accept: str = "text/event-stream",
) -> dict[str, str]:
    headers = dict(inbound)
    lower_keys = {key.lower() for key in headers}
    if "x-request-id" not in lower_keys and "request-id" not in lower_keys:
        request_id = get_request_id()
        if request_id:
            headers["x-request-id"] = request_id
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Accept"] = accept
    headers["Content-Type"] = "application/json"
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


_TRANSCRIBE_FORWARD_HEADER_PREFIXES = ("x-openai-", "x-codex-")


def _build_upstream_transcribe_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, str]:
    # Minimal header set matching Codex CLI ``/transcribe`` fingerprint.
    # Omit Accept, x-request-id, and bulk-forwarded inbound headers to
    # avoid upstream WAF rejection.
    headers: dict[str, str] = {}
    headers["Authorization"] = f"Bearer {access_token}"
    if account_id:
        headers["chatgpt-account-id"] = account_id
    for key, value in inbound.items():
        lower = key.lower()
        if lower == "user-agent":
            headers[key] = value
        elif lower.startswith(_TRANSCRIBE_FORWARD_HEADER_PREFIXES):
            headers[key] = value
    return headers


def _build_upstream_websocket_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, str]:
    connected_header_tokens: set[str] = set()
    for key, value in inbound.items():
        if key.lower() != "connection":
            continue
        connected_header_tokens.update(
            token.strip().lower() for token in value.split(",") if isinstance(value, str) and token.strip()
        )
    blocked_header_names = _HOP_BY_HOP_HEADER_NAMES | connected_header_tokens
    headers = {key: value for key, value in inbound.items() if key.lower() not in blocked_header_names}
    lower_keys = {key.lower() for key in headers}
    if "x-request-id" not in lower_keys and "request-id" not in lower_keys:
        request_id = get_request_id()
        if request_id:
            headers["x-request-id"] = request_id
    headers["Authorization"] = f"Bearer {access_token}"
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def _interesting_upstream_header_keys(headers: Mapping[str, str]) -> list[str]:
    return sorted({key.lower() for key in headers if key.lower() in _UPSTREAM_TRACE_HEADER_ALLOWLIST})


def _summarize_upstream_target(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _summarize_input_value(value: JsonValue | None) -> str:
    if value is None:
        return "0"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        if not value:
            return "0"
        type_counts: dict[str, int] = {}
        for item in value:
            type_name = type(item).__name__
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
        summary = ",".join(f"{key}={type_counts[key]}" for key in sorted(type_counts))
        return f"{len(value)}({summary})"
    return type(value).__name__


def _summarize_json_payload(payload: Mapping[str, JsonValue]) -> str:
    keys = sorted(payload.keys())
    model = payload.get("model")
    stream = payload.get("stream")
    input_summary = _summarize_input_value(payload.get("input"))
    return f"model={model} stream={stream} input={input_summary} keys={keys}"


def _summarize_transcription_payload(
    *,
    filename: str,
    content_type: str | None,
    prompt: str | None,
    audio_bytes: bytes,
) -> dict[str, JsonValue]:
    return {
        "filename": filename,
        "content_type": content_type,
        "prompt_present": prompt is not None,
        "audio_bytes": len(audio_bytes),
    }


def _error_details_from_envelope(payload: OpenAIErrorEnvelope) -> tuple[str | None, str | None]:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    message = error.get("message")
    return code if isinstance(code, str) else None, message if isinstance(message, str) else None


def _error_details_from_failed_event(payload: ResponseFailedEvent) -> tuple[str | None, str | None]:
    response = payload.get("response")
    if not isinstance(response, dict):
        return None, None
    error = response.get("error")
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    message = error.get("message")
    return code if isinstance(code, str) else None, message if isinstance(message, str) else None


def _extract_json_object_from_text(text: str) -> JsonValue | None:
    if not text:
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return value
    return None


def _infer_websocket_handshake_error_code(status: int | None, message: str) -> str:
    lowered = message.lower()
    for code, hint in _WEBSOCKET_HANDSHAKE_ERROR_HINTS:
        if hint in lowered:
            return code
    if status == 401:
        return "invalid_api_key"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limit_exceeded"
    return "upstream_error"


def _error_payload_from_websocket_handshake_error(exc: aiohttp.WSServerHandshakeError) -> OpenAIErrorEnvelope:
    message = exc.message or str(exc)
    extracted = _extract_json_object_from_text(message)
    if extracted is not None:
        error = parse_error_payload(extracted)
        if error is not None:
            return {"error": _openai_error_detail(error)}

    code = _infer_websocket_handshake_error_code(exc.status, message)
    if code == "invalid_api_key":
        return openai_error(code, message, error_type="authentication_error")
    if code == "not_found":
        return openai_error(code, message, error_type="invalid_request_error")
    if code == "rate_limit_exceeded":
        return openai_error(code, message, error_type="rate_limit_error")
    return openai_error(code, message)


def _maybe_log_upstream_request_start(
    *,
    kind: str,
    url: str,
    headers: Mapping[str, str],
    method: str,
    payload_summary: str,
    payload_json: str | None = None,
) -> None:
    settings = get_settings()
    if not settings.log_upstream_request_summary and not settings.log_upstream_request_payload:
        return

    request_id = get_request_id()
    target = _summarize_upstream_target(url)
    account_id = headers.get("chatgpt-account-id")
    header_keys = _interesting_upstream_header_keys(headers)

    if settings.log_upstream_request_summary:
        logger.info(
            "upstream_request_start request_id=%s kind=%s method=%s target=%s account_id=%s headers=%s payload=%s",
            request_id,
            kind,
            method,
            target,
            account_id,
            header_keys,
            payload_summary,
        )
    if settings.log_upstream_request_payload and payload_json is not None:
        logger.info(
            "upstream_request_payload request_id=%s kind=%s target=%s payload=%s",
            request_id,
            kind,
            target,
            payload_json,
        )


def _maybe_log_upstream_request_complete(
    *,
    kind: str,
    url: str,
    headers: Mapping[str, str],
    method: str,
    started_at: float,
    status_code: int | None,
    error_code: str | None,
    error_message: str | None,
    failure_phase: str | None = None,
    payload_object: str | None = None,
    failure_detail: str | None = None,
    failure_exception_type: str | None = None,
    retryable_same_contract: bool | None = None,
) -> None:
    settings = get_settings()
    if not settings.log_upstream_request_summary:
        return

    level = logging.INFO
    if status_code is not None and status_code >= 500:
        level = logging.ERROR
    elif (status_code is not None and status_code >= 400) or error_code is not None:
        level = logging.WARNING

    logger.log(
        level,
        (
            "upstream_request_complete request_id=%s kind=%s method=%s target=%s "
            "account_id=%s status=%s duration_ms=%s error_code=%s error_message=%s "
            "failure_phase=%s payload_object=%s failure_detail=%s failure_exception_type=%s "
            "retryable_same_contract=%s"
        ),
        get_request_id(),
        kind,
        method,
        _summarize_upstream_target(url),
        headers.get("chatgpt-account-id"),
        status_code,
        int((time.monotonic() - started_at) * 1000),
        error_code,
        error_message,
        failure_phase,
        payload_object,
        failure_detail,
        failure_exception_type,
        retryable_same_contract,
    )


def _normalize_error_code(code: str | None, error_type: str | None) -> str:
    if code:
        normalized_code = code.lower()
        mapped = _ERROR_TYPE_CODE_MAP.get(normalized_code)
        return mapped or normalized_code
    normalized_type = error_type.lower() if error_type else None
    if normalized_type:
        mapped = _ERROR_TYPE_CODE_MAP.get(normalized_type)
        return mapped or normalized_type
    return "upstream_error"


def _effective_stream_timeout(configured_timeout_seconds: float, timeout_kind: str) -> float:
    if timeout_kind == "connect":
        override = _STREAM_CONNECT_TIMEOUT_OVERRIDE.get()
    elif timeout_kind == "idle":
        override = _STREAM_IDLE_TIMEOUT_OVERRIDE.get()
    else:
        override = _STREAM_TOTAL_TIMEOUT_OVERRIDE.get()
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _effective_compact_connect_timeout(configured_timeout_seconds: float) -> float:
    override = _COMPACT_CONNECT_TIMEOUT_OVERRIDE.get()
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _effective_compact_total_timeout(configured_timeout_seconds: float | None) -> float | None:
    override = _COMPACT_TOTAL_TIMEOUT_OVERRIDE.get()
    if configured_timeout_seconds is None:
        return None if override is None else max(0.001, override)
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _effective_transcribe_connect_timeout(configured_timeout_seconds: float) -> float:
    override = _TRANSCRIBE_CONNECT_TIMEOUT_OVERRIDE.get()
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _effective_transcribe_total_timeout(configured_timeout_seconds: float) -> float:
    override = _TRANSCRIBE_TOTAL_TIMEOUT_OVERRIDE.get()
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _remaining_total_timeout(timeout_seconds: float | None, started_at: float, now: float) -> float | None:
    if timeout_seconds is None:
        return None
    return max(0.001, timeout_seconds - max(0.0, now - started_at))


def _find_sse_separator(buffer: bytes | bytearray) -> tuple[int, int] | None:
    separators = (b"\r\n\r\n", b"\n\n")
    positions = [(buffer.find(separator), len(separator)) for separator in separators]
    valid_positions = [position for position in positions if position[0] >= 0]
    if not valid_positions:
        return None
    return min(valid_positions, key=lambda item: item[0])


def _pop_sse_event(buffer: bytearray) -> bytes | None:
    separator = _find_sse_separator(buffer)
    if separator is None:
        return None
    index, separator_len = separator
    event_end = index + separator_len
    event = bytes(buffer[:event_end])
    del buffer[:event_end]
    return event


async def _iter_sse_events(
    resp: SSEResponse,
    idle_timeout_seconds: float,
    max_event_bytes: int,
) -> AsyncIterator[str]:
    async def _next_chunk() -> bytes:
        return await iterator.__anext__()

    async def _cancel_pending_chunk(task: asyncio.Task[bytes]) -> None:
        if task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    buffer = bytearray()
    chunk_iterator = resp.content.iter_chunked(_SSE_READ_CHUNK_SIZE)
    iterator = chunk_iterator.__aiter__()

    while True:
        next_chunk = asyncio.create_task(_next_chunk())
        try:
            done, _ = await asyncio.wait({next_chunk}, timeout=idle_timeout_seconds)
            if not done:
                await _cancel_pending_chunk(next_chunk)
                raise StreamIdleTimeoutError()
            chunk = await next_chunk
        except StopAsyncIteration:
            break
        except asyncio.CancelledError:
            await _cancel_pending_chunk(next_chunk)
            raise

        if not chunk:
            continue

        buffer.extend(chunk)
        while True:
            raw_event = _pop_sse_event(buffer)
            if raw_event is None:
                if len(buffer) > max_event_bytes:
                    raise StreamEventTooLargeError(len(buffer), max_event_bytes)
                break

            if len(raw_event) > max_event_bytes:
                raise StreamEventTooLargeError(len(raw_event), max_event_bytes)

            if raw_event.strip():
                yield raw_event.decode("utf-8", errors="replace")

    if buffer:
        if len(buffer) > max_event_bytes:
            raise StreamEventTooLargeError(len(buffer), max_event_bytes)
        yield bytes(buffer).decode("utf-8", errors="replace")


async def _error_event_from_response(resp: ErrorResponse) -> ResponseFailedEvent:
    fallback_message = f"Upstream error: HTTP {resp.status}"
    if resp.reason:
        fallback_message += f" {resp.reason}"
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        message = text.strip() or fallback_message
        return response_failed_event("upstream_error", message, response_id=get_request_id())

    if isinstance(data, dict):
        error = parse_error_payload(data)
        if error:
            payload = error.model_dump(exclude_none=True)
            event = response_failed_event(
                _normalize_error_code(payload.get("code"), payload.get("type")),
                payload.get("message", fallback_message),
                error_type=payload.get("type") or "server_error",
                response_id=get_request_id(),
                error_param=payload.get("param"),
            )
            for key in ("plan_type", "resets_at", "resets_in_seconds"):
                if key in payload:
                    event["response"]["error"][key] = payload[key]
            return event
        message = _extract_upstream_message(data)
        if message:
            return response_failed_event("upstream_error", message, response_id=get_request_id())
    return response_failed_event("upstream_error", fallback_message, response_id=get_request_id())


async def _error_payload_from_response(resp: ErrorResponse) -> OpenAIErrorEnvelope:
    fallback_message = f"Upstream error: HTTP {resp.status}"
    if resp.reason:
        fallback_message += f" {resp.reason}"
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        message = text.strip() or fallback_message
        return openai_error("upstream_error", message)

    if isinstance(data, dict):
        error = parse_error_payload(data)
        if error:
            return {"error": _openai_error_detail(error)}
        message = _extract_upstream_message(data)
        if message:
            return openai_error("upstream_error", message)
    return openai_error("upstream_error", fallback_message)


def _openai_error_detail(error: OpenAIError) -> OpenAIErrorDetail:
    detail: OpenAIErrorDetail = {}
    if error.message is not None:
        detail["message"] = error.message
    if error.type is not None:
        detail["type"] = error.type
    if error.code is not None:
        detail["code"] = error.code
    if error.param is not None:
        detail["param"] = error.param
    if error.plan_type is not None:
        detail["plan_type"] = error.plan_type
    if error.resets_at is not None:
        detail["resets_at"] = error.resets_at
    if error.resets_in_seconds is not None:
        detail["resets_in_seconds"] = error.resets_in_seconds
    return detail


def _extract_upstream_message(data: Mapping[str, JsonValue]) -> str | None:
    for key in ("message", "detail", "error"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _normalize_sse_data_line(line: str) -> str:
    if not line.startswith("data:"):
        return line
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return line
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return line
    if not isinstance(payload, dict):
        return line
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type in _SSE_EVENT_TYPE_ALIASES:
        payload["type"] = _SSE_EVENT_TYPE_ALIASES[event_type]
        return f"data: {json.dumps(payload, ensure_ascii=True, separators=(',', ':'))}"
    return line


def _normalize_sse_event_block(event_block: str) -> str:
    if not event_block:
        return event_block

    if '"type":' not in event_block:
        return event_block

    if event_block.endswith("\r\n\r\n"):
        line_separator = "\r\n"
        terminator = "\r\n\r\n"
        body = event_block[: -len(terminator)]
    elif event_block.endswith("\n\n"):
        line_separator = "\n"
        terminator = "\n\n"
        body = event_block[: -len(terminator)]
    else:
        line_separator = "\r\n" if "\r\n" in event_block else "\n"
        terminator = ""
        body = event_block

    lines = body.splitlines()
    if not lines:
        return event_block

    normalized_lines: list[str] = []
    changed = False
    for line in lines:
        normalized_line = _normalize_sse_data_line(line)
        if normalized_line != line:
            changed = True
        normalized_lines.append(normalized_line)
    if not changed:
        return event_block

    normalized = line_separator.join(normalized_lines)
    if terminator:
        return normalized + terminator
    return normalized


def _normalize_stream_event_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type in _SSE_EVENT_TYPE_ALIASES:
        normalized = dict(payload)
        normalized["type"] = _SSE_EVENT_TYPE_ALIASES[event_type]
        return normalized
    return payload


def _to_websocket_upstream_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        scheme = parsed.scheme
    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _configured_stream_transport(
    *,
    transport: str,
    transport_override: str | None = None,
) -> str:
    return transport_override if transport_override is not None else transport


def _has_native_codex_transport_headers(headers: Mapping[str, str]) -> bool:
    normalized = {key.lower(): value for key, value in headers.items()}
    originator = normalized.get("originator")
    if _is_native_codex_originator(originator):
        return True
    return any(key in normalized for key in _NATIVE_CODEX_STREAM_HEADER_KEYS)


def _is_native_codex_originator(originator: str | None) -> bool:
    if originator is None:
        return False
    stripped = originator.strip()
    if not stripped:
        return False
    return stripped in _NATIVE_CODEX_ORIGINATORS


def _payload_uses_image_generation_tool(payload: Mapping[str, JsonValue]) -> bool:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if tool_type == "image_generation":
            return True
    return False


def _resolve_stream_transport(
    *,
    transport: str,
    transport_override: str | None,
    model: str | None,
    headers: Mapping[str, str],
    has_image_generation_tool: bool = False,
) -> str:
    configured = _configured_stream_transport(transport=transport, transport_override=transport_override)
    if configured == "websocket":
        return "websocket"
    if configured == "http":
        return "http"
    if has_image_generation_tool:
        return "http"
    if _has_native_codex_transport_headers(headers):
        return "websocket"

    registry = get_model_registry()
    prefers_websockets = getattr(registry, "prefers_websockets", None)
    if callable(prefers_websockets):
        if prefers_websockets(model):
            return "websocket"
        return "http"

    snapshot = registry.get_snapshot()
    if snapshot is None or not isinstance(model, str):
        return "http"
    upstream_model = snapshot.models.get(model)
    if upstream_model and upstream_model.prefer_websockets:
        return "websocket"
    return "http"


def _should_fallback_to_http_after_websocket_handshake_error(
    transport_mode: str,
    exc: aiohttp.WSServerHandshakeError,
) -> bool:
    return transport_mode == "auto" and exc.status in _AUTO_WEBSOCKET_HANDSHAKE_FALLBACK_STATUSES


async def _open_upstream_websocket(
    *,
    session: aiohttp.ClientSession,
    url: str,
    headers: Mapping[str, str],
    connect_timeout_seconds: float,
    max_msg_size: int,
    account_id: str | None = None,
    hold_half_open_probe: bool = False,
) -> tuple[AsyncContextManager[aiohttp.ClientWebSocketResponse], aiohttp.ClientWebSocketResponse]:
    settings = get_settings()
    circuit_breaker = get_circuit_breaker_for_account(account_id, settings) if account_id else None
    is_probe = False
    if circuit_breaker is not None:
        is_probe = await circuit_breaker.pre_call_check()

    request_obj = getattr(session, "request", None)
    if not callable(request_obj):
        try:
            websocket_cm = session.ws_connect(
                url,
                headers=headers,
                receive_timeout=None,
                autoping=True,
                autoclose=True,
                max_msg_size=max_msg_size,
            )
            websocket = await asyncio.wait_for(websocket_cm.__aenter__(), timeout=connect_timeout_seconds)
            if hold_half_open_probe and is_probe and circuit_breaker is not None:
                _bind_half_open_probe(websocket, circuit_breaker)
            return websocket_cm, websocket
        except Exception as exc:
            if circuit_breaker is not None:
                await circuit_breaker._record_failure(exc)
            raise
        finally:
            if is_probe and circuit_breaker is not None and not hold_half_open_probe:
                await circuit_breaker.release_half_open_probe()
    request = cast(Callable[..., Awaitable[aiohttp.ClientResponse]], request_obj)

    request_headers = CIMultiDict(headers)
    request_headers.setdefault(hdrs.UPGRADE, "websocket")
    request_headers.setdefault(hdrs.CONNECTION, "Upgrade")
    request_headers.setdefault(hdrs.SEC_WEBSOCKET_VERSION, "13")
    sec_key = base64.b64encode(os.urandom(16)).decode()
    request_headers[hdrs.SEC_WEBSOCKET_KEY] = sec_key

    timeout = aiohttp.ClientTimeout(total=connect_timeout_seconds, sock_connect=connect_timeout_seconds)
    try:
        try:
            resp = await request(
                hdrs.METH_GET,
                url,
                headers=request_headers,
                timeout=timeout,
                read_until_eof=False,
            )
        except Exception as exc:
            if circuit_breaker is not None:
                await circuit_breaker._record_failure(exc)
            raise

        async def _raise_handshake_error(message: str) -> None:
            body_text = ""
            try:
                body_text = (await resp.text()).strip()
            except Exception:
                body_text = ""
            raise aiohttp.WSServerHandshakeError(
                resp.request_info,
                resp.history,
                message=body_text or message,
                status=resp.status,
                headers=resp.headers,
            )

        _cb_recorded = False
        try:
            if circuit_breaker is not None:
                if resp.status >= 500:
                    await circuit_breaker._record_failure(Exception(f"WebSocket handshake failed: HTTP {resp.status}"))
                    _cb_recorded = True
                elif resp.status != 101:
                    await circuit_breaker._record_success()
                    _cb_recorded = True

            if resp.status != 101:
                await _raise_handshake_error("Invalid response status")

            if resp.headers.get(hdrs.UPGRADE, "").lower() != "websocket":
                await _raise_handshake_error("Invalid upgrade header")

            if resp.headers.get(hdrs.CONNECTION, "").lower() != "upgrade":
                await _raise_handshake_error("Invalid connection header")

            response_key = resp.headers.get(hdrs.SEC_WEBSOCKET_ACCEPT, "")
            expected_key = base64.b64encode(hashlib.sha1(sec_key.encode() + WS_KEY).digest()).decode()
            if response_key != expected_key:
                await _raise_handshake_error("Invalid challenge response")

            conn = resp.connection
            assert conn is not None
            conn_proto = conn.protocol
            assert conn_proto is not None
            conn_proto.read_timeout = None

            transport = conn.transport
            assert transport is not None
            reader = WebSocketDataQueue(conn_proto, 2**16, loop=session._loop)
            conn_proto.set_parser(WebSocketReader(reader, max_msg_size), reader)
            writer = WebSocketWriter(conn_proto, transport, use_mask=True, compress=0, notakeover=False)
        except BaseException as exc:
            if circuit_breaker is not None and not _cb_recorded and isinstance(exc, Exception):
                await circuit_breaker._record_failure(exc)
                _cb_recorded = True
            resp.close()
            raise

        websocket = session._ws_response_class(
            reader,
            writer,
            None,
            resp,
            DEFAULT_WS_CLIENT_TIMEOUT,
            True,
            True,
            session._loop,
            heartbeat=None,
            compress=0,
            client_notakeover=False,
        )
        if hold_half_open_probe and is_probe and circuit_breaker is not None:
            _bind_half_open_probe(websocket, circuit_breaker)
        return websocket, websocket
    finally:
        if is_probe and circuit_breaker is not None and not hold_half_open_probe:
            await circuit_breaker.release_half_open_probe()


async def _stream_websocket_events(
    websocket: aiohttp.ClientWebSocketResponse,
    *,
    idle_timeout_seconds: float,
    total_timeout_seconds: float | None,
    max_event_bytes: int,
) -> AsyncIterator[str]:
    deadline = None if total_timeout_seconds is None else time.monotonic() + total_timeout_seconds

    while True:
        timeout_seconds = idle_timeout_seconds
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            timeout_seconds = min(timeout_seconds, remaining)

        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            if deadline is not None and deadline - time.monotonic() <= 0:
                raise
            raise StreamIdleTimeoutError() from exc

        if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED}:
            break
        if message.type == aiohttp.WSMsgType.ERROR:
            exc = websocket.exception()
            if exc is None and isinstance(message.data, BaseException):
                exc = message.data
            raise exc or aiohttp.ClientError("Upstream websocket error")
        if message.type not in {aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY}:
            continue

        if message.type == aiohttp.WSMsgType.TEXT:
            text = message.data
        else:
            text = message.data.decode("utf-8", errors="replace")
        text_bytes = text.encode("utf-8")
        if len(text_bytes) > max_event_bytes:
            raise StreamEventTooLargeError(len(text_bytes), max_event_bytes)

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        normalized = _normalize_stream_event_payload(payload)
        event_type = payload.get("type")
        yield format_sse_event(normalized)
        if isinstance(event_type, str) and event_type in (
            "response.completed",
            "response.failed",
            "response.incomplete",
        ):
            break


async def _stream_responses_via_websocket(
    *,
    payload_dict: JsonObject,
    url: str,
    headers: Mapping[str, str],
    client_session: aiohttp.ClientSession,
    effective_total_timeout: float,
    effective_connect_timeout: float,
    effective_idle_timeout: float,
    max_event_bytes: int,
    raise_for_status: bool,
    account_id: str | None = None,
) -> AsyncIterator[str]:
    websocket_url = _to_websocket_upstream_url(url)
    request_started_at = time.monotonic()
    request_payload = _prepare_websocket_response_create_payload(payload_dict)
    websocket_cm: AsyncContextManager[aiohttp.ClientWebSocketResponse] | None = None
    websocket: aiohttp.ClientWebSocketResponse | None = None
    circuit_breaker = None
    lifecycle_recorded = False
    seen_terminal = False
    settings = get_settings()
    if account_id is not None:
        circuit_breaker = get_circuit_breaker_for_account(account_id, settings)

    async def _record_lifecycle_success() -> None:
        nonlocal lifecycle_recorded
        if circuit_breaker is None or lifecycle_recorded:
            return
        await circuit_breaker._record_success()
        lifecycle_recorded = True

    async def _record_lifecycle_failure(exc: Exception) -> None:
        nonlocal lifecycle_recorded
        if circuit_breaker is None or lifecycle_recorded:
            return
        await circuit_breaker._record_failure(exc)
        lifecycle_recorded = True

    connect_timeout_seconds = min(
        effective_connect_timeout,
        _remaining_total_timeout(effective_total_timeout, request_started_at, time.monotonic())
        or effective_connect_timeout,
    )
    websocket_cm, websocket = await _open_upstream_websocket(
        session=client_session,
        url=websocket_url,
        headers=headers,
        connect_timeout_seconds=connect_timeout_seconds,
        max_msg_size=max_event_bytes,
        account_id=account_id,
        hold_half_open_probe=True,
    )

    try:
        send_json = getattr(websocket, "send_json", None)
        remaining_total_timeout = _remaining_total_timeout(
            effective_total_timeout,
            request_started_at,
            time.monotonic(),
        )
        archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="websocket",
            payload=request_payload,
            account_id=account_id,
            method="GET",
            url=websocket_url,
            headers=headers,
            extra={"frame_type": "text"},
        )
        if callable(send_json):
            await asyncio.wait_for(
                cast(Callable[[JsonObject], Awaitable[None]], send_json)(request_payload),
                timeout=remaining_total_timeout,
            )
        else:
            await asyncio.wait_for(
                websocket.send_str(json.dumps(request_payload, ensure_ascii=True, separators=(",", ":"))),
                timeout=remaining_total_timeout,
            )
        remaining_total_timeout = _remaining_total_timeout(
            effective_total_timeout,
            request_started_at,
            time.monotonic(),
        )
        async for event in _stream_websocket_events(
            websocket,
            idle_timeout_seconds=effective_idle_timeout,
            total_timeout_seconds=remaining_total_timeout,
            max_event_bytes=max_event_bytes,
        ):
            archive_text(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                text=event,
                account_id=account_id,
                method="GET",
                url=websocket_url,
                headers=headers,
                extra={"event_format": "sse"},
            )
            parsed_event = parse_sse_event(event)
            if parsed_event and parsed_event.type in ("response.completed", "response.failed", "response.incomplete"):
                seen_terminal = True
                await _record_lifecycle_success()
            yield event
        if not seen_terminal:
            await _record_lifecycle_failure(aiohttp.ClientError("Upstream websocket closed without terminal event"))
    except Exception as exc:
        await _record_lifecycle_failure(exc)
        raise
    finally:
        try:
            if websocket_cm is not None:
                await websocket_cm.__aexit__(None, None, None)
        finally:
            await _release_bound_half_open_probe(websocket)


def _build_websocket_response_create_payload(payload_dict: JsonObject) -> JsonObject:
    request_payload: JsonObject = {
        key: value for key, value in payload_dict.items() if key not in _WEBSOCKET_RESPONSE_CREATE_EXCLUDED_FIELDS
    }
    request_payload["type"] = "response.create"
    return request_payload


def _prepare_websocket_response_create_payload(payload_dict: JsonObject) -> JsonObject:
    request_payload = _build_websocket_response_create_payload(payload_dict)
    payload_text = json.dumps(request_payload, ensure_ascii=True, separators=(",", ":"))
    payload_size = len(payload_text.encode("utf-8"))
    if payload_size > _UPSTREAM_RESPONSE_CREATE_MAX_BYTES:
        slimmed_payload, slim_summary = _slim_response_create_payload_for_upstream(
            request_payload,
            max_bytes=_UPSTREAM_RESPONSE_CREATE_MAX_BYTES,
        )
        if slim_summary is not None:
            request_payload = cast(JsonObject, slimmed_payload)
            slimmed_text = json.dumps(request_payload, ensure_ascii=True, separators=(",", ":"))
            logger.warning(
                (
                    "Slimmed response.create before upstream websocket connect request_id=%s "
                    "original_bytes=%s slimmed_bytes=%s historical_tool_outputs_slimmed=%s "
                    "historical_images_slimmed=%s"
                ),
                get_request_id(),
                payload_size,
                len(slimmed_text.encode("utf-8")),
                slim_summary["historical_tool_outputs_slimmed"],
                slim_summary["historical_images_slimmed"],
            )
            payload_text = slimmed_text
            payload_size = len(payload_text.encode("utf-8"))
    if payload_size > _UPSTREAM_RESPONSE_CREATE_WARN_BYTES:
        previous_response_id = request_payload.get("previous_response_id")
        logger.warning(
            "Large response.create prepared request_id=%s bytes=%s previous_response_id=%s",
            get_request_id(),
            payload_size,
            previous_response_id if isinstance(previous_response_id, str) else None,
        )
    if payload_size <= _UPSTREAM_RESPONSE_CREATE_MAX_BYTES:
        return request_payload
    raise ProxyResponseError(
        413,
        _response_create_too_large_error_envelope(payload_size, _UPSTREAM_RESPONSE_CREATE_MAX_BYTES),
        failure_phase="validation",
        failure_detail=f"response.create_bytes={payload_size}",
    )


def _response_create_too_large_error_envelope(actual_bytes: int, max_bytes: int) -> OpenAIErrorEnvelope:
    payload = openai_error(
        "payload_too_large",
        (
            "response.create is too large for upstream websocket "
            f"({actual_bytes} bytes > {max_bytes} bytes). "
            "Reduce historical images/screenshots or compact the thread."
        ),
        error_type="invalid_request_error",
    )
    payload["error"]["param"] = "input"
    return payload


def _slim_response_create_payload_for_upstream(
    payload: JsonObject,
    *,
    max_bytes: int,
) -> tuple[JsonObject, dict[str, int] | None]:
    del max_bytes
    input_value = payload.get("input")
    if not isinstance(input_value, list) or not input_value:
        return payload, None

    input_items = cast(list[JsonValue], deepcopy(input_value))
    preserve_from = _response_create_recent_suffix_start(input_items)
    historical = input_items[:preserve_from]
    recent = input_items[preserve_from:]

    tool_outputs_slimmed = 0
    images_slimmed = 0

    slimmed_historical: list[JsonValue] = []
    for item in historical:
        slimmed_item, item_tool_outputs_slimmed, item_images_slimmed = _slim_historical_response_input_item(item)
        tool_outputs_slimmed += item_tool_outputs_slimmed
        images_slimmed += item_images_slimmed
        slimmed_historical.append(slimmed_item)

    if tool_outputs_slimmed == 0 and images_slimmed == 0:
        return payload, None

    candidate_payload = dict(payload)
    candidate_payload["input"] = slimmed_historical + recent
    return candidate_payload, {
        "historical_tool_outputs_slimmed": tool_outputs_slimmed,
        "historical_images_slimmed": images_slimmed,
    }


def _response_create_recent_suffix_start(input_items: list[JsonValue]) -> int:
    last_user_index: int | None = None
    for index, item in enumerate(input_items):
        if not is_json_mapping(item):
            continue
        if item.get("role") == "user":
            last_user_index = index
    if last_user_index is not None:
        return last_user_index
    return 0


def _slim_historical_response_input_item(item: JsonValue) -> tuple[JsonValue, int, int]:
    if not is_json_mapping(item):
        return item, 0, 0

    item_mapping = dict(cast(dict[str, JsonValue], deepcopy(item)))
    tool_outputs_slimmed = 0
    images_slimmed = 0

    if item_mapping.get("type") == "function_call_output":
        output = item_mapping.get("output")
        output_text = output if isinstance(output, str) else None
        if output_text is not None and _should_slim_historical_tool_output(output_text):
            item_mapping["output"] = _RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE.format(
                bytes=len(output_text.encode("utf-8"))
            )
            tool_outputs_slimmed += 1

    content = item_mapping.get("content")
    slimmed_content, content_images_slimmed = _slim_historical_response_content(content)
    if content_images_slimmed > 0:
        item_mapping["content"] = slimmed_content
        images_slimmed += content_images_slimmed

    if item_mapping.get("type") == "input_image" and _is_inline_image_reference(item_mapping.get("image_url")):
        return _response_create_inline_image_notice_item(), tool_outputs_slimmed, images_slimmed + 1

    return item_mapping, tool_outputs_slimmed, images_slimmed


def _slim_historical_response_content(content: JsonValue) -> tuple[JsonValue, int]:
    if is_json_mapping(content):
        return _slim_historical_response_content_part(content)
    if not isinstance(content, list):
        return content, 0

    slimmed_parts: list[JsonValue] = []
    images_slimmed = 0
    for part in content:
        slimmed_part, part_images_slimmed = _slim_historical_response_content_part(part)
        slimmed_parts.append(slimmed_part)
        images_slimmed += part_images_slimmed
    return slimmed_parts, images_slimmed


def _slim_historical_response_content_part(part: JsonValue) -> tuple[JsonValue, int]:
    if not is_json_mapping(part):
        return part, 0

    part_mapping = dict(cast(dict[str, JsonValue], deepcopy(part)))
    part_type = part_mapping.get("type")
    if part_type == "input_image" and _is_inline_image_reference(part_mapping.get("image_url")):
        return _response_create_inline_image_notice_part(), 1

    if part_type == "image_url":
        image_url_value = part_mapping.get("image_url")
        if is_json_mapping(image_url_value):
            image_url = image_url_value.get("url")
        else:
            image_url = image_url_value
        if _is_inline_image_reference(image_url):
            return _response_create_inline_image_notice_part(), 1

    return part_mapping, 0


def _response_create_inline_image_notice_part() -> JsonObject:
    return {"type": "input_text", "text": _RESPONSE_CREATE_IMAGE_OMISSION_NOTICE}


def _response_create_inline_image_notice_item() -> JsonObject:
    return {"role": "user", "content": [_response_create_inline_image_notice_part()]}


def _is_inline_image_reference(value: JsonValue) -> bool:
    return isinstance(value, str) and value.startswith("data:image/")


def _should_slim_historical_tool_output(output: str) -> bool:
    return "data:image/" in output or len(output.encode("utf-8")) > 32 * 1024


async def _inline_input_image_urls(
    payload: JsonObject,
    session: "ImageFetchSession",
    connect_timeout: float,
) -> dict[str, JsonValue]:
    payload_dict = dict(payload)
    input_value = payload_dict.get("input")
    if not isinstance(input_value, list):
        return payload_dict
    updated_input: list[JsonValue] = []
    changed = False
    for item in input_value:
        if not isinstance(item, dict):
            updated_input.append(item)
            continue
        content = item.get("content")
        updated_content, content_changed = await _inline_content_images(content, session, connect_timeout)
        if content_changed:
            new_item = dict(item)
            new_item["content"] = updated_content
            updated_input.append(new_item)
            changed = True
        else:
            updated_input.append(item)
    if not changed:
        return payload_dict
    payload_dict["input"] = updated_input
    return payload_dict


async def _inline_content_images(
    content: JsonValue,
    session: "ImageFetchSession",
    connect_timeout: float,
) -> tuple[JsonValue, bool]:
    if content is None:
        return content, False
    parts = content if isinstance(content, list) else [content]
    updated_parts: list[JsonValue] = []
    changed = False
    for part in parts:
        if not isinstance(part, dict):
            updated_parts.append(part)
            continue
        part_type = part.get("type")
        image_url = part.get("image_url") if part_type == "input_image" else None
        if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
            data_url = await _fetch_image_data_url(session, image_url, connect_timeout)
            if data_url:
                new_part = dict(part)
                new_part["image_url"] = data_url
                updated_parts.append(new_part)
                changed = True
                continue
        updated_parts.append(part)
    if isinstance(content, list):
        return updated_parts, changed
    return (updated_parts[0] if updated_parts else ""), changed


async def _fetch_image_data_url(
    session: "ImageFetchSession",
    image_url: str,
    connect_timeout: float,
) -> str | None:
    target = await _resolve_safe_image_fetch_target(image_url, connect_timeout=connect_timeout)
    if target is None:
        return None
    timeout = aiohttp.ClientTimeout(
        total=_IMAGE_INLINE_TIMEOUT_SECONDS,
        sock_connect=connect_timeout,
        sock_read=_IMAGE_INLINE_TIMEOUT_SECONDS,
    )
    headers = {"Host": target.host_header}
    for request_url in target.request_urls:
        try:
            async with session.get(
                request_url,
                timeout=timeout,
                allow_redirects=False,
                headers=headers,
                server_hostname=target.server_hostname,
            ) as resp:
                if resp.status != 200:
                    continue
                content_type = resp.headers.get("Content-Type")
                mime_type = content_type.split(";", 1)[0].strip() if isinstance(content_type, str) else ""
                if not mime_type:
                    mime_type = "application/octet-stream"
                data = bytearray()
                async for chunk in resp.content.iter_chunked(_IMAGE_INLINE_CHUNK_SIZE):
                    if not chunk:
                        continue
                    data.extend(chunk)
                    if len(data) > _IMAGE_INLINE_MAX_BYTES:
                        return None
                if not data:
                    continue
                encoded = base64.b64encode(data).decode("ascii")
                return f"data:{mime_type};base64,{encoded}"
        except (aiohttp.ClientError, asyncio.TimeoutError):
            continue
    return None


@dataclass(slots=True, frozen=True)
class SafeImageFetchTarget:
    request_urls: tuple[str, ...]
    host_header: str
    server_hostname: str


def _build_pinned_request_url(parsed: ParseResult, resolved_ip: str) -> str:
    path = parsed.path or "/"
    ip_host = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    netloc = f"{ip_host}:{parsed_port}" if parsed_port is not None else ip_host
    return urlunparse((parsed.scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))


async def _resolve_safe_image_fetch_target(
    url: str,
    *,
    connect_timeout: float,
) -> SafeImageFetchTarget | None:
    settings = get_settings()
    if not settings.image_inline_fetch_enabled:
        return None

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    if parsed.username or parsed.password:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return None
    if host in _BLOCKED_LITERAL_HOSTS:
        return None

    allowed_hosts = settings.image_inline_allowed_hosts
    if allowed_hosts and host not in allowed_hosts:
        return None

    literal_ip = _parse_ip_literal(host)
    if literal_ip is not None:
        if _is_disallowed_ip(literal_ip):
            return None
        resolved_ips = [literal_ip.compressed]
    else:
        resolve_timeout = min(connect_timeout, _IMAGE_INLINE_TIMEOUT_SECONDS)
        resolved_ips = await _resolve_global_ips(host, timeout_seconds=resolve_timeout)
        if not resolved_ips:
            return None

    request_urls = tuple(_build_pinned_request_url(parsed, resolved_ip) for resolved_ip in resolved_ips)
    if not request_urls:
        return None

    try:
        parsed_port = parsed.port
    except ValueError:
        return None
    host_header = host if parsed_port in (None, 443) else f"{host}:{parsed_port}"
    return SafeImageFetchTarget(
        request_urls=request_urls,
        host_header=host_header,
        server_hostname=host,
    )


async def _is_safe_image_fetch_url(url: str, *, connect_timeout: float) -> bool:
    target = await _resolve_safe_image_fetch_target(url, connect_timeout=connect_timeout)
    return target is not None


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_blocked_ip_literal(host: str) -> bool:
    ip = _parse_ip_literal(host)
    if ip is None:
        return False
    return _is_disallowed_ip(ip)


async def _resolve_global_ips(host: str, *, timeout_seconds: float) -> list[str] | None:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP),
            timeout=timeout_seconds,
        )
    except (OSError, asyncio.TimeoutError):
        return None
    if not infos:
        return None

    resolved_ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            return None
        addr = sockaddr[0]
        ip = _parse_ip_literal(addr)
        if ip is None:
            return None
        if _is_disallowed_ip(ip):
            return None
        normalized_ip = ip.compressed
        if normalized_ip in seen:
            continue
        seen.add(normalized_ip)
        resolved_ips.append(normalized_ip)
    return resolved_ips or None


async def _resolves_to_blocked_ip(host: str, *, timeout_seconds: float) -> bool:
    resolved_ips = await _resolve_global_ips(host, timeout_seconds=timeout_seconds)
    return resolved_ips is None


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_multicast:
        return True
    return not ip.is_global


class ImageFetchContent(Protocol):
    def iter_chunked(self, size: int) -> AsyncIterator[bytes]: ...


class ImageFetchResponse(Protocol):
    status: int
    headers: Mapping[str, str]
    content: ImageFetchContent


class ImageFetchSession(Protocol):
    def get(
        self,
        url: str,
        timeout: aiohttp.ClientTimeout,
        *,
        allow_redirects: bool = False,
        headers: Mapping[str, str] | None = None,
        server_hostname: str | None = None,
    ) -> AsyncContextManager[ImageFetchResponse]: ...


def _as_image_fetch_session(session: aiohttp.ClientSession) -> ImageFetchSession:
    return cast(ImageFetchSession, session)


async def stream_responses(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    raise_for_status: bool = False,
    session: aiohttp.ClientSession | None = None,
    upstream_stream_transport_override: str | None = None,
) -> AsyncIterator[str]:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/codex/responses"
    pre_request_started_at = time.monotonic()
    # Keep a default total timeout so direct callers cannot hang forever before
    # response headers or the first SSE event. ProxyService stream attempts clamp
    # this further by installing per-attempt overrides from the remaining budget.
    request_total_timeout = _effective_stream_timeout(
        settings.proxy_request_budget_seconds,
        "total",
    )
    effective_connect_timeout = _effective_stream_timeout(settings.upstream_connect_timeout_seconds, "connect")
    effective_idle_timeout = _effective_stream_timeout(settings.stream_idle_timeout_seconds, "idle")

    seen_terminal = False
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    client_session = session or get_http_client().session
    payload_dict = payload.to_payload()
    if settings.image_inline_fetch_enabled:
        payload_dict = await _inline_input_image_urls(
            payload_dict,
            _as_image_fetch_session(client_session),
            effective_connect_timeout,
        )
    transport_mode = _configured_stream_transport(
        transport=settings.upstream_stream_transport,
        transport_override=upstream_stream_transport_override,
    )
    transport = _resolve_stream_transport(
        transport=settings.upstream_stream_transport,
        transport_override=upstream_stream_transport_override,
        model=payload.model,
        headers=headers,
        has_image_generation_tool=_payload_uses_image_generation_tool(payload_dict),
    )
    if transport == "websocket":
        upstream_headers = _build_upstream_websocket_headers(headers, access_token, account_id)
        method = "GET"
    else:
        upstream_headers = _build_upstream_headers(headers, access_token, account_id)
        method = "POST"
    remaining_request_timeout = _remaining_total_timeout(
        request_total_timeout,
        pre_request_started_at,
        time.monotonic(),
    )
    timeout = aiohttp.ClientTimeout(
        total=remaining_request_timeout,
        sock_connect=effective_connect_timeout,
        sock_read=None,
    )
    started_at = time.monotonic()

    async def _stream_via_http(
        current_headers: Mapping[str, str],
        current_timeout: aiohttp.ClientTimeout,
    ) -> AsyncIterator[str]:
        nonlocal status_code, error_code, error_message, seen_terminal

        async with _service_circuit_breaker_context(
            client_session.post(
                url,
                json=payload_dict,
                headers=current_headers,
                timeout=current_timeout,
            ),
            settings=settings,
            account_id=account_id,
        ) as resp:
            status_code = resp.status
            if resp.status >= 400:
                if raise_for_status:
                    error_payload = await _error_payload_from_response(resp)
                    error_code, error_message = _error_details_from_envelope(error_payload)
                    archive_json(
                        direction="server_to_codex",
                        kind="responses",
                        transport="http",
                        payload=error_payload,
                        account_id=account_id,
                        method="POST",
                        url=url,
                        status_code=status_code,
                        headers=current_headers,
                    )
                    raise ProxyResponseError(resp.status, error_payload)
                event = await _error_event_from_response(resp)
                error_code, error_message = _error_details_from_failed_event(event)
                event_block = format_sse_event(event)
                archive_text(
                    direction="server_to_codex",
                    kind="responses",
                    transport="http",
                    text=event_block,
                    account_id=account_id,
                    method="POST",
                    url=url,
                    status_code=status_code,
                    headers=current_headers,
                    extra={"event_format": "sse"},
                )
                yield event_block
                return

            async for event_block in _iter_sse_events(
                resp,
                effective_idle_timeout,
                settings.max_sse_event_bytes,
            ):
                event_block = _normalize_sse_event_block(event_block)
                event = parse_sse_event(event_block)
                if event:
                    event_type = event.type
                    if event_type in ("response.completed", "response.failed", "response.incomplete"):
                        seen_terminal = True
                archive_text(
                    direction="server_to_codex",
                    kind="responses",
                    transport="http",
                    text=event_block,
                    account_id=account_id,
                    method="POST",
                    url=url,
                    status_code=status_code,
                    headers=current_headers,
                    extra={"event_format": "sse"},
                )
                yield event_block
                if seen_terminal:
                    break

    _maybe_log_upstream_request_start(
        kind="responses",
        url=url,
        headers=upstream_headers,
        method=method,
        payload_summary=_summarize_json_payload(payload_dict),
        payload_json=json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))
        if settings.log_upstream_request_payload
        else None,
    )
    if transport == "http":
        archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            payload=payload_dict,
            account_id=account_id,
            method=method,
            url=url,
            headers=upstream_headers,
        )
    try:
        if transport == "websocket":
            try:
                async for event_block in _stream_responses_via_websocket(
                    payload_dict=payload_dict,
                    url=url,
                    headers=upstream_headers,
                    client_session=client_session,
                    effective_total_timeout=(remaining_request_timeout or settings.proxy_request_budget_seconds),
                    effective_connect_timeout=effective_connect_timeout,
                    effective_idle_timeout=effective_idle_timeout,
                    max_event_bytes=settings.max_sse_event_bytes,
                    raise_for_status=raise_for_status,
                    account_id=account_id,
                ):
                    if status_code is None:
                        status_code = 101
                    event = parse_sse_event(event_block)
                    if event:
                        event_type = event.type
                        if event_type in ("response.completed", "response.failed", "response.incomplete"):
                            seen_terminal = True
                    yield event_block
                    if seen_terminal:
                        break
            except aiohttp.WSServerHandshakeError as exc:
                if not _should_fallback_to_http_after_websocket_handshake_error(transport_mode, exc):
                    error_payload = _error_payload_from_websocket_handshake_error(exc)
                    error_code, error_message = _error_details_from_envelope(error_payload)
                    status_code = exc.status
                    if error_message is None:
                        error_message = exc.message or str(exc)
                    if error_code is None:
                        error_code = "upstream_error"
                    if raise_for_status:
                        raise ProxyResponseError(exc.status, error_payload) from exc
                    yield format_sse_event(
                        response_failed_event(error_code, error_message, response_id=get_request_id())
                    )
                    return

                logger.warning(
                    "upstream_websocket_handshake_rejected request_id=%s status=%s target=%s retrying_transport=http",
                    get_request_id(),
                    exc.status,
                    _summarize_upstream_target(url),
                )
                _maybe_log_upstream_request_complete(
                    kind="responses",
                    url=url,
                    headers=upstream_headers,
                    method=method,
                    started_at=started_at,
                    status_code=exc.status,
                    error_code="upstream_websocket_handshake_rejected",
                    error_message=str(exc),
                )

                transport = "http"
                upstream_headers = _build_upstream_headers(headers, access_token, account_id)
                method = "POST"
                remaining_request_timeout = _remaining_total_timeout(
                    request_total_timeout,
                    pre_request_started_at,
                    time.monotonic(),
                )
                timeout = aiohttp.ClientTimeout(
                    total=remaining_request_timeout,
                    sock_connect=effective_connect_timeout,
                    sock_read=None,
                )
                started_at = time.monotonic()
                _maybe_log_upstream_request_start(
                    kind="responses",
                    url=url,
                    headers=upstream_headers,
                    method=method,
                    payload_summary=_summarize_json_payload(payload_dict),
                    payload_json=json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))
                    if settings.log_upstream_request_payload
                    else None,
                )
                archive_json(
                    direction="codex_to_server",
                    kind="responses",
                    transport="http",
                    payload=payload_dict,
                    account_id=account_id,
                    method=method,
                    url=url,
                    headers=upstream_headers,
                )
                async for event_block in _stream_via_http(upstream_headers, timeout):
                    yield event_block
        else:
            async for event_block in _stream_via_http(upstream_headers, timeout):
                yield event_block
    except ProxyResponseError as exc:
        status_code = exc.status_code
        raise
    except StreamIdleTimeoutError:
        error_code = "stream_idle_timeout"
        error_message = "Upstream stream idle timeout"
        yield format_sse_event(
            response_failed_event(
                "stream_idle_timeout",
                "Upstream stream idle timeout",
                response_id=get_request_id(),
            ),
        )
        return
    except StreamEventTooLargeError as exc:
        error_code = "stream_event_too_large"
        error_message = str(exc)
        yield format_sse_event(
            response_failed_event(
                "stream_event_too_large",
                str(exc),
                response_id=get_request_id(),
            ),
        )
        return
    except CircuitBreakerOpenError:
        error_code = "upstream_unavailable"
        error_message = "Upstream circuit breaker is open"
        yield format_sse_event(
            response_failed_event("upstream_unavailable", error_message, response_id=get_request_id()),
        )
        return
    except aiohttp.ClientError as exc:
        error_code = "upstream_unavailable"
        error_message = str(exc)
        yield format_sse_event(
            response_failed_event("upstream_unavailable", str(exc), response_id=get_request_id()),
        )
        return
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError as exc:
        if isinstance(exc, aiohttp.ClientError):
            error_code = "upstream_unavailable"
            error_message = str(exc) or "Request to upstream timed out"
            yield format_sse_event(
                response_failed_event("upstream_unavailable", error_message, response_id=get_request_id()),
            )
            return
        error_code = "upstream_request_timeout"
        error_message = "Proxy request budget exhausted"
        yield format_sse_event(
            response_failed_event(
                "upstream_request_timeout",
                "Proxy request budget exhausted",
                response_id=get_request_id(),
            ),
        )
        return
    except Exception as exc:
        error_code = "upstream_error"
        error_message = str(exc)
        yield format_sse_event(response_failed_event("upstream_error", str(exc), response_id=get_request_id()))
        return
    else:
        if not seen_terminal:
            error_code = "stream_incomplete"
            error_message = "Upstream closed stream without completion"
            yield format_sse_event(
                response_failed_event(
                    "stream_incomplete",
                    "Upstream closed stream without completion",
                    response_id=get_request_id(),
                ),
            )
    finally:
        _maybe_log_upstream_request_complete(
            kind="responses",
            url=url,
            headers=upstream_headers,
            method=method,
            started_at=started_at,
            status_code=status_code,
            error_code=error_code,
            error_message=error_message,
        )


def push_stream_timeout_overrides(
    *,
    connect_timeout_seconds: float | None = None,
    idle_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
) -> tuple[
    float | None,
    float | None,
    float | None,
]:
    previous = (
        _STREAM_CONNECT_TIMEOUT_OVERRIDE.get(),
        _STREAM_IDLE_TIMEOUT_OVERRIDE.get(),
        _STREAM_TOTAL_TIMEOUT_OVERRIDE.get(),
    )
    _STREAM_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout_seconds)
    _STREAM_IDLE_TIMEOUT_OVERRIDE.set(idle_timeout_seconds)
    _STREAM_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout_seconds)
    return previous


def pop_stream_timeout_overrides(
    tokens: tuple[
        float | None,
        float | None,
        float | None,
    ],
) -> None:
    connect_timeout, idle_timeout, total_timeout = tokens
    _STREAM_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout)
    _STREAM_IDLE_TIMEOUT_OVERRIDE.set(idle_timeout)
    _STREAM_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout)


@contextlib.contextmanager
def override_stream_timeouts(
    *,
    connect_timeout_seconds: float | None = None,
    idle_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
):
    tokens = push_stream_timeout_overrides(
        connect_timeout_seconds=connect_timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        total_timeout_seconds=total_timeout_seconds,
    )
    try:
        yield
    finally:
        pop_stream_timeout_overrides(tokens)


def push_compact_timeout_overrides(
    *,
    connect_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
) -> tuple[contextvars.Token[float | None], contextvars.Token[float | None]]:
    return (
        _COMPACT_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout_seconds),
        _COMPACT_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout_seconds),
    )


def pop_compact_timeout_overrides(
    tokens: tuple[contextvars.Token[float | None], contextvars.Token[float | None]],
) -> None:
    connect_token, total_token = tokens
    _COMPACT_CONNECT_TIMEOUT_OVERRIDE.reset(connect_token)
    _COMPACT_TOTAL_TIMEOUT_OVERRIDE.reset(total_token)


def push_transcribe_timeout_overrides(
    *,
    connect_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
) -> tuple[contextvars.Token[float | None], contextvars.Token[float | None]]:
    return (
        _TRANSCRIBE_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout_seconds),
        _TRANSCRIBE_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout_seconds),
    )


def pop_transcribe_timeout_overrides(
    tokens: tuple[contextvars.Token[float | None], contextvars.Token[float | None]],
) -> None:
    connect_token, total_token = tokens
    _TRANSCRIBE_CONNECT_TIMEOUT_OVERRIDE.reset(connect_token)
    _TRANSCRIBE_TOTAL_TIMEOUT_OVERRIDE.reset(total_token)


async def compact_responses(
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    session: aiohttp.ClientSession | None = None,
) -> CompactResponsePayload:
    transport = _CompactCommandTransport(
        payload=payload,
        headers=headers,
        access_token=access_token,
        account_id=account_id,
        session=session or get_http_client().session,
    )
    return await transport.execute()


def _is_retryable_compact_status(status_code: int) -> bool:
    return status_code in {401, 500, 502, 503, 504}


@dataclass(slots=True)
class _CompactCommandTransport:
    payload: ResponsesCompactRequest
    headers: Mapping[str, str]
    access_token: str
    account_id: str | None
    session: aiohttp.ClientSession

    async def execute(self) -> CompactResponsePayload:
        settings = get_settings()
        upstream_base = settings.upstream_base_url.rstrip("/")
        url = f"{upstream_base}/codex/responses/compact"
        upstream_headers = _build_upstream_headers(
            self.headers,
            self.access_token,
            self.account_id,
            accept="application/json",
        )
        pre_request_started_at = time.monotonic()
        compact_timeout_seconds = _effective_compact_total_timeout(settings.upstream_compact_timeout_seconds)
        effective_connect_timeout = _effective_compact_connect_timeout(settings.upstream_connect_timeout_seconds)
        payload_dict = self.payload.to_payload()
        if settings.image_inline_fetch_enabled:
            payload_dict = await _inline_input_image_urls(
                payload_dict,
                _as_image_fetch_session(self.session),
                effective_connect_timeout,
            )
        now = time.monotonic()
        compact_timeout_seconds = _remaining_total_timeout(
            compact_timeout_seconds,
            pre_request_started_at,
            now,
        )
        effective_connect_timeout = max(
            0.001,
            _remaining_total_timeout(
                effective_connect_timeout,
                pre_request_started_at,
                now,
            )
            or effective_connect_timeout,
        )
        timeout = aiohttp.ClientTimeout(
            total=compact_timeout_seconds,
            sock_connect=effective_connect_timeout,
            sock_read=compact_timeout_seconds,
        )
        started_at = time.monotonic()
        status_code: int | None = None
        error_code: str | None = None
        error_message: str | None = None
        failure_phase: str | None = None
        payload_object: str | None = None
        failure_detail: str | None = None
        failure_exception_type: str | None = None
        retryable_same_contract: bool | None = None
        _maybe_log_upstream_request_start(
            kind="responses_compact",
            url=url,
            headers=upstream_headers,
            method="POST",
            payload_summary=_summarize_json_payload(payload_dict),
            payload_json=json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))
            if settings.log_upstream_request_payload
            else None,
        )
        archive_json(
            direction="codex_to_server",
            kind="compact",
            transport="http",
            payload=payload_dict,
            account_id=self.account_id,
            method="POST",
            url=url,
            headers=upstream_headers,
        )
        try:
            async with _service_circuit_breaker_context(
                self.session.post(
                    url,
                    json=payload_dict,
                    headers=upstream_headers,
                    timeout=timeout,
                ),
                settings=settings,
                account_id=self.account_id,
            ) as resp:
                status_code = resp.status
                if resp.status >= 400:
                    error_payload = await _error_payload_from_response(resp)
                    archive_json(
                        direction="server_to_codex",
                        kind="compact",
                        transport="http",
                        payload=error_payload,
                        account_id=self.account_id,
                        method="POST",
                        url=url,
                        status_code=status_code,
                        headers=upstream_headers,
                    )
                    error_code, error_message = _error_details_from_envelope(error_payload)
                    failure_phase = "status"
                    failure_detail = error_message
                    retryable_same_contract = _is_retryable_compact_status(resp.status)
                    raise ProxyResponseError(
                        resp.status,
                        error_payload,
                        failure_phase=failure_phase,
                        retryable_same_contract=retryable_same_contract,
                        failure_detail=failure_detail,
                        upstream_status_code=resp.status,
                    )
                try:
                    data = await resp.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    message = str(exc) or "Request to upstream timed out"
                    error_code = "upstream_unavailable"
                    error_message = message
                    failure_phase = "body_read"
                    failure_detail = message
                    failure_exception_type = type(exc).__name__
                    retryable_same_contract = True
                    raise ProxyResponseError(
                        502,
                        openai_error("upstream_unavailable", message),
                        failure_phase=failure_phase,
                        retryable_same_contract=retryable_same_contract,
                        failure_detail=failure_detail,
                        failure_exception_type=failure_exception_type,
                        upstream_status_code=resp.status,
                    ) from exc
                except Exception as exc:
                    error_code = "upstream_error"
                    error_message = "Invalid JSON from upstream"
                    failure_phase = "parse"
                    failure_detail = str(exc) or error_message
                    failure_exception_type = type(exc).__name__
                    raise ProxyResponseError(
                        502,
                        openai_error("upstream_error", "Invalid JSON from upstream"),
                        failure_phase=failure_phase,
                        failure_detail=failure_detail,
                        failure_exception_type=failure_exception_type,
                        upstream_status_code=resp.status,
                    ) from exc
                parsed = parse_compact_response_payload(data)
                archive_json(
                    direction="server_to_codex",
                    kind="compact",
                    transport="http",
                    payload=data,
                    account_id=self.account_id,
                    method="POST",
                    url=url,
                    status_code=status_code,
                    headers=upstream_headers,
                )
                if parsed:
                    payload_object = parsed.object
                    return parsed
                error_code = "upstream_error"
                error_message = "Unexpected upstream payload"
                failure_phase = "parse"
                failure_detail = f"payload_type={type(data).__name__}"
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_error", "Unexpected upstream payload"),
                    failure_phase=failure_phase,
                    failure_detail=failure_detail,
                    upstream_status_code=resp.status,
                )
        except ProxyResponseError as exc:
            if error_code is None and error_message is None:
                error_code, error_message = _error_details_from_envelope(exc.payload)
            failure_phase = failure_phase or exc.failure_phase
            failure_detail = failure_detail or exc.failure_detail
            failure_exception_type = failure_exception_type or exc.failure_exception_type
            if retryable_same_contract is None:
                retryable_same_contract = exc.retryable_same_contract
            raise
        except CircuitBreakerOpenError as exc:
            error_code = "upstream_unavailable"
            error_message = "Upstream circuit breaker is open"
            failure_phase = "connect"
            failure_detail = str(exc)
            failure_exception_type = type(exc).__name__
            retryable_same_contract = True
            raise ProxyResponseError(
                503,
                openai_error("upstream_unavailable", error_message),
                failure_phase=failure_phase,
                retryable_same_contract=retryable_same_contract,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
            ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            message = str(exc) or "Request to upstream timed out"
            error_code = "upstream_unavailable"
            error_message = message
            failure_phase = "connect"
            failure_detail = message
            failure_exception_type = type(exc).__name__
            retryable_same_contract = True
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", message),
                failure_phase=failure_phase,
                retryable_same_contract=retryable_same_contract,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
            ) from exc
        finally:
            _maybe_log_upstream_request_complete(
                kind="responses_compact",
                url=url,
                headers=upstream_headers,
                method="POST",
                started_at=started_at,
                status_code=status_code,
                error_code=error_code,
                error_message=error_message,
                failure_phase=failure_phase,
                payload_object=payload_object,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
                retryable_same_contract=retryable_same_contract,
            )


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str | None,
    prompt: str | None,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, JsonValue]:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/transcribe"
    upstream_headers = _build_upstream_transcribe_headers(
        headers,
        access_token,
        account_id,
    )

    effective_total_timeout = _effective_transcribe_total_timeout(
        settings.transcription_request_budget_seconds,
    )
    effective_connect_timeout = _effective_transcribe_connect_timeout(settings.upstream_connect_timeout_seconds)
    timeout = aiohttp.ClientTimeout(
        total=effective_total_timeout,
        sock_connect=effective_connect_timeout,
        sock_read=effective_total_timeout,
    )

    normalized_filename = filename.strip() if filename else ""
    if not normalized_filename:
        normalized_filename = "audio.wav"
    normalized_content_type = content_type.strip() if content_type else ""
    if not normalized_content_type:
        normalized_content_type = "application/octet-stream"

    form = aiohttp.FormData()
    form.add_field(
        "file",
        audio_bytes,
        filename=normalized_filename,
        content_type=normalized_content_type,
    )
    if prompt is not None:
        form.add_field("prompt", prompt)

    client_session = session or get_http_client().session
    started_at = time.monotonic()
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata = _summarize_transcription_payload(
        filename=normalized_filename,
        content_type=normalized_content_type,
        prompt=prompt,
        audio_bytes=audio_bytes,
    )
    _maybe_log_upstream_request_start(
        kind="transcribe",
        url=url,
        headers=upstream_headers,
        method="POST",
        payload_summary=json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
        payload_json=json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
        if settings.log_upstream_request_payload
        else None,
    )
    try:
        async with _service_circuit_breaker_context(
            client_session.post(
                url,
                data=form,
                headers=upstream_headers,
                timeout=timeout,
            ),
            settings=settings,
            account_id=account_id,
        ) as resp:
            status_code = resp.status
            if resp.status >= 400:
                error_payload = await _error_payload_from_response(resp)
                error_code, error_message = _error_details_from_envelope(error_payload)
                raise ProxyResponseError(resp.status, error_payload)
            try:
                data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                message = str(exc) or "Request to upstream timed out"
                error_code = "upstream_unavailable"
                error_message = message
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_unavailable", message),
                ) from exc
            except Exception as exc:
                error_code = "upstream_error"
                error_message = "Invalid JSON from upstream"
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_error", "Invalid JSON from upstream"),
                ) from exc
            if isinstance(data, dict):
                return data
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "Unexpected upstream payload"),
            )
    except ProxyResponseError as exc:
        if error_code is None and error_message is None:
            error_code, error_message = _error_details_from_envelope(exc.payload)
        raise
    except CircuitBreakerOpenError as exc:
        error_code = "upstream_unavailable"
        error_message = "Upstream circuit breaker is open"
        raise ProxyResponseError(
            503,
            openai_error("upstream_unavailable", error_message),
        ) from exc
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        message = str(exc) or "Request to upstream timed out"
        error_code = "upstream_unavailable"
        error_message = message
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message),
        ) from exc
    finally:
        _maybe_log_upstream_request_complete(
            kind="transcribe",
            url=url,
            headers=upstream_headers,
            method="POST",
            started_at=started_at,
            status_code=status_code,
            error_code=error_code,
            error_message=error_message,
        )
