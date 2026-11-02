"""Relay of the `/backend-api/*` reads that have no pool analogue.

Pointing the Codex CLI's ``chatgpt_base_url`` at the balancer moves its whole
backend surface here. Workspace routing (``/wham/accounts/check``) and plugin
listings describe the caller's own account, so they are relayed upstream with
the caller's credential only; the pool never attaches its own.

The transport policy lives here — which headers may leave, which paths stay
with the pool, and the refusal to follow redirects — so the route is only a
decode/call/map shim.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

import aiohttp

from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.core.exceptions import ProxyAuthError, ProxyUpstreamError

# The caller's own credential and the metadata the Codex CLI's discovery reads. Everything
# else the CLI sends (cookies, a dashboard session, an alternate auth header, proxy
# credentials) belongs to a different audience and must not leave for the upstream backend.
RELAY_HEADER_NAMES: Final[frozenset[str]] = frozenset(
    {"authorization", "chatgpt-account-id", "accept", "accept-language", "user-agent"}
)
RELAY_HEADER_PREFIXES: Final[tuple[str, ...]] = ("x-codex-", "openai-")
# The pool-owned namespace keeps its own handlers; a GET that only the relay could answer
# here must behave as if no such route existed rather than borrow the relay.
RELAY_RESERVED_PREFIXES: Final[tuple[str, ...]] = ("codex", "wham/usage")
RELAY_TIMEOUT_SECONDS: Final[float] = 30.0


class BackendRelayReservedPath(Exception):
    """The path belongs to a pool-owned handler, so the relay must not answer it."""


@dataclass(frozen=True, slots=True)
class BackendRelayResponse:
    status_code: int
    body: bytes
    content_type: str | None


async def relay_backend_get(
    path: str,
    *,
    headers: Iterable[tuple[str, str]],
    query: Iterable[tuple[str, str]],
) -> BackendRelayResponse:
    """Relay one `/backend-api/*` read to the configured upstream backend.

    Raises ``BackendRelayReservedPath`` for a path the pool serves itself,
    ``ProxyAuthError`` without a caller Bearer, and ``ProxyUpstreamError`` when
    the upstream redirects or cannot be reached.
    """

    header_items = list(headers)
    if _is_reserved(path):
        raise BackendRelayReservedPath(path)
    _require_bearer(header_items)

    url = f"{get_settings().upstream_base_url.rstrip('/')}/{path}"
    timeout = aiohttp.ClientTimeout(total=RELAY_TIMEOUT_SECONDS)
    try:
        async with lease_http_session() as client_session:
            async with client_session.get(
                url,
                headers=_relay_headers(header_items),
                params=list(query),
                timeout=timeout,
                # A redirect would hand these headers to another host and change the answer.
                allow_redirects=False,
            ) as upstream:
                if 300 <= upstream.status < 400:
                    raise ProxyUpstreamError("The upstream backend redirected this read")
                return BackendRelayResponse(
                    status_code=upstream.status,
                    body=await upstream.read(),
                    content_type=upstream.headers.get("content-type"),
                )
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ProxyUpstreamError("Unable to reach the upstream backend") from exc


def _is_reserved(path: str) -> bool:
    return any(path == reserved or path.startswith(f"{reserved}/") for reserved in RELAY_RESERVED_PREFIXES)


def _require_bearer(headers: Iterable[tuple[str, str]]) -> None:
    authorization = next((value for name, value in headers if name.lower() == "authorization"), "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ProxyAuthError("Missing ChatGPT token in Authorization header")


def _relay_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {
        name: value
        for name, value in headers
        if name.lower() in RELAY_HEADER_NAMES or name.lower().startswith(RELAY_HEADER_PREFIXES)
    }
