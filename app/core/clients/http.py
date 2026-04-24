from __future__ import annotations

import asyncio
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
_http_client_lock = asyncio.Lock()


async def _build_http_client() -> HttpClient:
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
    return HttpClient(
        session=session,
        websocket_session=websocket_session,
        retry_client=retry_client,
    )


async def _close_client(client: HttpClient) -> None:
    try:
        await client.websocket_session.close()
    finally:
        await client.retry_client.close()


async def init_http_client() -> HttpClient:
    global _http_client
    async with _http_client_lock:
        if _http_client is not None:
            return _http_client
        _http_client = await _build_http_client()
        return _http_client


async def refresh_http_client() -> HttpClient:
    global _http_client
    async with _http_client_lock:
        previous = _http_client
        replacement = await _build_http_client()
        _http_client = replacement
    if previous is not None:
        await _close_client(previous)
    return replacement


async def close_http_client() -> None:
    global _http_client
    async with _http_client_lock:
        client = _http_client
        _http_client = None
    if client is not None:
        await _close_client(client)


def get_http_client() -> HttpClient:
    if _http_client is None:
        raise RuntimeError("HTTP client not initialized")
    return _http_client
