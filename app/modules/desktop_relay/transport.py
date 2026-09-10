from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from aiohttp import (
    ClientError,
    ClientRequest,
    ClientResponse,
    ClientSession,
    ClientTimeout,
    ClientWebSocketResponse,
    DummyCookieJar,
    WSMsgType,
    web,
)
from aiohttp.client_middlewares import ClientHandlerType
from multidict import CIMultiDict
from yarl import URL

from app.core.clients.http import _build_pooled_connector, _shared_ssl_context, _socks_proxy_config, _SocksProxyConfig
from app.core.config.settings import get_settings
from app.core.utils.proxy_env import resolve_http_proxy_from_env, resolve_websocket_proxy_from_env
from app.modules.desktop_relay.cookies import loopback_cookie


@dataclass(frozen=True)
class _Route:
    session: ClientSession
    proxy: str | None = None


_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
}
_WS_HEADERS = {"sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol"}


def _headers(raw: tuple[tuple[bytes, bytes], ...], *, websocket: bool = False) -> CIMultiDict[str]:
    headers = CIMultiDict(
        (key.decode("utf-8", "surrogateescape"), value.decode("utf-8", "surrogateescape")) for key, value in raw
    )
    excluded = _HOP_HEADERS | {
        token.strip().lower() for value in headers.getall("Connection", []) for token in value.split(",")
    }
    if websocket:
        excluded |= _WS_HEADERS
    return CIMultiDict((key, value) for key, value in headers.items() if key.lower() not in excluded)


def _response_headers(
    raw: tuple[tuple[bytes, bytes], ...], *, local_cookies: bool, websocket: bool = False
) -> CIMultiDict[str]:
    return CIMultiDict(
        (key, loopback_cookie(value) if local_cookies and key.lower() == "set-cookie" else value)
        for key, value in _headers(raw, websocket=websocket).items()
    )


class _HandshakeResponse(Exception):
    def __init__(self, response: ClientResponse) -> None:
        self.response = response
        super().__init__("Upstream did not accept the WebSocket handshake")


async def _preserve_websocket_failure(request: ClientRequest, handler: ClientHandlerType) -> ClientResponse:
    response = await handler(request)
    if request.headers.get("Upgrade", "").lower() == "websocket" and response.status != 101:
        # Return the original failure through the relay before aiohttp follows
        # a redirect or discards the handshake response body. The caller owns it.
        raise _HandshakeResponse(response)
    return response


async def _pump(
    source: ClientWebSocketResponse | web.WebSocketResponse,
    target: ClientWebSocketResponse | web.WebSocketResponse,
) -> None:
    while True:
        message = await source.receive()
        if message.type == WSMsgType.TEXT:
            await target.send_str(message.data)
        elif message.type == WSMsgType.BINARY:
            await target.send_bytes(message.data)
        elif message.type == WSMsgType.PING:
            await target.ping(message.data)
        elif message.type == WSMsgType.PONG:
            await target.pong(message.data)
        elif message.type == WSMsgType.CLOSE:
            await target.close(code=message.data or 1000, message=(message.extra or "").encode("utf-8"))
            return
        elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSED, WSMsgType.CLOSING}:
            await target.close(code=source.close_code or 1011)
            return


