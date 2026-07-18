from __future__ import annotations

from collections.abc import Awaitable, Callable
from ipaddress import IPv4Network, IPv6Network
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.middleware.firewall_cache import get_firewall_ip_cache
from app.core.request_locality import (
    FORWARDED_CHAIN_HEADER_NAMES,
    parse_trusted_proxy_networks,
    resolve_connection_client_ip,
)
from app.db.session import get_background_session
from app.modules.firewall.repository import FirewallRepository
from app.modules.firewall.service import FirewallRepositoryPort, FirewallService


def add_api_firewall_middleware(app: FastAPI) -> None:
    settings = get_settings()
    trusted_proxy_networks = parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs)
    firewall_cache = get_firewall_ip_cache()

    @app.middleware("http")
    async def api_firewall_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if not _is_protected_api_path(path):
            return await call_next(request)

        client_ip = resolve_connection_client_ip(
            request.headers,
            request.client.host if request.client else None,
            trust_proxy_headers=settings.firewall_trust_proxy_headers,
            trusted_proxy_networks=trusted_proxy_networks,
            allowed_proxy_header_names=FORWARDED_CHAIN_HEADER_NAMES,
        )
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
            return await call_next(request)

        return JSONResponse(
            status_code=403,
            content=openai_error("ip_forbidden", "Access denied for client IP", error_type="access_error"),
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
