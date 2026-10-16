"""Loopback relay that swaps a local Codex ChatGPT bearer for a codex-lb API key.

Only ``/backend-api/codex`` is forwarded, and only to ``127.0.0.1:<lb_port>``.
HTTP bodies and WebSocket messages pass through opaquely, with no redirects,
cookies, environment proxies, or retries. Normative contract:
``openspec/changes/add-codex-desktop-auth-bridge/specs/desktop-auth-bridge/spec.md``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import aiohttp
from aiohttp import web
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.http11 import Response as HandshakeResponse
from websockets.typing import Subprotocol
from yarl import URL

from app.core.ingress_limits import MAX_DECOMPRESSED_RESPONSES_BODY_BYTES
from app.desktop_bridge import policy
from app.desktop_bridge.credentials import CredentialError, load_credentials

# Fixed limits, deliberately not configurable (PRINCIPLES P2).
MAX_ACTIVE_REQUESTS = 16
CONNECT_TIMEOUT_SECONDS = 10
IDLE_TIMEOUT_SECONDS = 300
MAX_LIFETIME_SECONDS = 7200
HTTP_SHUTDOWN_GRACE_SECONDS = 5

_MAX_BODY_BYTES: Final[int] = MAX_DECOMPRESSED_RESPONSES_BODY_BYTES
_SKIP_AUTO_HEADERS: Final = ("User-Agent", "Accept", "Accept-Encoding", "Content-Type")
_SENDABLE_CLOSE_CODES: Final = frozenset({1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014})
_ERROR_MESSAGES: Final = {
    400: "The desktop bridge rejected this request.",
    403: "The desktop bridge denied this request.",
    404: "The desktop bridge only forwards /backend-api/codex.",
    405: "The desktop bridge does not forward this method.",
    413: "The request body exceeds the desktop bridge limit.",
    502: "codex-lb did not return a usable response through the desktop bridge.",
    503: "The desktop bridge is temporarily unavailable.",
}
# websockets logs the handshake request line and headers, LB bearer included, at
# DEBUG. This logger sits outside the logging hierarchy above CRITICAL, so no
# global logging configuration can enable it.
_SILENT_WEBSOCKET_LOGGER: Final = logging.Logger("codex_lb.desktop_bridge.websocket", logging.CRITICAL + 1)
# aiohttp's protocol parser can log malformed request lines before _handle runs.
_SILENT_SERVER_LOGGER: Final = logging.Logger("codex_lb.desktop_bridge.server", logging.CRITICAL + 1)


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    """Describe loopback ports and credential paths for one standalone bridge process."""

    listen_port: int
    lb_port: int
    codex_home: Path
    lb_token_file: Path


@dataclass(slots=True)
class _BridgeState:
    """Track the shared client session, request capacity, and sockets owned by this app."""

    config: BridgeConfig
    session: aiohttp.ClientSession | None = None
    active: int = 0
    stopping: bool = False
    websockets: dict[web.WebSocketResponse, web.Request] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _CloseFrame:
    """Carry a relayable WebSocket close status and reason between the message pumps."""

    code: int
    reason: str


@dataclass(slots=True)
class _Activity:
    """Track the latest message activity using the monotonic clock for idle deadlines."""

    last: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        """Reset the shared idle deadline after a message arrives from either peer."""
        self.last = time.monotonic()


class _BodyLimitExceeded(Exception):
    """A request or response body crossed the bridge size limit."""


class _NoRedirectConnect(connect):
    """websockets' ``connect`` follows redirects by default; the bridge never does."""

    def process_redirect(self, exc: Exception) -> Exception | str:
        """Return the handshake failure unchanged so the client never follows a redirect."""
        return exc


_STATE: Final = web.AppKey("desktop_bridge_state", _BridgeState)


def create_bridge_app(config: BridgeConfig) -> web.Application:
    """Build the bridge app; raises ``ValueError`` for bad ports or unusable credentials."""
    for port in (config.listen_port, config.lb_port):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Desktop bridge ports must be integers between 1 and 65535.")
    if config.listen_port == config.lb_port:
        raise ValueError("The desktop bridge port must differ from the codex-lb port.")
    load_credentials(config.codex_home, config.lb_token_file)
    # handler_args override runner kwargs, so these hold under run_app or a bare AppRunner.
    app = web.Application(
        handler_args={
            "auto_decompress": False,
            "handler_cancellation": True,
            "access_log": None,
            "logger": _SILENT_SERVER_LOGGER,
        }
    )
    app[_STATE] = _BridgeState(config=config)
    app.cleanup_ctx.append(_client_session)
    app.on_shutdown.append(_shutdown_websockets)
    app.router.add_route("*", "/{tail:.*}", _handle)
    return app


