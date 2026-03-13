from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urlparse, urlunparse

from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as websocket_connect
from websockets.datastructures import Headers
from websockets.exceptions import (
    ConnectionClosedError,
    ConnectionClosedOK,
    InvalidHandshake,
    InvalidProxy,
    InvalidStatus,
)
from websockets.typing import Origin

from app.core.clients.proxy import ProxyResponseError, filter_inbound_headers
from app.core.config.settings import get_settings
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.openai.parsing import parse_error_payload
from app.core.utils.request_id import get_request_id

_WEBSOCKET_HOP_BY_HOP_HEADERS = {
    "accept",
    "connection",
    "content-type",
    "cookie",
    "sec-websocket-extensions",
    "sec-websocket-key",
    "sec-websocket-protocol",
    "sec-websocket-version",
    "upgrade",
}
_RESPONSES_WEBSOCKET_BETA_HEADER = "responses_websockets=2026-02-06"


@dataclass(slots=True)
class UpstreamWebSocketMessage:
    kind: str
    text: str | None = None
    data: bytes | None = None
    close_code: int | None = None
    error: str | None = None


class UpstreamResponsesWebSocket(Protocol):
    async def send_text(self, text: str) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def receive(self) -> UpstreamWebSocketMessage: ...

    async def close(self) -> None: ...


class WebsocketsResponsesWebSocket:
    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection

    async def send_text(self, text: str) -> None:
        await self._connection.send(text)

    async def send_bytes(self, data: bytes) -> None:
        await self._connection.send(data)

    async def receive(self) -> UpstreamWebSocketMessage:
        try:
            message = await self._connection.recv()
        except ConnectionClosedOK as exc:
            return UpstreamWebSocketMessage(kind="close", close_code=_close_code_from_exception(exc))
        except ConnectionClosedError as exc:
            return UpstreamWebSocketMessage(
                kind="error",
                close_code=_close_code_from_exception(exc),
                error=str(exc),
            )

        if isinstance(message, str):
            return UpstreamWebSocketMessage(kind="text", text=message)
        if isinstance(message, bytes):
            return UpstreamWebSocketMessage(kind="binary", data=message)
        return UpstreamWebSocketMessage(kind="error", error=f"Unexpected websocket message type: {type(message)!r}")

    async def close(self) -> None:
        await self._connection.close()


def filter_inbound_websocket_headers(headers: dict[str, str]) -> dict[str, str]:
    filtered = filter_inbound_headers(headers)
    return {key: value for key, value in filtered.items() if key.lower() not in _WEBSOCKET_HOP_BY_HOP_HEADERS}


def _build_upstream_websocket_headers(
    inbound: dict[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, str]:
    headers = {key: value for key, value in inbound.items() if key.lower() != "cookie"}
    lower_keys = {key.lower() for key in headers}
    if "x-request-id" not in lower_keys and "request-id" not in lower_keys:
        request_id = get_request_id()
        if request_id:
            headers["x-request-id"] = request_id
    headers["Authorization"] = f"Bearer {access_token}"
    if account_id:
        headers["chatgpt-account-id"] = account_id
    _ensure_responses_websocket_beta_header(headers)
    return headers


def _ensure_responses_websocket_beta_header(headers: dict[str, str]) -> None:
    header_key = next((key for key in headers if key.lower() == "openai-beta"), "openai-beta")
    current_value = headers.get(header_key, "")
    beta_tokens = [token.strip() for token in current_value.split(",") if token.strip()]
    if _RESPONSES_WEBSOCKET_BETA_HEADER.lower() not in {token.lower() for token in beta_tokens}:
        beta_tokens.append(_RESPONSES_WEBSOCKET_BETA_HEADER)
    headers[header_key] = ", ".join(beta_tokens)


def _pop_header_case_insensitive(headers: dict[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key in tuple(headers):
        if key.lower() != lowered:
            continue
        return headers.pop(key)
    return None


def _responses_websocket_url(base_url: str) -> str:
    parsed = urlparse(f"{base_url.rstrip('/')}/codex/responses")
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        scheme = parsed.scheme
    return urlunparse(parsed._replace(scheme=scheme))


async def connect_responses_websocket(
    headers: dict[str, str],
    access_token: str,
    account_id: str | None,
    *,
    base_url: str | None = None,
    session: object | None = None,
) -> UpstreamResponsesWebSocket:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = _responses_websocket_url(upstream_base)
    upstream_headers = _build_upstream_websocket_headers(headers, access_token, account_id)
    origin = cast(Origin | None, _pop_header_case_insensitive(upstream_headers, "origin"))
    user_agent = _pop_header_case_insensitive(upstream_headers, "user-agent")
    _ = session

    try:
        response = await websocket_connect(
            url,
            origin=origin,
            additional_headers=upstream_headers or None,
            user_agent_header=user_agent,
            proxy=True if settings.upstream_websocket_trust_env else None,
            open_timeout=settings.upstream_connect_timeout_seconds,
            max_size=settings.max_sse_event_bytes,
        )
    except asyncio.TimeoutError as exc:
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", "Request to upstream timed out"),
        ) from exc
    except InvalidStatus as exc:
        response = exc.response
        message = response.reason_phrase or f"Upstream websocket error: HTTP {response.status_code}"
        raise ProxyResponseError(
            response.status_code,
            _handshake_error_payload(response.status_code, message, response.headers, response.body),
        ) from exc
    except InvalidHandshake as exc:
        message = str(exc) or "Invalid upstream websocket handshake"
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message, error_type="server_error"),
        ) from exc
    except InvalidProxy as exc:
        message = str(exc) or "Invalid upstream websocket proxy configuration"
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message, error_type="server_error"),
        ) from exc
    except OSError as exc:
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", str(exc)),
        ) from exc

    return WebsocketsResponsesWebSocket(response)


def _close_code_from_exception(exc: ConnectionClosedOK | ConnectionClosedError) -> int | None:
    if exc.rcvd is not None:
        return int(exc.rcvd.code)
    if exc.sent is not None:
        return int(exc.sent.code)
    return None


def _handshake_error_payload(
    status_code: int,
    message: str,
    headers: Headers | None = None,
    body: bytes | bytearray | None = None,
) -> OpenAIErrorEnvelope:
    parsed = _try_parse_handshake_error_payload(headers, body)
    if parsed is not None:
        return parsed
    if status_code == 401:
        return openai_error("invalid_api_key", message, error_type="authentication_error")
    if status_code == 429:
        return openai_error("rate_limit_exceeded", message, error_type="rate_limit_error")
    if status_code == 403:
        return openai_error("forbidden", message, error_type="permission_error")
    if status_code >= 500:
        return openai_error("upstream_error", message, error_type="server_error")
    return openai_error("invalid_request_error", message, error_type="invalid_request_error")


def _try_parse_handshake_error_payload(
    headers: Headers | None,
    body: bytes | bytearray | None,
) -> OpenAIErrorEnvelope | None:
    if not body:
        return None

    content_type = ""
    if headers is not None:
        content_type = headers.get("Content-Type", "")

    if "json" not in content_type.lower() and not body.strip().startswith((b"{", b"[")):
        return None

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None

    error = parse_error_payload(payload)
    if error is None:
        return None
    return {"error": error.model_dump(exclude_none=True)}
