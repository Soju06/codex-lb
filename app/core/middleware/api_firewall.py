from __future__ import annotations

from ipaddress import IPv4Network, IPv6Network
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.middleware.firewall_cache import FirewallIPCache, get_firewall_ip_cache
from app.core.request_locality import (
    FORWARDED_CHAIN_HEADER_NAMES,
    parse_trusted_proxy_networks,
    resolve_connection_client_ip,
)
from app.db.session import get_background_session
from app.modules.firewall.repository import FirewallRepository
from app.modules.firewall.service import FirewallRepositoryPort, FirewallService


class ApiFirewallMiddleware:
    """IP allowlist for ``/v1/*`` and ``/backend-api/codex/*`` HTTP requests.

    Pure ASGI: unprotected paths pass through with a single prefix check, and
    allow decisions are answered from the in-memory TTL cache; the database is
    only consulted on a cache miss.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        trust_proxy_headers: bool,
        trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
        firewall_cache: FirewallIPCache,
    ) -> None:
        self.app = app
        self._trust_proxy_headers = trust_proxy_headers
        self._trusted_proxy_networks = trusted_proxy_networks
        self._firewall_cache = firewall_cache

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_protected_api_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = resolve_connection_client_ip(
            Headers(scope=scope),
            client[0] if client else None,
            trust_proxy_headers=self._trust_proxy_headers,
            trusted_proxy_networks=self._trusted_proxy_networks,
            allowed_proxy_header_names=FORWARDED_CHAIN_HEADER_NAMES,
        )
        firewall_cache = self._firewall_cache
        cached_decision = await firewall_cache.is_allowed(client_ip) if client_ip is not None else None
        if cached_decision is not None:
            is_allowed = cached_decision
        else:
            version_before_read = firewall_cache.version
            async with get_background_session() as session:
                repository = cast(FirewallRepositoryPort, FirewallRepository(session))
                service = FirewallService(repository)
                is_allowed = await service.is_ip_allowed(client_ip)
            if client_ip is not None:
                await firewall_cache.set(client_ip, is_allowed, if_version=version_before_read)

        if is_allowed:
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=403,
            content=openai_error("ip_forbidden", "Access denied for client IP", error_type="access_error"),
        )
        await response(scope, receive, send)


def add_api_firewall_middleware(app: FastAPI) -> None:
    settings = get_settings()
    app.add_middleware(
        ApiFirewallMiddleware,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs),
        firewall_cache=get_firewall_ip_cache(),
    )


def _is_protected_api_path(path: str) -> bool:
    if path == "/backend-api/codex" or path.startswith("/backend-api/codex/"):
        return True
    return path == "/v1" or path.startswith("/v1/")


def _resolve_client_ip(
    request: Request,
    *,
    trust_proxy_headers: bool,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...] = (),
) -> str | None:
    return resolve_connection_client_ip(
        request.headers,
        request.client.host if request.client else None,
        trust_proxy_headers=trust_proxy_headers,
        trusted_proxy_networks=trusted_proxy_networks,
        allowed_proxy_header_names=FORWARDED_CHAIN_HEADER_NAMES,
    )