def run_bridge(config: BridgeConfig) -> None:
    """Serve the bridge on 127.0.0.1 until interrupted."""
    web.run_app(
        create_bridge_app(config),
        host=policy.LOOPBACK_HOST,
        port=config.listen_port,
        access_log=None,
        shutdown_timeout=HTTP_SHUTDOWN_GRACE_SECONDS,
    )


async def _shutdown_websockets(app: web.Application) -> None:
    """Stop admission and concurrently close tracked sockets with bounded going-away closes."""
    state = app[_STATE]
    state.stopping = True
    # Closing a downstream receive wakes its relay pumps. TaskGroup owns every
    # close, and each has its own deadline, so 16 peers cannot multiply the wait.
    async with asyncio.TaskGroup() as closes:
        for websocket, request in tuple(state.websockets.items()):
            closes.create_task(_close_websocket(request, websocket, _CloseFrame(1001, "")))


async def _client_session(app: web.Application) -> AsyncIterator[None]:
    """Own a credential-neutral HTTP client session for the application lifetime."""
    state = app[_STATE]
    # force_close: an upstream socket is never reused, so a stale keep-alive
    # connection can neither fail nor silently replay a request.
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(force_close=True),
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
        auto_decompress=False,
        skip_auto_headers=_SKIP_AUTO_HEADERS,
        timeout=aiohttp.ClientTimeout(total=None),
    ) as session:
        state.session = session
        try:
            yield
        finally:
            state.session = None


async def _handle(request: web.Request) -> web.StreamResponse:
    """Validate local identity and capacity, then own a relay slot until forwarding ends."""
    state = request.app[_STATE]
    if state.stopping:
        return _error(503)
    config = state.config
    try:
        raw_target = policy.checked_target(request.method, request.raw_path)
        policy.check_host(request.headers.getall("Host", []), config.listen_port)
        policy.check_no_origin(request.headers.getall("Origin", []))
        presented = policy.bearer_token(request.headers.getall("Authorization", []))
    except policy.PolicyRejection as rejection:
        return _error(rejection.status)
    try:
        local = load_credentials(config.codex_home, config.lb_token_file)
    except CredentialError:
        return _error(503)
    if not policy.tokens_match(presented, local.access_token):
        return _error(403)
    session = state.session
    if session is None or state.active >= MAX_ACTIVE_REQUESTS:
        return _error(503)
    state.active += 1
    try:
        if _is_websocket_upgrade(request):
            return await _relay_websocket(request, config.lb_port, raw_target, local.lb_token)
        return await _relay_http(request, session, config.lb_port, raw_target, local.lb_token)
    except Exception:
        # Relays handle expected failures; anything else closes the connection
        # instead of letting aiohttp log a traceback.
        _abort(request)
        return _error(502)
    finally:
        state.active -= 1


async def _relay_http(
    request: web.Request, session: aiohttp.ClientSession, lb_port: int, raw_target: str, lb_token: str
) -> web.StreamResponse:
    """Stream one bounded HTTP exchange, masking auth failures and aborting partial responses."""
    declared_length = request.content_length
    if declared_length is not None and declared_length > _MAX_BODY_BYTES:
        return _error(413)
    body = _BoundedRequestBody(request)
    response: web.StreamResponse | None = None
    try:
        async with asyncio.timeout(MAX_LIFETIME_SECONDS):
            async with session.request(
                request.method,
                URL(f"http://{policy.LOOPBACK_HOST}:{lb_port}{raw_target}", encoded=True),
                headers=policy.request_headers(request.headers.items(), lb_token, websocket=False),
                data=body.chunks() if request.body_exists else None,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=None, connect=CONNECT_TIMEOUT_SECONDS, sock_read=IDLE_TIMEOUT_SECONDS
                ),
            ) as upstream:
                # Codex treats 401 as a ChatGPT login failure, and a 3xx would leak a Location.
                if upstream.status == 401 or 300 <= upstream.status < 400:
                    return _error(502)
                response = web.StreamResponse(status=upstream.status)
                response.headers.extend(policy.response_headers(upstream.headers.items(), websocket=False))
                await response.prepare(request)
                await _copy_response_body(upstream, response)
                await response.write_eof()
                return response
    except Exception:
        if response is None:
            return _error(413 if body.exceeded else 502)
        # Bytes may already be on the wire: close rather than fake a complete response.
        _abort(request)
        return response


