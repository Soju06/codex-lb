from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from app.core.clients.codex_tls import codex_tls_kwargs
from app.core.upstream_proxy import ResolvedUpstreamRoute

_RESERVED = frozenset({"proxy", "proxies", "impersonate", "ja3", "akamai", "extra_fp"})


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
        _reject_reserved(kwargs)
        endpoints = (route.endpoint, *route.fallbacks)
        allow_fallback = _is_idempotent_method(method)
        for index, endpoint in enumerate(endpoints):
            candidate = route.with_endpoint(endpoint, tuple(endpoints[index + 1 :]))
            try:
                response = await self._session.request(
                    method, url, proxy=endpoint.proxy_url, **codex_tls_kwargs(), **kwargs
                )
                return CodexRequestResult(response, candidate, index > 0)
            except Exception as exc:
                if index == len(endpoints) - 1 or not allow_fallback:
                    raise _transport_error("request", endpoint.id, exc) from None
        raise RuntimeError("unreachable Codex client fallback state")

    async def ws_connect(self, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> Any:
        if route is None:
            raise ValueError("Codex upstream calls require a resolved upstream proxy route")
        _reject_reserved(kwargs)
        result = self._session.ws_connect(url, proxy=route.proxy_url, **codex_tls_kwargs(), **kwargs)
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
                context = self._session.ws_connect(
                    url,
                    proxy=endpoint.proxy_url,
                    **codex_tls_kwargs(),
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
    from curl_cffi.requests import AsyncSession

    return AsyncSession(max_clients=max_clients, trust_env=False, **codex_tls_kwargs())


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
