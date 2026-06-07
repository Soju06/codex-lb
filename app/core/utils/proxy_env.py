from __future__ import annotations

import os
import urllib.request
from collections.abc import Mapping
from urllib.parse import urlparse, urlunparse

_WEBSOCKET_PROXY_ENV_PRIORITY: dict[str, tuple[str, ...]] = {
    "ws": (
        "ws",
        "socks",
        "https",
        "http",
        "all",
    ),
    "wss": (
        "wss",
        "socks",
        "https",
        "all",
    ),
}

STANDARD_OUTBOUND_PROXY_ENV_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(f"{name}_proxy" for names in _WEBSOCKET_PROXY_ENV_PRIORITY.values() for name in names)
)


def outbound_proxy_env_configured(environ: Mapping[str, str | None] = os.environ) -> bool:
    return any(
        name.lower() in STANDARD_OUTBOUND_PROXY_ENV_NAMES and value is not None and value.strip()
        for name, value in environ.items()
    )


def resolve_websocket_proxy_from_env(url: str, environ: Mapping[str, str | None] = os.environ) -> str | None:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    env_names = _WEBSOCKET_PROXY_ENV_PRIORITY.get(scheme)
    if env_names is None:
        return None

    hostname = parsed.hostname
    port = parsed.port or (443 if scheme == "wss" else 80)
    proxies = _sanitized_proxy_env(environ)
    proxy_bypass_environment = getattr(urllib.request, "proxy_bypass_environment")
    if hostname and proxy_bypass_environment(f"{hostname}:{port}", proxies):
        return None

    for name in env_names:
        proxy_url = proxies.get(name)
        if proxy_url is not None:
            return proxy_url
    return None


def _sanitized_proxy_env(environ: Mapping[str, str | None] = os.environ) -> dict[str, str]:
    proxies: dict[str, str] = {}
    for name, value in environ.items():
        if name == "HTTP_PROXY" and environ.get("REQUEST_METHOD"):
            continue
        _add_proxy_env_value(proxies, name, value)
    for name, value in environ.items():
        if name == "HTTP_PROXY" and environ.get("REQUEST_METHOD"):
            continue
        if name == name.lower():
            _add_proxy_env_value(proxies, name, value)
    return proxies


def _add_proxy_env_value(proxies: dict[str, str], name: str, value: str | None) -> None:
    normalized_name = name.lower()
    if not normalized_name.endswith("_proxy"):
        return
    proxy_name = normalized_name.removesuffix("_proxy")
    if not value:
        proxies.pop(proxy_name, None)
        return
    proxies[proxy_name] = _normalize_proxy_url(proxy_name, value.strip())


def _normalize_proxy_url(proxy_name: str, proxy_url: str) -> str:
    if proxy_name != "socks":
        return proxy_url
    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"http", "https"}:
        return proxy_url
    return urlunparse(parsed._replace(scheme="socks5h"))
