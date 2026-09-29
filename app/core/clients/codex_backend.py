"""Streaming passthrough for Codex's ChatGPT-backend calls.

Codex sends these calls to ``chatgpt_base_url`` under the user's own ChatGPT
login. When that base URL is codex-lb's ``/backend-api``, the calls codex-lb
does not serve are forwarded here to the same upstream path, under the
caller's own token (never a pool account's), with the body streamed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from yarl import URL

from app.core.clients.codex import CodexClient, create_codex_session, release_codex_response
from app.core.clients.http import HttpClientLease, acquire_http_client
from app.core.clients.proxy import _build_upstream_headers
from app.core.clock import REAL_CLOCK, Clock
from app.core.config.dashboard_overrides import with_dashboard_overrides
from app.core.config.settings import get_settings
from app.core.upstream_proxy import ResolvedUpstreamRoute

_BODY_CHUNK_BYTES = 64 * 1024

# Headers the caller sends that must not reach upstream: cookies are the
# caller's browser state for codex-lb, and compressed bodies would have to be
# decoded per transport before relay, so upstream is asked for identity.
_DROPPED_REQUEST_HEADERS = frozenset({"cookie", "accept-encoding"})


@dataclass(slots=True)
class CodexBackendStream:
    """An open upstream response whose body has not been read yet."""

    status_code: int
    headers: Mapping[str, str]
    _response: Any
    _owned_session: aiohttp.ClientSession | None = None
    _lease: HttpClientLease | None = None
    _clock: Clock = REAL_CLOCK
    opened_at: float = field(default=0.0, init=False)
    bytes_relayed: int = field(default=0, init=False)
    body_completed: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.opened_at = self._clock.monotonic()

    async def iter_body(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.content.iter_chunked(_BODY_CHUNK_BYTES):
                if chunk:
                    self.bytes_relayed += len(chunk)
                    yield chunk
            self.body_completed = True
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        """Release the upstream connection; safe to call more than once."""

        if self._closed:
            return
        self._closed = True
        try:
            await release_codex_response(self._response)
        finally:
            if self._owned_session is not None:
                await self._owned_session.close()
            if self._lease is not None:
                await self._lease.close()


def backend_upstream_url(rest: str, raw_query: str = "") -> URL:
    """Map ``/backend-api/<rest>?<raw_query>`` on codex-lb to the same URL upstream.

    Built as an already-encoded URL so the path and query reach upstream
    byte-for-byte, without yarl re-quoting them.
    """

    settings = with_dashboard_overrides(get_settings())
    base = settings.upstream_base_url.rstrip("/")
    if "/backend-api" not in base:
        base = f"{base}/backend-api"
    return URL(f"{base}/{rest}{'?' + raw_query if raw_query else ''}", encoded=True)


def _passthrough_request_headers(inbound: Mapping[str, str], access_token: str, account_id: str) -> dict[str, str]:
    kept = {key: value for key, value in inbound.items() if key.lower() not in _DROPPED_REQUEST_HEADERS}
    headers = _build_upstream_headers(kept, access_token, account_id, accept=inbound.get("accept", "*/*"))
    for key in [key for key in headers if key.lower() == "content-type"]:
        del headers[key]
    content_type = inbound.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    headers["Accept-Encoding"] = "identity"
    return headers


async def open_codex_backend_stream(
    rest: str,
    *,
    method: str,
    body: bytes | None,
    raw_query: str,
    inbound_headers: Mapping[str, str],
    access_token: str,
    chatgpt_account_id: str,
    route: ResolvedUpstreamRoute | None,
) -> CodexBackendStream:
    """Send one passthrough request and return its response before reading the body.

    ``route`` is the caller's account egress route; ``None`` means the account
    has no proxy binding and egress is direct, as for its usage-identity check.
    Redirects are returned to the caller, never followed here.
    """

    settings = with_dashboard_overrides(get_settings())
    url = backend_upstream_url(rest, raw_query)
    headers = _passthrough_request_headers(inbound_headers, access_token, chatgpt_account_id)
    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=settings.upstream_connect_timeout_seconds,
        sock_read=settings.stream_idle_timeout_seconds,
    )
    if route is not None:
        session = create_codex_session()
        try:
            response = await CodexClient(session).request(
                method,
                url,
                route=route,
                allow_redirects=False,
                data=body,
                headers=headers,
                timeout=timeout,
                buffer_response=False,
            )
        except BaseException:
            await session.close()
            raise
        return CodexBackendStream(
            status_code=int(getattr(response, "status_code", getattr(response, "status", 0))),
            headers=dict(getattr(response, "headers", {}) or {}),
            _response=response,
            _owned_session=session,
        )

    lease = await acquire_http_client()
    try:
        response = await lease.client.session.request(
            method,
            url,
            allow_redirects=False,
            data=body,
            headers=headers,
            timeout=timeout,
        )
    except BaseException:
        await lease.close()
        raise
    return CodexBackendStream(
        status_code=response.status,
        headers=dict(response.headers),
        _response=response,
        _lease=lease,
    )


__all__ = ["CodexBackendStream", "open_codex_backend_stream", "backend_upstream_url"]
