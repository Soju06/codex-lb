from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import jwt
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.auth.dashboard_mode import DashboardAuthMode, DashboardRequestAuth
from app.core.config.settings import get_settings
from app.core.middleware.api_firewall import _is_trusted_proxy_source, _parse_trusted_proxy_networks
from app.core.resilience.overload import deny_websocket_with_http_response

logger = logging.getLogger(__name__)
_ACCESS_ASSERTION_REQUIRED_DETAIL = "A valid Cloudflare Access assertion is required"


class DashboardAuthProxyHeaderSanitizerMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        settings = get_settings()
        self._enabled = settings.dashboard_auth_mode == DashboardAuthMode.TRUSTED_HEADER
        self._trust_proxy_headers = settings.firewall_trust_proxy_headers
        self._trusted_proxy_networks = _parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs)
        self._trusted_header_name = settings.dashboard_auth_proxy_header.lower().encode("latin-1")
        self._access_assertion_header_name = settings.dashboard_access_jwt_header.lower().encode("latin-1")
        self._access_jwt_issuer = settings.dashboard_access_jwt_issuer
        self._access_jwt_required = settings.dashboard_access_jwt_required
        self._access_jwt_audiences = tuple(settings.dashboard_access_jwt_audiences)
        self._access_allowed_email_domains = frozenset(
            domain.strip().lower().lstrip("@")
            for domain in settings.dashboard_access_allowed_email_domains
            if domain.strip()
        )
        self._access_jwks_client = (
            jwt.PyJWKClient(
                f"{self._access_jwt_issuer.rstrip('/')}/cdn-cgi/access/certs",
                cache_keys=True,
                lifespan=300,
                timeout=5,
            )
            if self._access_jwt_issuer
            else None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = cast(str, scope.get("type", ""))
        if not self._enabled or scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if scope_type == "websocket" and self._access_jwks_client is None:
            await self.app(scope, receive, send)
            return

        client = cast(tuple[str, int] | None, scope.get("client"))
        client_host = client[0] if client is not None else None
        trusted_source = (
            self._trust_proxy_headers
            and client_host
            and _is_trusted_proxy_source(client_host, self._trusted_proxy_networks)
        )

        headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
        access_jwt_required_for_path = self._access_jwt_required and _is_access_jwt_required_path(
            cast(str, scope.get("path", "")),
            cast(str, scope.get("method", "GET" if scope_type == "websocket" else "")),
        )
        # A configured Access assertion is the authentication boundary. Verify
        # it cryptographically even when an earlier proxy-aware middleware has
        # rewritten scope.client to the original internet client address.
        # The legacy raw trusted-header path below still requires socket CIDR
        # provenance.
        if self._access_jwks_client is not None:
            assertion = _header_value(headers, self._access_assertion_header_name)
            actor = await self._validate_access_assertion(assertion)
            sanitized_headers = _filter_headers(
                _filter_headers(headers, self._trusted_header_name), self._access_assertion_header_name
            )
            next_scope = {**scope, "headers": sanitized_headers}
            if actor is not None:
                state = dict(cast(dict[str, Any], scope.get("state", {})))
                state["dashboard_request_auth"] = DashboardRequestAuth(
                    mode=DashboardAuthMode.TRUSTED_HEADER,
                    actor=actor,
                )
                next_scope["state"] = state
            elif access_jwt_required_for_path:
                await _deny_access_assertion_required(next_scope, receive, send)
                return
            await self.app(next_scope, receive, send)
            return

        if trusted_source:
            await self.app(scope, receive, send)
            return

        protected_headers = {self._trusted_header_name, self._access_assertion_header_name}
        if not any(name.lower() in protected_headers for name, _ in headers):
            await self.app(scope, receive, send)
            return

        scrubbed_headers = headers
        for protected_header in protected_headers:
            scrubbed_headers = _filter_headers(scrubbed_headers, protected_header)
        scrubbed_scope = {**scope, "headers": scrubbed_headers}
        await self.app(scrubbed_scope, receive, send)

    async def _validate_access_assertion(self, assertion: str | None) -> str | None:
        if not assertion or self._access_jwks_client is None or self._access_jwt_issuer is None:
            return None
        try:
            claims = await asyncio.to_thread(self._decode_access_assertion, assertion)
        except Exception as exc:
            logger.warning("cloudflare_access_assertion_rejected reason=%s", type(exc).__name__)
            return None
        email = claims.get("email")
        if not isinstance(email, str):
            return None
        normalized_email = email.strip().lower()
        _, separator, domain = normalized_email.rpartition("@")
        if not separator or domain not in self._access_allowed_email_domains:
            return None
        return normalized_email

    def _decode_access_assertion(self, assertion: str) -> dict[str, Any]:
        if self._access_jwks_client is None or self._access_jwt_issuer is None:
            raise ValueError("Access JWT validation is not configured")
        signing_key = self._access_jwks_client.get_signing_key_from_jwt(assertion)
        claims = jwt.decode(
            assertion,
            signing_key.key,
            algorithms=["RS256"],
            audience=list(self._access_jwt_audiences),
            issuer=self._access_jwt_issuer,
            options={"require": ["exp", "iss", "aud", "email"]},
        )
        return claims


def add_dashboard_auth_proxy_middleware(app: FastAPI) -> None:
    app.add_middleware(cast(Any, DashboardAuthProxyHeaderSanitizerMiddleware))


def _filter_headers(headers: list[tuple[bytes, bytes]], target: bytes) -> list[tuple[bytes, bytes]]:
    return [(name, value) for name, value in headers if name.lower() != target]


def _header_value(headers: list[tuple[bytes, bytes]], target: bytes) -> str | None:
    for name, value in headers:
        if name.lower() == target:
            return value.decode("latin-1")
    return None


async def _deny_access_assertion_required(scope: Scope, receive: Receive, send: Send) -> None:
    content = {"detail": _ACCESS_ASSERTION_REQUIRED_DETAIL}
    if scope["type"] == "websocket":
        await deny_websocket_with_http_response(
            receive,
            send,
            status_code=401,
            payload=content,
        )
        return
    response = JSONResponse(status_code=401, content=content)
    await response(scope, receive, send)


_ACCESS_JWT_PROTECTED_INTERNAL_MUTATION_PATHS = frozenset({"/internal/drain/start", "/internal/drain/stop"})
_ACCESS_JWT_API_KEY_PREFIXES = (
    "/api/codex",
    "/api/fleet",
    "/backend-api",
    "/internal/bridge",
    "/v1",
)


def _is_access_jwt_required_path(path: str, method: str) -> bool:
    normalized_method = method.upper()
    if path == "/health" or path.startswith("/health/"):
        return False
    if path in _ACCESS_JWT_PROTECTED_INTERNAL_MUTATION_PATHS and normalized_method not in {"GET", "HEAD"}:
        return True
    if path == "/internal/drain/status":
        return False
    if path.startswith("/internal/"):
        return False
    if any(path == prefix or path.startswith(f"{prefix}/") for prefix in _ACCESS_JWT_API_KEY_PREFIXES):
        return False
    return True


__all__ = ["add_dashboard_auth_proxy_middleware", "DashboardAuthProxyHeaderSanitizerMiddleware"]
