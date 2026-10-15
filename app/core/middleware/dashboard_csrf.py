"""Reject cross-site browser requests that would mutate dashboard state.

The dashboard authenticates with a ``SameSite=Lax`` session cookie or, on
loopback without a password, with no credential at all. Neither protects a
mutation that a foreign page triggers from the operator's browser, so this
middleware inspects the browser-controlled ``Sec-Fetch-Site`` and ``Origin``
headers before routing and answers ``403 cross_site_request_rejected`` when
the request did not come from the dashboard's own origin.

Only unsafe methods under ``/api/`` are inspected. Bearer-authenticated
data-plane routes (``/api/fleet/``, ``/api/codex/``) and any request carrying
an ``Authorization: Bearer`` header are exempt because a foreign page cannot
attach that header without CORS consent. Requests without either header come
from non-browser clients and pass through unchanged.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import dashboard_error

CROSS_SITE_REQUEST_REJECTED_CODE = "cross_site_request_rejected"
CROSS_SITE_REQUEST_REJECTED_MESSAGE = "Cross-site dashboard requests are not allowed"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_PROTECTED_PATH_PREFIX = "/api/"
_BEARER_AUTHENTICATED_PATH_PREFIXES = ("/api/fleet/", "/api/codex/")
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})
_DEFAULT_PORTS = {"http": 80, "https": 443}

logger = logging.getLogger(__name__)


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _has_bearer_authorization(headers: list[tuple[bytes, bytes]]) -> bool:
    authorization = _header(headers, b"authorization")
    if authorization is None:
        return False
    scheme, _, credentials = authorization.strip().partition(" ")
    return scheme.lower() == "bearer" and credentials.strip() != ""


def _origin_tuple(scheme: str, netloc: str) -> tuple[str, str, int] | None:
    scheme = scheme.lower()
    default_port = _DEFAULT_PORTS.get(scheme)
    if default_port is None:
        return None
    try:
        parsed = urlsplit(f"//{netloc}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not hostname:
        return None
    return scheme, hostname.lower(), port if port is not None else default_port


def _parse_origin_header(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return _origin_tuple(parsed.scheme, parsed.netloc)


def _request_origin(scope: Scope, headers: list[tuple[bytes, bytes]]) -> tuple[str, str, int] | None:
    host = _header(headers, b"host")
    if host is None:
        return None
    return _origin_tuple(str(scope.get("scheme", "http")), host.strip())


def _is_protected(scope: Scope, headers: list[tuple[bytes, bytes]]) -> bool:
    if scope["type"] != "http":
        return False
    if str(scope.get("method", "GET")).upper() in _SAFE_METHODS:
        return False
    path = get_route_path(scope)
    if not path.startswith(_PROTECTED_PATH_PREFIX) or path.startswith(_BEARER_AUTHENTICATED_PATH_PREFIXES):
        return False
    return not _has_bearer_authorization(headers)


def _format_origin(origin: tuple[str, str, int] | None) -> str:
    if origin is None:
        return "-"
    scheme, host, port = origin
    host_text = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host_text}:{port}"


def _rejection_reason(scope: Scope, headers: list[tuple[bytes, bytes]]) -> str | None:
    """Return a log-safe reason when the request must be rejected, else ``None``.

    The reason names only the ``Sec-Fetch-Site`` value or the presented ``Origin``
    (scheme, host, port; path and query stripped) next to the expected origin
    derived from the request scheme and ``Host`` header. No other header values
    are included.
    """

    fetch_site = _header(headers, b"sec-fetch-site")
    if fetch_site is not None:
        site = fetch_site.strip().lower()
        return None if site in _ALLOWED_FETCH_SITES else f"sec_fetch_site={site or '-'}"

    origin = _header(headers, b"origin")
    if origin is None:
        return None
    expected = _request_origin(scope, headers)
    if origin.strip().lower() == "null":
        return f"origin=null expected={_format_origin(expected)}"
    presented = _parse_origin_header(origin)
    if presented is None or expected is None or presented != expected:
        return f"origin={_format_origin(presented)} expected={_format_origin(expected)}"
    return None


class DashboardCsrfMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
        if not _is_protected(scope, headers):
            await self.app(scope, receive, send)
            return

        reason = _rejection_reason(scope, headers)
        if reason is None:
            await self.app(scope, receive, send)
            return

        logger.warning(
            "dashboard_error_response method=%s path=%s status=403 code=%s %s",
            scope.get("method"),
            get_route_path(scope),
            CROSS_SITE_REQUEST_REJECTED_CODE,
            reason,
        )
        response = JSONResponse(
            status_code=403,
            content=dashboard_error(CROSS_SITE_REQUEST_REJECTED_CODE, CROSS_SITE_REQUEST_REJECTED_MESSAGE),
        )
        await response(scope, receive, send)


def add_dashboard_csrf_middleware(app: FastAPI) -> None:
    app.add_middleware(cast(Any, DashboardCsrfMiddleware))


__all__ = [
    "CROSS_SITE_REQUEST_REJECTED_CODE",
    "CROSS_SITE_REQUEST_REJECTED_MESSAGE",
    "DashboardCsrfMiddleware",
    "add_dashboard_csrf_middleware",
]
