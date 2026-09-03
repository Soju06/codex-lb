from __future__ import annotations

import asyncio
import inspect
import json
import secrets
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode

import aiohttp
from aiohttp_socks import ProxyConnector
from python_socks import ProxyType
from yarl import URL

from app.core.clients.native_egress import (
    NativeEgressClient,
    NativeEgressError,
    NativeEgressRequest,
    NativeEgressTransportError,
    NativeEgressUnavailable,
    NativeWebSocketRequest,
    discover_native_egress_client,
)
from app.core.resilience.network_recovery import (
    PROCESS_NETWORK_UNAVAILABLE_CODE,
    is_pre_dispatch_connection_failure,
    is_process_network_failure,
)
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

_RESERVED = frozenset({"akamai", "extra_fp", "impersonate", "ja3", "proxies", "proxy"})
_CODEX_SKIP_AUTO_IDENTITY_HEADERS = frozenset({aiohttp.hdrs.ACCEPT, aiohttp.hdrs.ACCEPT_ENCODING})


class CodexTransportError(RuntimeError):
    """Credential-safe routed transport failure.

    ``retryable_same_contract`` is provenance, not a synonym for transient:
    callers may replay only when the failing layer proved dispatch never
    happened.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        retryable_same_contract: bool = False,
        failure_phase: str | None = None,
        is_tls_verification_failure: bool = False,
        handshake_headers: Mapping[str, str] | None = None,
        handshake_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.retryable_same_contract = retryable_same_contract
        self.failure_phase = failure_phase
        self.is_tls_verification_failure = is_tls_verification_failure
        # Handshake response metadata preserved across the sanitizing
        # boundary so callers can classify edge-level rejections (for
        # example Cloudflare browser challenges) that the sanitized message
        # and status alone cannot prove. In-process only; never surfaced to
        # clients.
        self.handshake_headers = handshake_headers
        self.handshake_message = handshake_message


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
    native: bool = False


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
        return json.loads(self.content)


@dataclass(frozen=True, slots=True)
class _PreparedNativeRequest:
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout_seconds: float
    connect_timeout_seconds: float | None
    response_head_timeout_seconds: float | None


async def release_codex_response(response: Any) -> None:
    """Release an unbuffered upstream response once its consumer stops reading.

    Streams routinely end before body EOF (terminal SSE event, idle timeout,
    cancellation, downstream disconnect). Closing the owning session alone
    leaves the aiohttp ``Connection`` acquired, so the cyclic GC later finalizes
    it as ``Unclosed connection``. Duck-typed on purpose: aiohttp exposes
    ``release()``, the native egress response exposes ``aclose()``, and buffered
    or test responses expose neither.
    """
    for name in ("release", "close", "aclose"):
        method = getattr(response, name, None)
        if callable(method):
            result = method()
            if inspect.isawaitable(result):
                await result
            return


class _SessionOwnedContent:
    def __init__(self, response: Any, session: aiohttp.ClientSession) -> None:
        self._response = response
        self._session = session

    def iter_chunked(self, size: int) -> Any:
        return self._iter_and_close(self._response.content.iter_chunked(size))

    async def _iter_and_close(self, iterator: Any) -> Any:
        try:
            async for chunk in iterator:
                yield chunk
        finally:
            await release_codex_response(self._response)
            await self._session.close()


class _SessionOwnedResponse:
    def __init__(self, response: Any, session: aiohttp.ClientSession) -> None:
        self._response = response
        self._session = session
        self.status = getattr(response, "status", getattr(response, "status_code", 0))
        self.status_code = getattr(response, "status_code", self.status)
        self.headers = getattr(response, "headers", {}) or {}
        self.content = _SessionOwnedContent(response, session)

    async def release(self) -> None:
        try:
            await release_codex_response(self._response)
        finally:
            await self._session.close()

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
            await self.release()

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
    def __init__(
        self,
        session: Any,
        *,
        native_egress_client: NativeEgressClient | None = None,
    ) -> None:
        self._session = session
        self._native_egress_client = (
            native_egress_client if native_egress_client is not None else discover_native_egress_client()
        )

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> Any:
        return (await self.request_with_route_metadata(method, url, route=route, **kwargs)).response

    async def request_with_route_metadata(
        self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any
    ) -> CodexRequestResult:
        if route is None:
            raise ValueError("Codex upstream calls require a resolved upstream proxy route")
        buffer_response = bool(kwargs.pop("buffer_response", True))
        _reject_reserved(kwargs)
        native_request = _prepare_native_request(url, kwargs)
        aiohttp_kwargs = dict(kwargs)
        _normalize_aiohttp_request_kwargs(aiohttp_kwargs)
        endpoints = (route.endpoint, *route.fallbacks)
        allow_fallback = _is_idempotent_method(method)
        for index, endpoint in enumerate(endpoints):
            candidate = route.with_endpoint(endpoint, tuple(endpoints[index + 1 :]))
            try:
                response: Any | None = None
                if self._native_egress_client is not None and native_request is not None:
                    try:
                        response = await self._native_egress_client.request(
                            NativeEgressRequest(
                                method=method,
                                url=native_request.url,
                                headers=native_request.headers,
                                body=native_request.body,
                                timeout_seconds=native_request.timeout_seconds,
                                connect_timeout_seconds=native_request.connect_timeout_seconds,
                                response_head_timeout_seconds=native_request.response_head_timeout_seconds,
                                proxy_url=endpoint.proxy_url,
                            )
                        )
                        if buffer_response:
                            response = await _buffer_response(response)
                    except NativeEgressUnavailable:
                        response = None
                    except NativeEgressError as exc:
                        raise _native_transport_error("request", endpoint.id, exc) from None
                if response is not None:
                    pass
                elif endpoint.scheme.startswith("socks"):
                    response = await _request_via_socks_proxy(
                        method,
                        url,
                        endpoint,
                        buffer_response=buffer_response,
                        **aiohttp_kwargs,
                    )
                else:
                    try:
                        response = await self._session.request(
                            method,
                            url,
                            proxy=endpoint.proxy_url,
                            **aiohttp_kwargs,
                        )
                    except Exception as exc:
                        raise _transport_error(
                            "request",
                            endpoint.id,
                            exc,
                            failure_phase="connect" if is_pre_dispatch_connection_failure(exc) else "request",
                            retryable_same_contract=is_pre_dispatch_connection_failure(exc),
                        ) from None
                    if buffer_response:
                        try:
                            response = await _buffer_response(response)
                        except Exception as exc:
                            # Headers prove the POST reached the response phase;
                            # a failed buffered read is neutral when classified
                            # as host-network loss, but it is never replay-safe.
                            raise _transport_error(
                                "response body",
                                endpoint.id,
                                exc,
                                failure_phase="body_read",
                                retryable_same_contract=False,
                            ) from None
                return CodexRequestResult(response, candidate, index > 0)
            except CodexTransportError as exc:
                # A confirmed pre-dispatch connect failure proves the request
                # never left for upstream, so trying the next endpoint in the
                # same resolved pool is safe even for a non-idempotent POST.
                # TLS verification failures are stable endpoint configuration
                # errors rather than transient connect losses; they keep the
                # idempotent-only rule.
                if index == len(endpoints) - 1 or not (
                    allow_fallback or (exc.retryable_same_contract and not exc.is_tls_verification_failure)
                ):
                    raise
            except Exception as exc:
                pre_dispatch = is_pre_dispatch_connection_failure(exc) and not isinstance(exc, aiohttp.ClientSSLError)
                if index == len(endpoints) - 1 or not (allow_fallback or pre_dispatch):
                    raise _transport_error(
                        "request",
                        endpoint.id,
                        exc,
                        failure_phase="connect" if is_pre_dispatch_connection_failure(exc) else "request",
                        retryable_same_contract=is_pre_dispatch_connection_failure(exc),
                    ) from None
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
        self,
        url: str,
        *,
        route: ResolvedUpstreamRoute,
        retry_handshake_status: bool = True,
        retry_network_errors: bool = True,
        **kwargs: Any,
    ) -> CodexWebSocketResult:
        if route is None:
            raise ValueError("Codex upstream calls require a resolved upstream proxy route")
        _reject_reserved(kwargs)
        endpoints = (route.endpoint, *route.fallbacks)
        for index, endpoint in enumerate(endpoints):
            candidate = route.with_endpoint(endpoint, tuple(endpoints[index + 1 :]))
            context: Any | None = None
            entered_context: Any | None = None
            try:
                websocket: Any | None = None
                native_request = _prepare_native_websocket_request(
                    url,
                    endpoint.proxy_url,
                    kwargs,
                )
                if self._native_egress_client is not None and native_request is not None:
                    try:
                        websocket = await self._native_egress_client.websocket(native_request)
                    except NativeEgressUnavailable:
                        websocket = None
                    except NativeEgressError as exc:
                        raise _native_transport_error("websocket", endpoint.id, exc) from None
                if websocket is not None:
                    return CodexWebSocketResult(
                        websocket,
                        None,
                        candidate,
                        index > 0,
                        native=True,
                    )
                elif endpoint.scheme.startswith("socks"):
                    websocket, context = await _open_ws_via_socks_proxy(url, endpoint, **kwargs)
                    entered_context = context
                else:
                    context = self._session.ws_connect(
                        url,
                        proxy=endpoint.proxy_url,
                        **kwargs,
                    )
                    if asyncio.iscoroutine(context):
                        context = await context
                    if hasattr(context, "__aenter__"):
                        websocket = await context.__aenter__()
                        entered_context = context
                    else:
                        websocket = context
                return CodexWebSocketResult(
                    websocket,
                    entered_context,
                    candidate,
                    index > 0,
                )
            except CodexTransportError as exc:
                if entered_context is not None and hasattr(entered_context, "__aexit__"):
                    await entered_context.__aexit__(None, None, None)
                if exc.is_tls_verification_failure:
                    raise
                if exc.status_code is not None and not retry_handshake_status:
                    raise _without_same_contract_retry(exc) from None
                if exc.status_code is None and not retry_network_errors:
                    raise _without_same_contract_retry(exc) from None
                if index == len(endpoints) - 1:
                    raise
            except Exception as exc:
                if entered_context is not None and hasattr(entered_context, "__aexit__"):
                    await entered_context.__aexit__(None, None, None)
                handshake_status = _transport_error_status_code(exc)
                if handshake_status is not None and not retry_handshake_status:
                    raise _transport_error(
                        "websocket",
                        endpoint.id,
                        exc,
                        failure_phase="connect",
                        retryable_same_contract=False,
                    ) from None
                if handshake_status is None and not retry_network_errors:
                    raise _transport_error(
                        "websocket",
                        endpoint.id,
                        exc,
                        failure_phase="connect",
                        retryable_same_contract=False,
                    ) from None
                if index == len(endpoints) - 1:
                    raise _transport_error(
                        "websocket",
                        endpoint.id,
                        exc,
                        failure_phase="connect",
                        # No response.create frame can be delivered before the
                        # websocket open returns to its caller.
                        retryable_same_contract=is_pre_dispatch_connection_failure(exc),
                    ) from None
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
    from app.core.clients.http import _shared_ssl_context

    connector = aiohttp.TCPConnector(limit=max_clients, ssl=_shared_ssl_context())
    return aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=None),
        trust_env=False,
        skip_auto_headers=_CODEX_SKIP_AUTO_IDENTITY_HEADERS,
    )


async def _request_via_socks_proxy(
    method: str,
    url: str,
    endpoint: ResolvedProxyEndpoint,
    *,
    buffer_response: bool,
    **kwargs: Any,
) -> Any:
    connector = _socks_proxy_connector(endpoint)
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=None),
        trust_env=False,
        skip_auto_headers=_CODEX_SKIP_AUTO_IDENTITY_HEADERS,
    )
    try:
        try:
            response = await session.request(method, url, **kwargs)
        except Exception as exc:
            raise _transport_error(
                "request",
                endpoint.id,
                exc,
                failure_phase="connect" if is_pre_dispatch_connection_failure(exc) else "request",
                retryable_same_contract=is_pre_dispatch_connection_failure(exc),
            ) from None
        if buffer_response:
            try:
                return await _buffer_response(response)
            except Exception as exc:
                raise _transport_error(
                    "response body",
                    endpoint.id,
                    exc,
                    failure_phase="body_read",
                    retryable_same_contract=False,
                ) from None
        return _SessionOwnedResponse(response, session)
    except Exception:
        await session.close()
        raise
    finally:
        if buffer_response:
            await session.close()


async def _open_ws_via_socks_proxy(url: str, endpoint: ResolvedProxyEndpoint, **kwargs: Any) -> tuple[Any, Any]:
    connector = _socks_proxy_connector(endpoint)
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=None),
        trust_env=False,
        skip_auto_headers=_CODEX_SKIP_AUTO_IDENTITY_HEADERS,
    )
    try:
        context = session.ws_connect(url, **kwargs)
        if asyncio.iscoroutine(context):
            context = await context
        websocket = await context.__aenter__() if hasattr(context, "__aenter__") else context
        return websocket, _SessionOwnedWebSocketContext(context, session)
    except BaseException:
        await session.close()
        raise


def _socks_proxy_connector(endpoint: ResolvedProxyEndpoint) -> ProxyConnector:
    from app.core.clients.http import _shared_ssl_context

    proxy_scheme = endpoint.proxy_url.split(":", 1)[0]
    return ProxyConnector(
        host=endpoint.host,
        port=endpoint.port,
        proxy_type=ProxyType.SOCKS5,
        username=endpoint.username,
        password=endpoint.password,
        rdns=proxy_scheme == "socks5h",
        ssl=_shared_ssl_context(),
    )


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


def _prepare_native_request(
    url: str,
    kwargs: Mapping[str, Any],
) -> _PreparedNativeRequest | None:
    supported = {
        "data",
        "files",
        "headers",
        "json",
        "params",
        "skip_auto_headers",
        "timeout",
    }
    if set(kwargs).difference(supported):
        return None

    raw_headers = kwargs.get("headers") or {}
    if not isinstance(raw_headers, Mapping):
        return None
    headers = {str(name): str(value) for name, value in raw_headers.items()}

    params = kwargs.get("params")
    if params is not None:
        try:
            url = str(URL(url).update_query(params))
        except (TypeError, ValueError):
            return None

    has_json = "json" in kwargs
    data = kwargs.get("data")
    files = kwargs.get("files")
    if has_json and (data is not None or files is not None):
        return None

    body: bytes | None
    if has_json:
        try:
            body = json.dumps(
                kwargs.get("json"),
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return None
        _set_header_default(headers, "Content-Type", "application/json")
    elif files is not None:
        encoded = _encode_native_multipart(data, files)
        if encoded is None:
            return None
        body, content_type = encoded
        _set_header_default(headers, "Content-Type", content_type)
    elif data is None:
        body = None
    elif isinstance(data, bytes):
        body = data
    elif isinstance(data, (bytearray, memoryview)):
        body = bytes(data)
    elif isinstance(data, str):
        body = data.encode("utf-8")
        _set_header_default(headers, "Content-Type", "text/plain; charset=utf-8")
    elif isinstance(data, Mapping):
        body = urlencode([(str(name), str(value)) for name, value in data.items()], doseq=True).encode()
        _set_header_default(headers, "Content-Type", "application/x-www-form-urlencoded")
    else:
        return None

    timeout_seconds, connect_timeout_seconds, response_head_timeout_seconds = _native_timeout_parts(
        kwargs.get("timeout")
    )
    if timeout_seconds is None:
        return None
    return _PreparedNativeRequest(
        url=url,
        headers=headers,
        body=body,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
        response_head_timeout_seconds=response_head_timeout_seconds,
    )


def _native_timeout_parts(value: Any) -> tuple[float | None, float | None, float | None]:
    if value is None:
        return 60.0, None, None
    if isinstance(value, aiohttp.ClientTimeout):
        total = float(value.total) if value.total is not None else 60.0
        connect = float(value.sock_connect) if value.sock_connect is not None else None
        response_head = float(value.sock_read) if value.sock_read is not None else None
    else:
        try:
            total = float(value)
        except (TypeError, ValueError):
            return None, None, None
        connect = None
        response_head = None
    if total <= 0 or (connect is not None and connect <= 0) or (response_head is not None and response_head <= 0):
        return None, None, None
    return total, connect, response_head


def _encode_native_multipart(data: Any, files: Any) -> tuple[bytes, str] | None:
    data_items = _form_items(data)
    file_items = _form_items(files)
    if data_items is None or file_items is None:
        return None
    boundary = f"codex-lb-{secrets.token_hex(16)}"
    chunks: list[bytes] = []
    for name, value in data_items:
        if not isinstance(value, (str, bytes, bytearray, memoryview, int, float, bool)):
            return None
        payload = (
            value.encode()
            if isinstance(value, str)
            else bytes(value)
            if not isinstance(value, (int, float, bool))
            else str(value).encode()
        )
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{_multipart_parameter(name)}"\r\n\r\n'.encode(),
                payload,
                b"\r\n",
            )
        )
    for name, value in file_items:
        if not isinstance(value, tuple) or len(value) < 2:
            return None
        filename = str(value[0] or name)
        file_value = value[1]
        content_type = str(value[2]) if len(value) >= 3 and value[2] else "application/octet-stream"
        if isinstance(file_value, str):
            payload = file_value.encode()
        elif isinstance(file_value, (bytes, bytearray, memoryview)):
            payload = bytes(file_value)
        else:
            return None
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{_multipart_parameter(name)}"; '
                    f'filename="{_multipart_parameter(filename)}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                payload,
                b"\r\n",
            )
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _form_items(value: Any) -> list[tuple[str, Any]] | None:
    if value is None:
        return []
    items = value.items() if isinstance(value, Mapping) else value
    try:
        return [(str(name), item) for name, item in items]
    except (TypeError, ValueError):
        return None


def _multipart_parameter(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")


def _set_header_default(headers: dict[str, str], name: str, value: str) -> None:
    if not any(existing.lower() == name.lower() for existing in headers):
        headers[name] = value


def _prepare_native_websocket_request(
    url: str,
    proxy_url: str,
    kwargs: Mapping[str, Any],
) -> NativeWebSocketRequest | None:
    supported = {
        "compress",
        "headers",
        "heartbeat",
        "max_msg_size",
        "protocols",
        "timeout",
    }
    if set(kwargs).difference(supported):
        return None
    raw_headers = kwargs.get("headers") or {}
    if not isinstance(raw_headers, Mapping):
        return None
    headers = {str(name): str(value) for name, value in raw_headers.items()}
    protocols = kwargs.get("protocols")
    if protocols:
        try:
            headers["sec-websocket-protocol"] = ", ".join(str(value) for value in protocols)
        except TypeError:
            return None
    try:
        connect_timeout = float(kwargs.get("timeout", 10.0))
        max_message_bytes = int(kwargs.get("max_msg_size", 4 * 1024 * 1024))
        compress = int(kwargs.get("compress", 0))
        heartbeat = kwargs.get("heartbeat")
        ping_interval_seconds = float(heartbeat) if heartbeat is not None else None
        ping_timeout_seconds = ping_interval_seconds / 2 if ping_interval_seconds is not None else None
    except (TypeError, ValueError):
        return None
    if (
        compress != 15
        or connect_timeout <= 0
        or max_message_bytes <= 0
        or (ping_interval_seconds is not None and ping_interval_seconds <= 0)
    ):
        return None
    return NativeWebSocketRequest(
        url=url,
        headers=headers,
        connect_timeout_seconds=connect_timeout,
        max_message_bytes=max_message_bytes,
        ping_interval_seconds=ping_interval_seconds,
        ping_timeout_seconds=ping_timeout_seconds,
        proxy_url=proxy_url,
    )


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


def _transport_error(
    operation: str,
    endpoint_id: str,
    exc: Exception,
    *,
    failure_phase: str,
    retryable_same_contract: bool,
) -> CodexTransportError:
    process_network_failure = is_process_network_failure(exc, include_permanent_dns=False)
    return CodexTransportError(
        codex_transport_error_message(operation, endpoint_id, exc),
        status_code=_transport_error_status_code(exc),
        # A missing proxy hostname is endpoint configuration, not evidence
        # that the host lost DNS. Transient DNS and local route loss remain
        # process-wide failures and are safe to classify without exposing the
        # credential-bearing original exception.
        error_code=PROCESS_NETWORK_UNAVAILABLE_CODE if process_network_failure else None,
        retryable_same_contract=retryable_same_contract,
        failure_phase=failure_phase,
        is_tls_verification_failure=isinstance(exc, aiohttp.ClientSSLError),
        handshake_headers=_transport_error_handshake_headers(exc),
        handshake_message=_transport_error_handshake_message(exc),
    )


def _transport_error_handshake_headers(exc: Exception) -> Mapping[str, str] | None:
    """Handshake response headers of a wrapped upgrade rejection, else ``None``.

    ``aiohttp.WSServerHandshakeError`` carries the response headers directly;
    exceptions wrapping a response object carry them one level down.
    """

    response = getattr(exc, "response", None)
    for source in (exc, response):
        if source is None:
            continue
        headers = getattr(source, "headers", None)
        if headers is not None:
            return headers
    return None


def _transport_error_handshake_message(exc: Exception) -> str | None:
    message = getattr(exc, "message", None)
    return message if isinstance(message, str) else None


def _native_transport_error(
    operation: str,
    endpoint_id: str,
    exc: NativeEgressError,
) -> CodexTransportError:
    if isinstance(exc, NativeEgressTransportError):
        process_failure = exc.failure_phase in {
            "helper_exit",
            "helper_read",
            "helper_write",
            "shutdown",
        }
        return CodexTransportError(
            codex_transport_error_message(operation, endpoint_id, exc),
            status_code=exc.status_code,
            error_code=PROCESS_NETWORK_UNAVAILABLE_CODE if process_failure else None,
            retryable_same_contract=(exc.retryable_same_contract and not exc.is_tls_verification_failure),
            failure_phase=exc.failure_phase,
            is_tls_verification_failure=exc.is_tls_verification_failure,
            handshake_headers=dict(exc.headers) if exc.headers else None,
            handshake_message=(exc.body.decode("utf-8", errors="replace") if exc.body else None),
        )
    return CodexTransportError(
        codex_transport_error_message(operation, endpoint_id, exc),
        retryable_same_contract=False,
        failure_phase="protocol",
    )


def _without_same_contract_retry(exc: CodexTransportError) -> CodexTransportError:
    return CodexTransportError(
        str(exc),
        status_code=exc.status_code,
        error_code=exc.error_code,
        retryable_same_contract=False,
        failure_phase=exc.failure_phase,
        is_tls_verification_failure=exc.is_tls_verification_failure,
        handshake_headers=exc.handshake_headers,
        handshake_message=exc.handshake_message,
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
