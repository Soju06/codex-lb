"""Strict upstream proxy route resolution for Codex/OpenAI egress."""

from app.core.upstream_proxy.resolver import UpstreamProxyRouteError, resolve_upstream_route
from app.core.upstream_proxy.types import ResolvedProxyEndpoint, ResolvedUpstreamRoute

__all__ = [
    "ResolvedProxyEndpoint",
    "ResolvedUpstreamRoute",
    "UpstreamProxyRouteError",
    "resolve_upstream_route",
]
