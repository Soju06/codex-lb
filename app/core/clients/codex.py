from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

import aiohttp
from aiohttp_socks import ProxyConnector

from app.core.upstream_proxy import ResolvedUpstreamRoute

_RESERVED = frozenset({"akamai", "extra_fp", "impersonate", "ja3", "proxies", "proxy"})


class CodexTransportError(RuntimeError):
    """Sanitized upstream transport failure.

    Transport libraries can include proxy URLs in exception strings. Proxy URLs
    may embed credentials, so callers should surface this sanitized exception
    instead of the original transport message.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def require_route_or_direct_egress_opt_in(
    *,
    route: ResolvedUpstreamRoute | None,
    allow_direct_egress: bool,
    operation: str,
) -> None:
    if route is None and not allow_direct_egress:
        raise ValueError(f"Direct Codex upstream egress for {operation} requires allow_direct_egress=True")


@dataclass(frozen=True, slots=True)
class CodexRequestResult:
    response: Any
    route: ResolvedUpstreamRoute
    fallback_used: bool


@dataclass(frozen=True, slots=True)
class CodexWebSocketResult:
    websocket: Any
    context: Any | None
    route: ResolvedUpstreamRoute
    fallback_used: bool


@dataclass(frozen=True, slots=True)
class _BufferedResponse:
    status: int
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    async def read(self) -> bytes:
        return self.content

    async def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json

        return json.loads(self.content)


class _SessionOwnedContent:
    def __init__(self, content: Any, session: aiohttp.ClientSession) -> None:
        self._content = content
        self._session = session

    def iter_chunked(self, size: int) -> Any:
        return self._iter_and_close(self._content.iter_chunked(size))

    async def _iter_and_close(self, iterator: Any) -> Any:
        try:
            async for chunk in iterator:
                yield chunk
        finally:
            await self._session.close()


class _SessionOwnedResponse:
    def __init__(self, response: Any, session: aiohttp.ClientSession) -> None:
        self._response = response
        self._session = session
        self.status = getattr(response, "status", getattr(response, "status_code", 0))
        self.status_code = getattr(response, "status_code", self.status)
        self.headers = getattr(response, "headers", {}) or {}
        self.content = _SessionOwnedContent(response.content, session)

    async def read(self) -> bytes:
        try:
            result = self._response.read()
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, bytes):
                return result
            if isinstance(result, str):
                return result.encode()
            return b""
        finally:
            await self._session.close()

    async def text(self) -> str:
        return (await self.read()).decode("utf-8", errors="replace")

    async def json(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        import json

        return json.loads(await self.read())


class _SessionOwnedWebSocketContext:
    def __init__(self, context: Any, session: aiohttp.ClientSession) -> None:
        self._context = context
        self._session = session

    async def __aenter__(self) -> Any:
        return self._context

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if hasattr(self._context, "__aexit__"):
                await self._context.__aexit__(exc_type, exc, traceback)
            else:
                close = getattr(self._context, "close", None)
                if callable(close):
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
        finally:
            await self._session.close()


class CodexClient:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> Any:
        return (await self.request_with_route_metadata(method, url, route=route, **kwargs)).response

    async def request_with_route_metadata(
        self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any
    ) -> CodexRequestResult:
        if route is None:
            raise ValueError("Codex upstream calls require a resolved upstream proxy route")
        buffer_response = bool(kwargs.pop("buffer_response", True))
        _normalize_aiohttp_request_kwargs(kwargs)
        _reject_reserved(kwargs)
        endpoints = (route.endpoint, *route.fallbacks)
        allow_fallback = _is_idempotent_method(method)
        for index, endpoint in enumerate(endpoints):
            candidate = route.with_endpoint(endpoint, tuple(endpoints[index + 1 :]))
            try:
                if endpoint.scheme.startswith("socks"):
                    response = await _request_via_socks_proxy(
                        method,
                        url,
                        endpoint.proxy_url,
                        buffer_response=buffer_response,
                        **kwargs,
                    )
                else:
                    response = await self._session.request(method, url, proxy=endpoint.proxy_url, **kwargs)
                if buffer_response and not endpoint.scheme.startswith("socks"):
                    response = await _buffer_response(response)
                return CodexRequestResult(response, candidate, index > 0)
            except Exception as exc:
                if index == len(endpoints) - 1 or not allow_fallback:
                    raise _transport_error("request", endpoint.id, exc) from None
        raise RuntimeError("unreachable Codex client fallback state")

    async def ws_connect(self, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> Any:
        if route is None:
            raise ValueError("Codex upstream calls require a resolved upstream proxy route")
        _reject_reserved(kwargs)
        result = self._session.ws_connect(url, proxy=route.proxy_url, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def open_ws_with_route_metadata(
        self, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any
    ) -> CodexWebSocketResult:
        if route is None:
            raise ValueError("Codex upstream calls require a resolved upstream proxy route")
        _reject_reserved(kwargs)
        endpoints = (route.endpoint, *route.fallbacks)
        for index, endpoint in enumerate(endpoints):
            candidate = route.with_endpoint(endpoint, tuple(endpoints[index + 1 :]))
            context: Any | None = None
            try:
                if endpoint.scheme.startswith("socks"):
                    websocket, context = await _open_ws_via_socks_proxy(url, endpoint.proxy_url, **kwargs)
                else:
                    context = self._session.ws_connect(
                        url,
                        proxy=endpoint.proxy_url,
                        **kwargs,
                    )
                    if asyncio.iscoroutine(context):
                        context = await context
                    websocket = await context.__aenter__() if hasattr(context, "__aenter__") else context
                return CodexWebSocketResult(
                    websocket,
                    context if hasattr(context, "__aenter__") else None,
                    candidate,
                    index > 0,
                )
            except Exception as exc:
                if context is not None and hasattr(context, "__aexit__"):
                    await context.__aexit__(None, None, None)
                if index == len(endpoints) - 1:
                    raise _transport_error("websocket", endpoint.id, exc) from None
        raise RuntimeError("unreachable Codex client websocket fallback state")

    async def close(self) -> None:
        close = getattr(self._session, "close", None)
        if not callable(close):
            close = getattr(self._session, "aclose", None)
        if not callable(close):
            return
        result = close()
        if asyncio.iscoroutine(result):
            await result


def create_codex_session(*, max_clients: int = 10) -> Any:
    from app.core.clients.http import _build_ssl_context

    connector = aiohttp.TCPConnector(limit=max_clients, ssl=_build_ssl_context())
    return aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=None), trust_env=False)


async def _request_via_socks_proxy(
    method: str,
    url: str,
    proxy_url: str,
    *,
    buffer_response: bool,
    **kwargs: Any,
) -> Any:
    from app.core.clients.http import _build_ssl_context

    connector = ProxyConnector.from_url(proxy_url, ssl=_build_ssl_context())
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=None),
        trust_env=False,
    )
    try:
        response = await session.request(method, url, **kwargs)
        if buffer_response:
            return await _buffer_response(response)
        return _SessionOwnedResponse(response, session)
    except Exception:
        await session.close()
        raise
    finally:
        if buffer_response:
            await session.close()


async def _open_ws_via_socks_proxy(url: str, proxy_url: str, **kwargs: Any) -> tuple[Any, Any]:
    from app.core.clients.http import _build_ssl_context

    connector = ProxyConnector.from_url(proxy_url, ssl=_build_ssl_context())
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=None),
        trust_env=False,
    )
    try:
        context = session.ws_connect(url, **kwargs)
        if asyncio.iscoroutine(context):
            context = await context
        websocket = await context.__aenter__() if hasattr(context, "__aenter__") else context
        return websocket, _SessionOwnedWebSocketContext(context, session)
    except Exception:
        await session.close()
        raise


def _normalize_aiohttp_request_kwargs(kwargs: dict[str, Any]) -> None:
    files = kwargs.pop("files", None)
    if files is None:
        return
    form = aiohttp.FormData()
    data = kwargs.pop("data", None)
    if isinstance(data, Mapping):
        for name, value in data.items():
            form.add_field(str(name), value)
    elif data is not None:
        form.add_field("data", data)

    file_items = files.items() if isinstance(files, Mapping) else files
    for name, value in file_items:
        _add_form_file(form, str(name), value)
    kwargs["data"] = form


def _add_form_file(form: aiohttp.FormData, name: str, value: Any) -> None:
    if isinstance(value, tuple):
        filename = value[0] if len(value) >= 1 else None
        file_value = value[1] if len(value) >= 2 else b""
        content_type = value[2] if len(value) >= 3 else None
        form.add_field(
            name,
            file_value,
            filename=str(filename) if filename else None,
            content_type=str(content_type) if content_type else None,
        )
        return
    form.add_field(name, value)


async def _buffer_response(response: Any) -> Any:
    body = await _read_response_body(response)
    if body is None:
        return response
    status = _response_status(response)
    headers = getattr(response, "headers", {}) or {}
    return _BufferedResponse(
        status=status,
        status_code=status,
        headers={str(key): str(value) for key, value in headers.items()},
        content=body,
    )


async def _read_response_body(response: Any) -> bytes | None:
    read = getattr(response, "read", None)
    if callable(read):
        result = read()
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, bytes):
            return result
        if isinstance(result, str):
            return result.encode()
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode()
    return None


def _response_status(response: Any) -> int:
    value = getattr(response, "status", getattr(response, "status_code", 0))
    return int(value or 0)


def _reject_reserved(kwargs: Mapping[str, Any]) -> None:
    forbidden = sorted(_RESERVED.intersection(kwargs))
    if forbidden:
        raise ValueError(f"Codex upstream route/TLS kwargs are controlled centrally: {', '.join(forbidden)}")


def _is_idempotent_method(method: str) -> bool:
    return method.upper() in {"GET", "HEAD", "OPTIONS", "TRACE"}


def _transport_error(operation: str, endpoint_id: str, exc: Exception) -> CodexTransportError:
    return CodexTransportError(
        codex_transport_error_message(operation, endpoint_id, exc),
        status_code=_transport_error_status_code(exc),
    )


def _transport_error_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    for source in (response, exc):
        if source is None:
            continue
        value = getattr(source, "status_code", getattr(source, "status", None))
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def codex_transport_error_message(operation: str, endpoint_id: str | None, exc: Exception) -> str:
    endpoint = endpoint_id or "unknown"
    return f"Codex upstream {operation} failed via proxy endpoint {endpoint}: {type(exc).__name__}"
