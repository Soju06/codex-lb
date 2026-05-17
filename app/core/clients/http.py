from __future__ import annotations

from dataclasses import dataclass

import aiohttp
from aiohttp_retry import RetryClient

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
    connector = aiohttp.TCPConnector(
        limit=settings.http_connector_limit,
        limit_per_host=settings.http_connector_limit_per_host,
    )
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=None),
        trust_env=True,
    )
    try:
        # Match direct Codex transport unless operators explicitly override or
        # standard outbound proxy env vars are present.
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
