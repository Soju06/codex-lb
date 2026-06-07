from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, cast
from urllib.parse import urlparse, urlunparse

from curl_cffi.const import CurlWsFlag
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

from app.core.clients.codex import (
    CodexClient,
    CodexTransportError,
    codex_transport_error_message,
    create_codex_session,
    require_route_or_direct_egress_opt_in,
)
from app.core.clients.proxy import ProxyResponseError, filter_inbound_headers
from app.core.config.settings import get_settings
from app.core.conversation_archive import archive_bytes, archive_text
from app.core.errors import OpenAIErrorDetail, OpenAIErrorEnvelope, openai_error
from app.core.openai.models import OpenAIError
from app.core.openai.parsing import parse_error_payload
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.utils.proxy_env import resolve_websocket_proxy_from_env
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

    def response_header(self, name: str) -> str | None: ...


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

    def response_header(self, name: str) -> str | None:
        response = getattr(self._connection, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        value = headers.get(name)
        if value is None:
            return None
        return str(value)


class CodexResponsesWebSocket:
    def __init__(
        self,
        websocket: Any,
        *,
        context: Any | None = None,
        codex_client: CodexClient | None = None,
        owns_codex_client: bool = False,
        endpoint_id: str | None = None,
        response_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._websocket = websocket
        self._context = context
        self._codex_client = codex_client
        self._owns_codex_client = owns_codex_client
        self._endpoint_id = endpoint_id
        self._response_headers = _normalize_response_headers(response_headers)

    async def send_text(self, text: str) -> None:
        try:
            result = self._websocket.send_str(text)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            raise RuntimeError(codex_transport_error_message("websocket send", self._endpoint_id, exc)) from None

    async def send_bytes(self, data: bytes) -> None:
        try:
            result = self._websocket.send_bytes(data)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            raise RuntimeError(codex_transport_error_message("websocket send", self._endpoint_id, exc)) from None

    async def receive(self) -> UpstreamWebSocketMessage:
        try:
            data, flags = await self._websocket.recv()
        except Exception as exc:
            return UpstreamWebSocketMessage(
                kind="error",
                error=codex_transport_error_message("websocket receive", self._endpoint_id, exc),
            )
        if flags & int(CurlWsFlag.CLOSE):
            return UpstreamWebSocketMessage(kind="close")
        if flags & int(CurlWsFlag.TEXT):
            return UpstreamWebSocketMessage(kind="text", text=data.decode("utf-8", errors="replace"))
        return UpstreamWebSocketMessage(kind="binary", data=bytes(data))

    async def close(self) -> None:
        try:
            result = self._websocket.close()
            if asyncio.iscoroutine(result):
                await result
        finally:
            if self._context is not None:
                await self._context.__aexit__(None, None, None)
            if self._owns_codex_client and self._codex_client is not None:
                await self._codex_client.close()

    def response_header(self, name: str) -> str | None:
        return self._response_headers.get(name.lower())


class ArchivingResponsesWebSocket:
    def __init__(
        self,
        wrapped: UpstreamResponsesWebSocket,
        *,
        url: str,
        headers: dict[str, str],
        account_id: str | None,
        route: ResolvedUpstreamRoute | None = None,
        fallback_used: bool | None = None,
        direct_egress: bool = False,
    ) -> None:
        self._wrapped = wrapped
        self._url = url
        self._headers = headers
        self._account_id = account_id
        self.upstream_proxy_route_mode = route.mode if route is not None else ("direct" if direct_egress else None)
        self.upstream_proxy_pool_id = route.pool_id if route is not None else None
        self.upstream_proxy_endpoint_id = route.endpoint_id if route is not None else None
        self.upstream_proxy_fallback_used = fallback_used if route is not None else None

    async def send_text(self, text: str) -> None:
        archive_text(
            direction="codex_to_server",
            kind="responses",
            transport="websocket",
            text=text,
            account_id=self._account_id,
            method="GET",
            url=self._url,
            headers=self._headers,
            extra={"frame_type": "text"},
        )
        await self._wrapped.send_text(text)

    async def send_bytes(self, data: bytes) -> None:
        archive_bytes(
            direction="codex_to_server",
            kind="responses",
            transport="websocket",
            data=data,
            account_id=self._account_id,
            method="GET",
            url=self._url,
            headers=self._headers,
            extra={"frame_type": "binary"},
        )
        await self._wrapped.send_bytes(data)

    async def receive(self) -> UpstreamWebSocketMessage:
        message = await self._wrapped.receive()
        if message.kind == "text" and message.text is not None:
            archive_text(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                text=message.text,
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": "text"},
            )
        elif message.kind == "binary" and message.data is not None:
            archive_bytes(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                data=message.data,
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": "binary"},
            )
        else:
            archive_text(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                text=message.error or "",
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": message.kind, "close_code": message.close_code},
            )
        return message

    async def close(self) -> None:
        await self._wrapped.close()

    def response_header(self, name: str) -> str | None:
        return self._wrapped.response_header(name)


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
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
) -> UpstreamResponsesWebSocket:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = _responses_websocket_url(upstream_base)
    upstream_headers = _build_upstream_websocket_headers(headers, access_token, account_id)
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="responses websocket",
    )
    if route is not None:
        owns_codex_client = codex_client is None
        active_codex_client = codex_client or CodexClient(create_codex_session())
        endpoint_id = route.endpoint_id
        active_route = route
        fallback_used = False
        try:
            opener = getattr(active_codex_client, "open_ws_with_route_metadata", None)
            if callable(opener):
                result = await opener(
                    url,
                    route=route,
                    headers=upstream_headers,
                    timeout=settings.upstream_connect_timeout_seconds,
                    max_message_size=settings.max_sse_event_bytes,
                )
                context = result.context
                websocket = result.websocket
                endpoint_id = result.route.endpoint_id
                active_route = result.route
                fallback_used = result.fallback_used
            else:
                context = await active_codex_client.ws_connect(
                    url,
                    route=route,
                    headers=upstream_headers,
                    timeout=settings.upstream_connect_timeout_seconds,
                    max_message_size=settings.max_sse_event_bytes,
                )
                websocket = await context.__aenter__() if hasattr(context, "__aenter__") else context
                if not hasattr(context, "__aenter__"):
                    context = None
                endpoint_id = route.endpoint_id
        except CodexTransportError as exc:
            if owns_codex_client:
                await active_codex_client.close()
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", str(exc), error_type="server_error"),
            ) from exc
        except Exception:
            if owns_codex_client:
                await active_codex_client.close()
            raise
        return ArchivingResponsesWebSocket(
            CodexResponsesWebSocket(
                websocket,
                context=context if hasattr(context, "__aenter__") else None,
                codex_client=active_codex_client,
                owns_codex_client=owns_codex_client,
                endpoint_id=endpoint_id,
                response_headers=_codex_websocket_response_headers(websocket, context),
            ),
            url=url,
            headers=upstream_headers,
            account_id=account_id,
            route=active_route,
            fallback_used=fallback_used,
        )
    origin = cast(Origin | None, _pop_header_case_insensitive(upstream_headers, "origin"))
    user_agent = _pop_header_case_insensitive(upstream_headers, "user-agent")
    proxy_env = (
        settings.upstream_websocket_proxy_env() if hasattr(settings, "upstream_websocket_proxy_env") else os.environ
    )
    proxy_url = resolve_websocket_proxy_from_env(url, proxy_env) if settings.upstream_websocket_trust_env else None
    connect_kwargs: dict[str, Any] = {
        "origin": origin,
        "additional_headers": upstream_headers or None,
        "user_agent_header": user_agent,
        "open_timeout": settings.upstream_connect_timeout_seconds,
        # Long Codex turns can spend minutes in upstream reasoning without
        # sending application frames. Keep transport pings enabled so
        # intermediaries still see liveness, but disable the library's pong
        # watchdog so codex-lb's own request/idle budgets decide when a
        # healthy long turn has stalled.
        "ping_timeout": None,
        "max_size": settings.max_sse_event_bytes,
    }
    connect_kwargs["proxy"] = proxy_url
    try:
        response = await websocket_connect(url, **connect_kwargs)
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

    return ArchivingResponsesWebSocket(
        WebsocketsResponsesWebSocket(response),
        url=url,
        headers=upstream_headers,
        account_id=account_id,
        direct_egress=allow_direct_egress,
    )


def _close_code_from_exception(exc: ConnectionClosedOK | ConnectionClosedError) -> int | None:
    if exc.rcvd is not None:
        return int(exc.rcvd.code)
    if exc.sent is not None:
        return int(exc.sent.code)
    return None


def _codex_websocket_response_headers(websocket: object, context: object | None) -> Mapping[str, str]:
    for source in (websocket, context):
        headers = _response_headers_from_source(source)
        if headers:
            return headers
    return {}


def _response_headers_from_source(source: object | None) -> Mapping[str, str]:
    if source is None:
        return {}
    for attr in ("response", "handshake_response"):
        response = getattr(source, attr, None)
        headers = getattr(response, "headers", None)
        if headers:
            return _normalize_response_headers(headers)
    for attr in ("headers", "response_headers"):
        headers = getattr(source, attr, None)
        if headers:
            return _normalize_response_headers(headers)
    return {}


def _normalize_response_headers(headers: Mapping[str, object] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


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
    return {"error": _openai_error_detail(error)}


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
