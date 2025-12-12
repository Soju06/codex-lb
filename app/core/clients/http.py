from __future__ import annotations

from dataclasses import dataclass

import aiohttp
from aiohttp_retry import RetryClient


@dataclass(slots=True)
class HttpClient:
    session: aiohttp.ClientSession
    retry_client: RetryClient


_http_client: HttpClient | None = None


async def init_http_client() -> HttpClient:
    global _http_client
    if _http_client is not None:
        return _http_client
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))
    retry_client = RetryClient(client_session=session, raise_for_status=False)
    _http_client = HttpClient(session=session, retry_client=retry_client)
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is None:
        return
    await _http_client.retry_client.close()
    _http_client = None


def get_http_client() -> HttpClient:
    if _http_client is None:
        raise RuntimeError("HTTP client not initialized")
    return _http_client
