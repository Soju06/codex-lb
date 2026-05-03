from __future__ import annotations

from urllib.parse import urlparse

from aiohttp_socks import ProxyConnector

SUPPORTED_UPSTREAM_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})


class UpstreamProxyConfigurationError(ValueError):
    pass


def normalize_upstream_proxy_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise UpstreamProxyConfigurationError("Proxy URL is empty")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in SUPPORTED_UPSTREAM_PROXY_SCHEMES:
        raise UpstreamProxyConfigurationError("Proxy URL scheme must be http, https, socks5, or socks5h")
    if not parsed.hostname:
        raise UpstreamProxyConfigurationError("Proxy URL must include a host")
    return url


def redact_upstream_proxy_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.username and not parsed.password:
        return value
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return parsed._replace(netloc=host).geturl()


def is_socks_upstream_proxy(value: str | None) -> bool:
    if not value:
        return False
    return urlparse(value).scheme.lower() in {"socks5", "socks5h"}


def aiohttp_proxy_kwargs(value: str | None) -> dict[str, str]:
    if not value or is_socks_upstream_proxy(value):
        return {}
    return {"proxy": value}


def aiohttp_proxy_url(value: str | None) -> str | None:
    if not value or is_socks_upstream_proxy(value):
        return None
    return value


def socks_proxy_connector(value: str) -> ProxyConnector:
    return ProxyConnector.from_url(value)