class _BoundedRequestBody:
    """Stream an upload with byte and idle limits, recording whether its size was exceeded."""

    def __init__(self, request: web.Request) -> None:
        """Retain the inbound request and initialize its upload-limit status."""
        self._request = request
        self.exceeded = False

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield raw upload chunks, raising on idle timeout or excess total body size."""
        received = 0
        while True:
            async with asyncio.timeout(IDLE_TIMEOUT_SECONDS):
                chunk = await self._request.content.readany()
            if not chunk:
                return
            received += len(chunk)
            if received > _MAX_BODY_BYTES:
                self.exceeded = True
                raise _BodyLimitExceeded
            yield chunk


async def _copy_response_body(upstream: aiohttp.ClientResponse, response: web.StreamResponse) -> None:
    """Copy raw response chunks with a total byte limit and per-write idle deadline."""
    received = 0
    async for chunk in upstream.content.iter_any():
        received += len(chunk)
        if received > _MAX_BODY_BYTES:
            raise _BodyLimitExceeded
        async with asyncio.timeout(IDLE_TIMEOUT_SECONDS):
            await response.write(chunk)


async def _relay_websocket(request: web.Request, lb_port: int, raw_target: str, lb_token: str) -> web.StreamResponse:
    """Connect upstream before upgrading Desktop, then own both sockets through shutdown."""
    try:
        offered = policy.offered_subprotocols(request.headers.getall("Sec-WebSocket-Protocol", []))
    except policy.PolicyRejection as rejection:
        return _error(rejection.status)
    # Probing with the offered protocols keeps aiohttp from logging a mismatch warning.
    if not web.WebSocketResponse(protocols=offered).can_prepare(request).ok:
        return _error(400)
    # Open codex-lb first so a refused handshake is answered before any upgrade.
    try:
        upstream = await _NoRedirectConnect(
            f"ws://{policy.LOOPBACK_HOST}:{lb_port}{raw_target}",
            additional_headers=policy.request_headers(request.headers.items(), lb_token, websocket=True),
            subprotocols=[Subprotocol(protocol) for protocol in offered] or None,
            compression=None,
            proxy=None,
            user_agent_header=None,
            open_timeout=CONNECT_TIMEOUT_SECONDS,
            close_timeout=CONNECT_TIMEOUT_SECONDS,
            max_size=_MAX_BODY_BYTES,
            logger=_SILENT_WEBSOCKET_LOGGER,
        )
    except InvalidStatus as exc:
        return _handshake_rejection(exc.response)
    except Exception:
        return _error(502)
    try:
        state = request.app[_STATE]
        if state.stopping:
            return _error(503)
        websocket = web.WebSocketResponse(
            protocols=(upstream.subprotocol,) if upstream.subprotocol else (),
            compress=False,
            max_msg_size=_MAX_BODY_BYTES,
            autoclose=False,
        )
        handshake = upstream.response
        if handshake is not None:
            websocket.headers.extend(policy.response_headers(handshake.headers.raw_items(), websocket=True))
        # aiohttp warns with raw offered values when no protocol is selected.
        # The upstream has already negotiated; omit the unselected offer only
        # from the downstream handshake view to keep it out of all logs.
        handshake_request = request
        if offered and upstream.subprotocol is None:
            handshake_headers = request.headers.copy()
            handshake_headers.popall("Sec-WebSocket-Protocol", None)
            handshake_request = request.clone(headers=handshake_headers)
        await websocket.prepare(handshake_request)
        state.websockets[websocket] = request
        try:
            # Shutdown may have begun while prepare yielded, after its snapshot.
            if state.stopping:
                await _close_websocket(request, websocket, _CloseFrame(1001, ""))
            else:
                await _relay_messages(request, websocket, upstream)
        finally:
            state.websockets.pop(websocket, None)
        return websocket
    finally:
        with suppress(Exception):
            await upstream.close(1001)


def _handshake_rejection(handshake: HandshakeResponse) -> web.Response:
    """Sanitize a refused upgrade, masking redirects and authentication failures as 502."""
    status = handshake.status_code
    if status < 200 or status == 401 or 300 <= status < 400:
        return _error(502)
    return web.Response(
        status=status,
        body=b"" if status in (204, 205) else (handshake.body or b""),
        headers=policy.response_headers(handshake.headers.raw_items(), websocket=True),
    )


async def _relay_messages(request: web.Request, websocket: web.WebSocketResponse, upstream: ClientConnection) -> None:
    """Own both message pumps, enforce session deadlines, and propagate permitted close frames."""
    activity = _Activity()
    pumps = (
        asyncio.create_task(_client_to_upstream(websocket, upstream, activity)),
        asyncio.create_task(_upstream_to_client(upstream, websocket, activity)),
    )
    try:
        with suppress(TimeoutError):
            async with asyncio.timeout(MAX_LIFETIME_SECONDS):
                while not any(pump.done() for pump in pumps):
                    remaining = activity.last + IDLE_TIMEOUT_SECONDS - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.wait(pumps, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for pump in pumps:
            pump.cancel()
        from_client, from_upstream = await asyncio.gather(*pumps, return_exceptions=True)
    # Idle or lifetime expiry is "going away"; a failed pump is an internal error.
    failed = isinstance(from_client, Exception) or isinstance(from_upstream, Exception)
    fallback = _CloseFrame(1011 if failed else 1001, "")
    to_upstream = from_client if isinstance(from_client, _CloseFrame) else fallback
    to_client = from_upstream if isinstance(from_upstream, _CloseFrame) else fallback
    if request.app[_STATE].stopping:
        to_upstream = to_client = _CloseFrame(1001, "")
    with suppress(Exception):
        await upstream.close(to_upstream.code, to_upstream.reason)
    await _close_websocket(request, websocket, to_client)


async def _close_websocket(request: web.Request, websocket: web.WebSocketResponse, frame: _CloseFrame) -> None:
    """Attempt a bounded downstream close, aborting its transport if the close fails."""
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
            await websocket.close(code=frame.code, message=frame.reason.encode())
    except Exception:
        _abort(request)


async def _client_to_upstream(
    websocket: web.WebSocketResponse, upstream: ClientConnection, activity: _Activity
) -> _CloseFrame:
    """Forward Desktop text and binary messages unchanged and return its normalized close."""
    while True:
        message = await websocket.receive()
        if message.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
            activity.touch()
            await upstream.send(message.data)
        elif message.type is aiohttp.WSMsgType.CLOSE:
            return _relayable(message.data, message.extra)
        else:
            return _relayable(websocket.close_code, None)


async def _upstream_to_client(
    upstream: ClientConnection, websocket: web.WebSocketResponse, activity: _Activity
) -> _CloseFrame:
    """Forward LB text and binary messages unchanged and return its normalized close."""
    with suppress(ConnectionClosed):
        async for message in upstream:
            activity.touch()
            if isinstance(message, str):
                await websocket.send_str(message)
            else:
                await websocket.send_bytes(message)
    return _relayable(upstream.close_code, upstream.close_reason)


def _relayable(code: int | None, reason: str | None) -> _CloseFrame:
    """Preserve sendable close codes and reasons, substituting safe codes for reserved values."""
    # 1005 means the peer closed without a status code.
    if code is None or code == 1005:
        return _CloseFrame(1000, "")
    if code in _SENDABLE_CLOSE_CODES or 3000 <= code <= 4999:
        return _CloseFrame(code, reason or "")
    # 1006, 1015, and other reserved codes can never be sent on the wire.
    return _CloseFrame(1001, "")


def _is_websocket_upgrade(request: web.Request) -> bool:
    """Recognize a websocket token across all case-insensitive Upgrade header values."""
    return any(
        token.strip().lower() == "websocket"
        for value in request.headers.getall("Upgrade", [])
        for token in value.split(",")
    )


def _abort(request: web.Request) -> None:
    """Abort the downstream transport so incomplete data cannot appear successfully terminated."""
    if request.transport is not None:
        request.transport.abort()


def _error(status: int) -> web.Response:
    """Build a non-cacheable local error envelope containing no request or credential details."""
    return web.json_response(
        {"error": {"message": _ERROR_MESSAGES[status], "type": "desktop_bridge_error"}},
        status=status,
        headers={"Cache-Control": "no-store"},
    )
