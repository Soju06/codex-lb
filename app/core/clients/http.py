from __future__ import annotations

from dataclasses import dataclass

import aiohttp
from aiohttp_retry import RetryClient

from app.core.config.proxy import normalize_http_proxy_url
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.config.settings import get_settings

@dataclass(slots=True)
class HttpClient:
    session: aiohttp.ClientSession
    websocket_session: aiohttp.ClientSession
    retry_client: RetryClient


_http_client: HttpClient | None = None


async def init_http_client() -> HttpClient:
    global _http_client
    if _http_client is not None:
        return _http_client
    settings = get_settings()
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None), trust_env=True)
    try:
        # Match Codex CLI's direct websocket transport by avoiding env proxies unless operators
        # explicitly opt in for websocket handshakes.
        websocket_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None),
            trust_env=settings.upstream_websocket_trust_env,
        )
    except Exception:
        await session.close()
        raise
    retry_client = RetryClient(client_session=session, raise_for_status=False)
    _http_client = HttpClient(
        session=session,
        websocket_session=websocket_session,
        retry_client=retry_client,
    )
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is None:
        return
    client = _http_client
    try:
        await client.websocket_session.close()
    finally:
        await client.retry_client.close()
    _http_client = None


def get_http_client() -> HttpClient:
    if _http_client is None:
        raise RuntimeError("HTTP client not initialized")
    return _http_client


async def get_http_proxy_url() -> str | None:
    env_proxy = normalize_http_proxy_url(get_settings().http_proxy_url)
    if env_proxy:
        return env_proxy

    try:
        settings_row = await get_settings_cache().get()
    except Exception:
        return None
    return normalize_http_proxy_url(getattr(settings_row, "http_proxy_url", None))


async def get_http_proxy_request_kwargs() -> dict[str, str]:
    proxy = await get_http_proxy_url()
    if not proxy:
        return {}
    return {"proxy": proxy}
