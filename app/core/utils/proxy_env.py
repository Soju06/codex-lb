from __future__ import annotations

import urllib.request
from urllib.parse import urlparse

_WEBSOCKET_PROXY_ENV_PRIORITY: dict[str, tuple[str, ...]] = {
    "ws": (
        "ws",
        "https",
        "http",
        "socks",
        "all",
    ),
    "wss": (
        "wss",
        "https",
        "socks",
        "all",
    ),
}

STANDARD_OUTBOUND_PROXY_ENV_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(f"{name}_proxy" for names in _WEBSOCKET_PROXY_ENV_PRIORITY.values() for name in names)
)


def outbound_proxy_env_configured() -> bool:
    proxies = _sanitized_proxy_env()
    return any(name in proxies for name in STANDARD_OUTBOUND_PROXY_ENV_NAMES)


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

    proxies = _sanitized_proxy_env()
    for name in env_names:
        proxy_url = proxies.get(f"{name}_proxy")
        if proxy_url is not None:
            return proxy_url
    return None


def _sanitized_proxy_env() -> dict[str, str]:
    return {
        f"{name.lower()}_proxy": value.strip() for name, value in urllib.request.getproxies().items() if value.strip()
    }