async def _websocket(request: web.Request, route: _Route, target: URL) -> web.WebSocketResponse:
    protocols = tuple(
        item.strip() for item in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if item.strip()
    )
    async with route.session.ws_connect(
        target,
        proxy=route.proxy,
        headers=_headers(request.raw_headers, websocket=True),
        protocols=protocols,
        autoping=False,
        autoclose=False,
        max_msg_size=16 * 1024 * 1024,
    ) as upstream:
        downstream = web.WebSocketResponse(
            protocols=(upstream.protocol,) if upstream.protocol else (),
            autoping=False,
            autoclose=False,
            max_msg_size=16 * 1024 * 1024,
        )
        # aiohttp owns the handshake fields; retain other end-to-end metadata.
        for key, value in _response_headers(upstream._response.raw_headers, local_cookies=True, websocket=True).items():
            if key.lower() != "sec-websocket-accept":
                downstream.headers.add(key, value)
        await downstream.prepare(request)
        tasks = [asyncio.create_task(_pump(upstream, downstream)), asyncio.create_task(_pump(downstream, upstream))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await downstream.close()
        return downstream


class RelayTransport:
    def __init__(self, local: _Route, http: _Route, websocket: _Route) -> None:
        self.local = local
        self.http = http
        self.websocket = websocket

    async def forward(self, request: web.Request, target: URL, *, usage: bool) -> web.StreamResponse:
        route = self.local if usage else self.http
        response: web.StreamResponse | None = None
        try:
            if request.headers.get("Upgrade", "").lower() == "websocket":
                if usage:
                    raise web.HTTPBadRequest(text="Usage does not support WebSocket")
                try:
                    return await _websocket(
                        request, self.websocket, target.with_scheme("wss" if target.scheme == "https" else "ws")
                    )
                except _HandshakeResponse as failure:
                    pending_response = failure.response
            else:
                pending_response = route.session.request(
                    request.method,
                    target,
                    proxy=route.proxy,
                    headers=_headers(request.raw_headers),
                    data=request.content.iter_chunked(65536) if request.can_read_body else None,
                    allow_redirects=False,
                )
            async with pending_response as upstream:
                response = web.StreamResponse(
                    status=upstream.status,
                    headers=_response_headers(upstream.raw_headers, local_cookies=not usage),
                )
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except (ClientError, TimeoutError, ConnectionError):
            if response is not None and response.prepared:
                if request.transport is not None:
                    request.transport.close()
                return response
            return web.Response(status=502, text="Desktop relay upstream unavailable")


@asynccontextmanager
async def _open_route(
    proxy: str | None, timeout: float, *, socks: _SocksProxyConfig | None = None
) -> AsyncIterator[_Route]:
    if socks is None and proxy:
        socks = _socks_proxy_config({"ALL_PROXY": proxy})
    connector = _build_pooled_connector(get_settings(), _shared_ssl_context(), socks)
    async with ClientSession(
        connector=connector,
        cookie_jar=DummyCookieJar(),
        middlewares=[_preserve_websocket_failure],
        auto_decompress=False,
        trust_env=False,
        timeout=ClientTimeout(total=None, connect=10, sock_read=timeout),
        skip_auto_headers={"User-Agent", "Accept-Encoding", "Content-Type"},
    ) as session:
        # Proxy selection is explicit; trust_env would also import netrc credentials.
        # Disable aiohttp's implicit retry on a dropped reused connection.
        session._retry_connection = False
        yield _Route(session, None if socks else proxy)


@asynccontextmanager
async def open_transport(backend_origin: URL, timeout: float) -> AsyncIterator[RelayTransport]:
    settings = get_settings()
    environ = settings.upstream_websocket_proxy_env()
    # Shared policy gives any configured SOCKS route precedence over HTTP proxies.
    socks = _socks_proxy_config(environ)
    http_proxy = resolve_http_proxy_from_env(str(backend_origin), environ)
    websocket_origin = backend_origin.with_scheme("wss" if backend_origin.scheme == "https" else "ws")
    websocket_proxy = (
        resolve_websocket_proxy_from_env(str(websocket_origin), environ)
        if settings.upstream_websocket_trust_env
        else None
    )
    async with AsyncExitStack() as stack:
        local = await stack.enter_async_context(_open_route(None, timeout))
        http = await stack.enter_async_context(_open_route(http_proxy, timeout, socks=socks))
        websocket = await stack.enter_async_context(
            _open_route(websocket_proxy, timeout, socks=socks if settings.upstream_websocket_trust_env else None)
        )
        yield RelayTransport(local, http, websocket)
