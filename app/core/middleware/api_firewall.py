from __future__ import annotations

from collections.abc import Awaitable, Callable
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.db.session import get_background_session
from app.modules.firewall.repository import FirewallRepository
from app.modules.firewall.service import FirewallService


def add_api_firewall_middleware(app: FastAPI) -> None:
    settings = get_settings()
    trusted_proxy_networks = _parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs)

    @app.middleware("http")
    async def api_firewall_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if not _is_protected_api_path(path):
            return await call_next(request)

        client_ip = _resolve_client_ip(
            request,
            trust_proxy_headers=settings.firewall_trust_proxy_headers,
            trusted_proxy_networks=trusted_proxy_networks,
        )
        async with get_background_session() as session:
            service = FirewallService(FirewallRepository(session))
            is_allowed = await service.is_ip_allowed(client_ip)

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
    socket_ip = request.client.host if request.client else None
    if trust_proxy_headers and socket_ip and _is_trusted_proxy_source(socket_ip, trusted_proxy_networks):
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            first = forwarded_for.split(",", 1)[0].strip()
            if _is_valid_ip(first):
                return first
    return socket_ip


def _parse_trusted_proxy_networks(cidrs: list[str]) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(ip_network(cidr, strict=False) for cidr in cidrs)


def _is_trusted_proxy_source(
    host: str,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> bool:
    if not trusted_proxy_networks:
        return False
    try:
        source_ip = ip_address(host)
    except ValueError:
        return False
    return any(source_ip in network for network in trusted_proxy_networks)


def _is_valid_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True
