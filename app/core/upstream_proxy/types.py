from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True, slots=True)
class ResolvedProxyEndpoint:
    id: str
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def proxy_url(self) -> str:
        scheme = "socks5h" if self.scheme == "socks5" else self.scheme
        auth = ""
        if self.username:
            auth = f"{quote(self.username, safe='')}:{quote(self.password or '', safe='')}@"
        return f"{scheme}://{auth}{self.host}:{self.port}"


@dataclass(frozen=True, slots=True)
class ResolvedUpstreamRoute:
    mode: str
    pool_id: str
    endpoint: ResolvedProxyEndpoint
    fallbacks: tuple[ResolvedProxyEndpoint, ...] = ()

    @property
    def endpoint_id(self) -> str:
        return self.endpoint.id

    @property
    def proxy_url(self) -> str:
        return self.endpoint.proxy_url

    def with_endpoint(
        self,
        endpoint: ResolvedProxyEndpoint,
        fallbacks: tuple[ResolvedProxyEndpoint, ...],
    ) -> "ResolvedUpstreamRoute":
        return ResolvedUpstreamRoute(self.mode, self.pool_id, endpoint, fallbacks)
