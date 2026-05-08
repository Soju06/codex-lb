from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import aiohttp
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from app.core.clients.http import get_http_client
from app.core.config.settings import Settings, get_settings
from app.core.types import JsonValue
from app.core.utils.sse import parse_sse_data_json

if TYPE_CHECKING:
    from app.modules.api_keys.service import ApiKeyData

logger = logging.getLogger(__name__)

PEER_FALLBACK_DEPTH_HEADER = "x-codex-lb-peer-fallback-depth"
PEER_FALLBACK_REASON_HEADER = "x-codex-lb-peer-fallback-reason"

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_REQUEST_STRIP_HEADERS = _HOP_BY_HOP_HEADERS | {
    "cookie",
    "host",
    "content-length",
}
_RESPONSE_STRIP_HEADERS = _HOP_BY_HOP_HEADERS | {
    "content-length",
}
_STREAM_CHUNK_SIZE = 64 * 1024


@dataclass(slots=True)
class _PeerStream:
    response: aiohttp.ClientResponse

    async def iter_body(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.response.content.iter_chunked(_STREAM_CHUNK_SIZE):
                if chunk:
                    yield chunk
        finally:
            self.response.release()


def error_code_from_sse_event(event_block: str) -> str | None:
    payload = parse_sse_data_json(event_block)
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    if event_type == "error":
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            return code if isinstance(code, str) else None
        return None
    if event_type != "response.failed":
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    error = response.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def error_code_from_envelope(payload: Mapping[str, object]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = cast(Mapping[str, object], error).get("code")
    return code if isinstance(code, str) else None


async def open_stream_response(
    request: Request,
    payload: Mapping[str, JsonValue],
    *,
    reason_code: str | None,
    api_key: "ApiKeyData | None" = None,
) -> Response | None:
    settings = get_settings()
    peer_base_urls = await _effective_peer_base_urls(api_key)
    if not _is_eligible(request, settings, reason_code=reason_code, peer_base_urls=peer_base_urls):
        return None
    peer = await _open_peer_stream(
        request,
        payload,
        settings=settings,
        reason_code=reason_code,
        peer_base_urls=peer_base_urls,
    )
    if peer is None:
        return None
    content_type = peer.response.headers.get("content-type")
    return StreamingResponse(
        peer.iter_body(),
        status_code=peer.response.status,
        headers=_response_headers(peer.response.headers),
        media_type=content_type,
    )


async def open_buffered_response(
    request: Request,
    payload: Mapping[str, JsonValue],
    *,
    reason_code: str | None,
    api_key: "ApiKeyData | None" = None,
) -> Response | None:
    settings = get_settings()
    peer_base_urls = await _effective_peer_base_urls(api_key)
    if not _is_eligible(request, settings, reason_code=reason_code, peer_base_urls=peer_base_urls):
        return None

    session = get_http_client().session
    timeout = aiohttp.ClientTimeout(total=settings.peer_fallback_timeout_seconds)
    for base_url in peer_base_urls:
        if not await _peer_is_healthy(session, base_url, timeout=timeout):
            _log_peer_skip(request, base_url, reason_code, "health_check_failed")
            continue
        try:
            response = await session.request(
                request.method,
                _target_url(base_url, request),
                json=dict(payload),
                headers=_request_headers(request.headers, settings=settings, reason_code=reason_code),
                timeout=timeout,
                auto_decompress=False,
            )
            try:
                body = await response.read()
                return Response(
                    content=body,
                    status_code=response.status,
                    headers=_response_headers(response.headers),
                    media_type=response.headers.get("content-type"),
                )
            finally:
                response.release()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning(
                "peer_fallback_buffered_failed path=%s peer=%s reason_code=%s",
                request.url.path,
                base_url,
                reason_code,
                exc_info=True,
            )
    return None


def _is_eligible(
    request: Request,
    settings: Settings,
    *,
    reason_code: str | None,
    peer_base_urls: list[str],
) -> bool:
    if not peer_base_urls:
        _log_no_fallback(request, reason_code, "no_peers_configured")
        return False
    if reason_code not in set(settings.peer_fallback_error_codes):
        _log_no_fallback(request, reason_code, "error_code_not_enabled")
        return False
    if settings.peer_fallback_max_hops <= 0:
        _log_no_fallback(request, reason_code, "disabled")
        return False
    if _has_fallback_marker(request.headers):
        _log_no_fallback(request, reason_code, "loop_prevention")
        return False
    if request.method.upper() != "POST":
        _log_no_fallback(request, reason_code, "unsupported_method")
        return False
    if _is_internal_bridge_path(request.url.path):
        _log_no_fallback(request, reason_code, "internal_bridge_request")
        return False
    return True


async def _open_peer_stream(
    request: Request,
    payload: Mapping[str, JsonValue],
    *,
    settings: Settings,
    reason_code: str | None,
    peer_base_urls: list[str],
) -> _PeerStream | None:
    session = get_http_client().session
    health_timeout = aiohttp.ClientTimeout(total=settings.peer_fallback_timeout_seconds)
    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=settings.peer_fallback_timeout_seconds,
        sock_connect=settings.peer_fallback_timeout_seconds,
    )
    for base_url in peer_base_urls:
        if not await _peer_is_healthy(session, base_url, timeout=health_timeout):
            _log_peer_skip(request, base_url, reason_code, "health_check_failed")
            continue
        try:
            async with asyncio.timeout(settings.peer_fallback_timeout_seconds):
                response = await session.request(
                    request.method,
                    _target_url(base_url, request),
                    json=dict(payload),
                    headers=_request_headers(request.headers, settings=settings, reason_code=reason_code),
                    timeout=timeout,
                    auto_decompress=False,
                )
            logger.info(
                "peer_fallback_stream_selected path=%s peer=%s reason_code=%s status=%s",
                request.url.path,
                base_url,
                reason_code,
                response.status,
            )
            return _PeerStream(response=response)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning(
                "peer_fallback_stream_failed path=%s peer=%s reason_code=%s",
                request.url.path,
                base_url,
                reason_code,
                exc_info=True,
            )
    return None


async def _effective_peer_base_urls(api_key: "ApiKeyData | None") -> list[str]:
    if api_key is None:
        return []
    return list(api_key.peer_fallback_base_urls)


async def _peer_is_healthy(
    session: aiohttp.ClientSession,
    base_url: str,
    *,
    timeout: aiohttp.ClientTimeout,
) -> bool:
    try:
        async with session.get(f"{base_url.rstrip('/')}/health", timeout=timeout) as response:
            return 200 <= response.status < 300
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False


def _target_url(base_url: str, request: Request) -> str:
    path = request.url.path
    query = request.url.query
    target = f"{base_url.rstrip('/')}{path}"
    if query:
        target = f"{target}?{query}"
    return target


def _request_headers(
    headers: Mapping[str, str],
    *,
    settings: Settings,
    reason_code: str | None,
) -> dict[str, str]:
    forwarded = {key: value for key, value in headers.items() if key.lower() not in _REQUEST_STRIP_HEADERS}
    authorization = _peer_authorization_header(settings)
    if authorization is not None:
        forwarded = {key: value for key, value in forwarded.items() if key.lower() != "authorization"}
        forwarded["authorization"] = authorization
    forwarded[PEER_FALLBACK_DEPTH_HEADER] = str(min(_fallback_depth(headers) + 1, settings.peer_fallback_max_hops))
    if reason_code:
        forwarded[PEER_FALLBACK_REASON_HEADER] = reason_code
    return forwarded


def _peer_authorization_header(settings: Settings) -> str | None:
    api_key = getattr(settings, "codex_api_key", None)
    if api_key is None:
        return None
    if hasattr(api_key, "get_secret_value"):
        value = api_key.get_secret_value()
    else:
        value = str(api_key)
    stripped = value.strip()
    if not stripped:
        return None
    return f"Bearer {stripped}"


def _response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in _RESPONSE_STRIP_HEADERS}


def _fallback_depth(headers: Mapping[str, str]) -> int:
    raw_depth = headers.get(PEER_FALLBACK_DEPTH_HEADER) or headers.get(PEER_FALLBACK_DEPTH_HEADER.title())
    if raw_depth is None:
        return 0
    try:
        return max(0, int(raw_depth))
    except ValueError:
        return 1


def _has_fallback_marker(headers: Mapping[str, str]) -> bool:
    header_names = {key.lower() for key in headers}
    return PEER_FALLBACK_DEPTH_HEADER in header_names or PEER_FALLBACK_REASON_HEADER in header_names


def _is_internal_bridge_path(path: str) -> bool:
    return path.startswith("/internal/bridge/")


def _log_no_fallback(request: Request, reason_code: str | None, reason: str) -> None:
    logger.info(
        "peer_fallback_not_attempted path=%s reason_code=%s no_fallback_reason=%s",
        request.url.path,
        reason_code,
        reason,
    )


def _log_peer_skip(request: Request, peer: str, reason_code: str | None, reason: str) -> None:
    logger.info(
        "peer_fallback_peer_skipped path=%s peer=%s reason_code=%s skip_reason=%s",
        request.url.path,
        peer,
        reason_code,
        reason,
    )
