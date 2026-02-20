from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
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
    buffer = bytearray()
    chunk_iterator = resp.content.iter_chunked(_SSE_READ_CHUNK_SIZE)
    iterator = chunk_iterator.__aiter__()

    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=idle_timeout_seconds)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError as exc:
            raise StreamIdleTimeoutError() from exc

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
    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=settings.upstream_connect_timeout_seconds,
        sock_read=None,
    )

    seen_terminal = False
    client_session = session or get_http_client().session
    payload_dict = payload.to_payload()
    if settings.image_inline_fetch_enabled:
        payload_dict = await _inline_input_image_urls(
            payload_dict,
            _as_image_fetch_session(client_session),
            settings.upstream_connect_timeout_seconds,
        )
    try:
        async with client_session.post(
            url,
            json=payload_dict,
            headers=upstream_headers,
            timeout=timeout,
        ) as resp:
            if resp.status >= 400:
                if raise_for_status:
                    error_payload = await _error_payload_from_response(resp)
                    raise ProxyResponseError(resp.status, error_payload)
                event = await _error_event_from_response(resp)
                yield format_sse_event(event)
                return

            async for event_block in _iter_sse_events(
                resp,
                settings.stream_idle_timeout_seconds,
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
        yield format_sse_event(
            response_failed_event(
                "stream_idle_timeout",
                "Upstream stream idle timeout",
                response_id=get_request_id(),
            ),
        )
        return
    except StreamEventTooLargeError as exc:
        yield format_sse_event(
            response_failed_event(
                "stream_event_too_large",
                str(exc),
                response_id=get_request_id(),
            ),
        )
        return
    except aiohttp.ClientError as exc:
        yield format_sse_event(
            response_failed_event("upstream_unavailable", str(exc), response_id=get_request_id()),
        )
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield format_sse_event(response_failed_event("upstream_error", str(exc), response_id=get_request_id()))
        return

    if not seen_terminal:
        yield format_sse_event(
            response_failed_event(
                "stream_incomplete",
                "Upstream closed stream without completion",
                response_id=get_request_id(),
            ),
        )


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
    timeout = aiohttp.ClientTimeout(
        total=60,
        sock_connect=settings.upstream_connect_timeout_seconds,
        sock_read=60,
    )

    client_session = session or get_http_client().session
    payload_dict = payload.to_payload()
    if settings.image_inline_fetch_enabled:
        payload_dict = await _inline_input_image_urls(
            payload_dict,
            _as_image_fetch_session(client_session),
            settings.upstream_connect_timeout_seconds,
        )
    try:
        async with client_session.post(
            url,
            json=payload_dict,
            headers=upstream_headers,
            timeout=timeout,
        ) as resp:
            if resp.status >= 400:
                error_payload = await _error_payload_from_response(resp)
                raise ProxyResponseError(resp.status, error_payload)
            try:
                data = await resp.json(content_type=None)
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
    except ProxyResponseError:
        raise
    except aiohttp.ClientError as exc:
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", str(exc)),
        ) from exc
