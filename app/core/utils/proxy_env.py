from __future__ import annotations

import os
import urllib.request
from urllib.parse import urlparse

_WEBSOCKET_PROXY_ENV_PRIORITY: dict[str, tuple[str, ...]] = {
    "ws": (
        "ws_proxy",
        "WS_PROXY",
        "http_proxy",
        "HTTP_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ),
    "wss": (
        "wss_proxy",
        "WSS_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ),
}

STANDARD_OUTBOUND_PROXY_ENV_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(name for names in _WEBSOCKET_PROXY_ENV_PRIORITY.values() for name in names)
)


def outbound_proxy_env_configured() -> bool:
    return any(_read_proxy_env(name) is not None for name in STANDARD_OUTBOUND_PROXY_ENV_NAMES)


def resolve_websocket_proxy_from_env(url: str) -> str | None:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    env_names = _WEBSOCKET_PROXY_ENV_PRIORITY.get(scheme)
    if env_names is None:
        return None

    hostname = parsed.hostname
    port = parsed.port or (443 if scheme == "wss" else 80)
    if hostname and urllib.request.proxy_bypass(f"{hostname}:{port}"):
        return None

    for name in env_names:
        proxy_url = _read_proxy_env(name)
        if proxy_url is not None:
            return proxy_url
    return None


def _read_proxy_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
