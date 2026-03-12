from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import ipaddress
import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import AsyncContextManager, AsyncIterator, Awaitable, Mapping, Protocol, TypeAlias, cast
from urllib.parse import ParseResult, urlparse, urlunparse

import aiohttp

from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings
from app.core.errors import OpenAIErrorEnvelope, ResponseFailedEvent, openai_error, response_failed_event
from app.core.openai.models import OpenAIResponsePayload
from app.core.openai.parsing import parse_error_payload, parse_response_payload, parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonObject, JsonValue
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

_SSE_READ_CHUNK_SIZE = 8 * 1024
_IMAGE_INLINE_MAX_BYTES = 8 * 1024 * 1024
_IMAGE_INLINE_CHUNK_SIZE = 64 * 1024
_IMAGE_INLINE_TIMEOUT_SECONDS = 8.0
_BLOCKED_LITERAL_HOSTS = {"localhost", "localhost.localdomain"}
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

    def __init__(self, status_code: int, payload: OpenAIErrorEnvelope) -> None:
        super().__init__(f"Proxy response error ({status_code})")
        self.status_code = status_code
        self.payload = payload


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


def _maybe_log_upstream_request_start(
    *,
    kind: str,
    url: str,
    headers: Mapping[str, str],
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
            "upstream_request_start request_id=%s kind=%s method=POST target=%s account_id=%s headers=%s payload=%s",
            request_id,
            kind,
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
    started_at: float,
    status_code: int | None,
    error_code: str | None,
    error_message: str | None,
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
            "upstream_request_complete request_id=%s kind=%s method=POST target=%s "
            "account_id=%s status=%s duration_ms=%s error_code=%s error_message=%s"
        ),
        get_request_id(),
        kind,
        _summarize_upstream_target(url),
        headers.get("chatgpt-account-id"),
        status_code,
        int((time.monotonic() - started_at) * 1000),
        error_code,
        error_message,
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
            return {"error": error.model_dump(exclude_none=True)}
        message = _extract_upstream_message(data)
        if message:
            return openai_error("upstream_error", message)
    return openai_error("upstream_error", fallback_message)


def _extract_upstream_message(data: Mapping[str, object]) -> str | None:
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
) -> AsyncIterator[str]:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/codex/responses"
    upstream_headers = _build_upstream_headers(headers, access_token, account_id)
    pre_request_started_at = time.monotonic()
    # Keep a default total timeout so direct callers cannot hang forever before
    # response headers or the first SSE event. ProxyService stream attempts clamp
    # this further by installing per-attempt overrides from the remaining budget.
    effective_total_timeout = _effective_stream_timeout(
        getattr(settings, "proxy_request_budget_seconds", 75.0),
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
    effective_total_timeout = _remaining_total_timeout(
        effective_total_timeout,
        pre_request_started_at,
        time.monotonic(),
    )
    timeout = aiohttp.ClientTimeout(
        total=effective_total_timeout,
        sock_connect=effective_connect_timeout,
        sock_read=None,
    )
    started_at = time.monotonic()
    _maybe_log_upstream_request_start(
        kind="responses",
        url=url,
        headers=upstream_headers,
        payload_summary=_summarize_json_payload(payload_dict),
        payload_json=json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))
        if settings.log_upstream_request_payload
        else None,
    )
    try:
        async with client_session.post(
            url,
            json=payload_dict,
            headers=upstream_headers,
            timeout=timeout,
        ) as resp:
            status_code = resp.status
            if resp.status >= 400:
                if raise_for_status:
                    error_payload = await _error_payload_from_response(resp)
                    error_code, error_message = _error_details_from_envelope(error_payload)
                    raise ProxyResponseError(resp.status, error_payload)
                event = await _error_event_from_response(resp)
                error_code, error_message = _error_details_from_failed_event(event)
                yield format_sse_event(event)
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
                yield event_block
    except ProxyResponseError:
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
) -> OpenAIResponsePayload:
    settings = get_settings()
    upstream_base = settings.upstream_base_url.rstrip("/")
    url = f"{upstream_base}/codex/responses/compact"
    upstream_headers = _build_upstream_headers(
        headers,
        access_token,
        account_id,
        accept="application/json",
    )
    pre_request_started_at = time.monotonic()
    compact_timeout_seconds = _effective_compact_total_timeout(settings.upstream_compact_timeout_seconds)
    effective_connect_timeout = _effective_compact_connect_timeout(settings.upstream_connect_timeout_seconds)

    client_session = session or get_http_client().session
    payload_dict = payload.to_payload()
    if settings.image_inline_fetch_enabled:
        payload_dict = await _inline_input_image_urls(
            payload_dict,
            _as_image_fetch_session(client_session),
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
        _remaining_total_timeout(effective_connect_timeout, pre_request_started_at, now) or effective_connect_timeout,
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
    _maybe_log_upstream_request_start(
        kind="responses_compact",
        url=url,
        headers=upstream_headers,
        payload_summary=_summarize_json_payload(payload_dict),
        payload_json=json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))
        if settings.log_upstream_request_payload
        else None,
    )
    try:
        async with client_session.post(
            url,
            json=payload_dict,
            headers=upstream_headers,
            timeout=timeout,
        ) as resp:
            status_code = resp.status
            if resp.status >= 400:
                error_payload = await _error_payload_from_response(resp)
                error_code, error_message = _error_details_from_envelope(error_payload)
                raise ProxyResponseError(resp.status, error_payload)
            try:
                data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                error_code = "upstream_unavailable"
                error_message = str(exc)
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_unavailable", str(exc)),
                ) from exc
            except Exception as exc:
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_error", "Invalid JSON from upstream"),
                ) from exc
            parsed = parse_response_payload(data)
            if parsed:
                return parsed
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "Unexpected upstream payload"),
            )
    except ProxyResponseError as exc:
        if error_code is None and error_message is None:
            error_code, error_message = _error_details_from_envelope(exc.payload)
        raise
    except aiohttp.ClientError as exc:
        error_code = "upstream_unavailable"
        error_message = str(exc)
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", str(exc)),
        ) from exc
    finally:
        _maybe_log_upstream_request_complete(
            kind="responses_compact",
            url=url,
            headers=upstream_headers,
            started_at=started_at,
            status_code=status_code,
            error_code=error_code,
            error_message=error_message,
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
    upstream_headers = _build_upstream_headers(
        headers,
        access_token,
        account_id,
        accept="application/json",
    )
    for header_name in tuple(upstream_headers):
        if header_name.lower() == "content-type":
            upstream_headers.pop(header_name, None)

    effective_total_timeout = _effective_transcribe_total_timeout(
        getattr(settings, "transcription_request_budget_seconds", 120.0),
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
        payload_summary=json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
        payload_json=json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
        if settings.log_upstream_request_payload
        else None,
    )
    try:
        async with client_session.post(
            url,
            data=form,
            headers=upstream_headers,
            timeout=timeout,
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
            started_at=started_at,
            status_code=status_code,
            error_code=error_code,
            error_message=error_message,
        )
