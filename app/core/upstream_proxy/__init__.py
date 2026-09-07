"""Strict upstream proxy route resolution for Codex/OpenAI egress."""

from app.core.upstream_proxy.resolver import UpstreamProxyRouteError, resolve_proxy_endpoint, resolve_upstream_route
from app.core.upstream_proxy.types import ResolvedProxyEndpoint, ResolvedUpstreamRoute, sends_plaintext_credentials

__all__ = [
    "ResolvedProxyEndpoint",
    "ResolvedUpstreamRoute",
    "sends_plaintext_credentials",
    "UpstreamProxyRouteError",
    "resolve_proxy_endpoint",
    "resolve_upstream_route",
]
