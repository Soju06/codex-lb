from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import ssl
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from types import TracebackType

import aiohttp
import certifi
from aiohttp_retry import RetryClient
from aiohttp_socks import ProxyConnector

from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HttpClient:
    session: aiohttp.ClientSession
    websocket_session: aiohttp.ClientSession
    retry_client: RetryClient


@dataclass(frozen=True, slots=True)
class _SocksProxyConfig:
    connector_url: str
    rdns: bool | None = None


@dataclass(slots=True, eq=False)
class _ManagedHttpClient:
    client: HttpClient
    active_leases: int = 0
    close_requested: bool = False
    close_task: asyncio.Task[None] | None = None
    closed: asyncio.Event = field(default_factory=asyncio.Event)


_http_client: _ManagedHttpClient | None = None
_http_client_lock = asyncio.Lock()
_retired_http_clients: list[_ManagedHttpClient] = []
_closing_http_clients: list[_ManagedHttpClient] = []


def _socks_proxy_config(environ: Mapping[str, str | None] = os.environ) -> _SocksProxyConfig | None:
    request_method_set = bool(environ.get("REQUEST_METHOD"))
    for var in (
        "SOCKS_PROXY",
        "socks_proxy",
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        if request_method_set and var in ("HTTP_PROXY", "http_proxy"):
            continue
        val = (environ.get(var) or "").strip()
        lowered = val.lower()
        if var in ("SOCKS_PROXY", "socks_proxy") and lowered.startswith("http://"):
            val = f"socks5://{val.split('://', 1)[1]}"
            lowered = val.lower()
        if lowered.startswith(("socks5://", "socks5h://", "socks4://", "socks4a://")):
            if lowered.startswith("socks5h://"):
                return _SocksProxyConfig(
                    connector_url="socks5://" + val[len("socks5h://") :],
                    rdns=True,
                )
            elif lowered.startswith("socks4a://"):
                return _SocksProxyConfig(
                    connector_url="socks4://" + val[len("socks4a://") :],
                    rdns=True,
                )
            return _SocksProxyConfig(connector_url=val)
    return None


def _socks_proxy_url(environ: Mapping[str, str | None] = os.environ) -> str | None:
    config = _socks_proxy_config(environ)
    return config.connector_url if config else None


def _build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


class HttpClientLease:
    def __init__(self, managed_client: _ManagedHttpClient) -> None:
        self.client = managed_client.client
        self._managed_client = managed_client
        self._closed = False

    async def __aenter__(self) -> HttpClient:
        return self.client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _release_http_client(self._managed_client)


async def _build_http_client() -> HttpClient:
    settings = get_settings()
    ssl_context = _build_ssl_context()
    proxy_env = (
        settings.upstream_websocket_proxy_env() if hasattr(settings, "upstream_websocket_proxy_env") else os.environ
    )
    socks_config = _socks_proxy_config(proxy_env)
    if socks_config:
        connector = ProxyConnector.from_url(
            socks_config.connector_url,
            limit=settings.http_connector_limit,
            limit_per_host=settings.http_connector_limit_per_host,
            ssl=ssl_context,
            rdns=socks_config.rdns,
        )
    else:
        connector = aiohttp.TCPConnector(
            limit=settings.http_connector_limit,
            limit_per_host=settings.http_connector_limit_per_host,
            ssl=ssl_context,
        )
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=None),
        trust_env=not socks_config,
    )
    try:
        if socks_config and settings.upstream_websocket_trust_env:
            ws_connector: aiohttp.TCPConnector | ProxyConnector = ProxyConnector.from_url(
                socks_config.connector_url,
                ssl=ssl_context,
                rdns=socks_config.rdns,
            )
            ws_trust_env = False
        else:
            ws_connector = aiohttp.TCPConnector(ssl=ssl_context)
            ws_trust_env = settings.upstream_websocket_trust_env
        try:
            websocket_session = aiohttp.ClientSession(
                connector=ws_connector,
                timeout=aiohttp.ClientTimeout(total=None),
                trust_env=ws_trust_env,
            )
        except Exception:
            await ws_connector.close()
            raise
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


async def _close_managed_client(managed_client: _ManagedHttpClient) -> None:
    try:
        await _close_client(managed_client.client)
    finally:
        managed_client.closed.set()


def _complete_managed_client_close(managed_client: _ManagedHttpClient, task: asyncio.Task[None]) -> None:
    with contextlib.suppress(ValueError):
        _closing_http_clients.remove(managed_client)
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("HTTP client close failed")


def _start_client_close_locked(managed_client: _ManagedHttpClient) -> asyncio.Task[None]:
    if managed_client.close_task is not None:
        return managed_client.close_task
    with contextlib.suppress(ValueError):
        _retired_http_clients.remove(managed_client)
    _closing_http_clients.append(managed_client)
    task = asyncio.create_task(_close_managed_client(managed_client))
    managed_client.close_task = task
    task.add_done_callback(lambda completed_task: _complete_managed_client_close(managed_client, completed_task))
    return task


def _request_client_close_locked(managed_client: _ManagedHttpClient, *, force: bool = False) -> None:
    managed_client.close_requested = True
    if managed_client.active_leases > 0 and not force:
        if managed_client not in _retired_http_clients:
            _retired_http_clients.append(managed_client)
        return
    _start_client_close_locked(managed_client)


async def _release_http_client(managed_client: _ManagedHttpClient) -> None:
    async with _http_client_lock:
        managed_client.active_leases -= 1
        if managed_client.active_leases < 0:
            raise RuntimeError("HTTP client lease released too many times")
        if managed_client.close_requested and managed_client.active_leases == 0:
            _start_client_close_locked(managed_client)


async def acquire_http_client() -> HttpClientLease:
    async with _http_client_lock:
        if _http_client is None:
            raise RuntimeError("HTTP client not initialized")
        _http_client.active_leases += 1
        return HttpClientLease(_http_client)


@contextlib.asynccontextmanager
async def lease_http_client() -> AsyncIterator[HttpClient]:
    lease = await acquire_http_client()
    try:
        yield lease.client
    finally:
        await lease.close()


@contextlib.asynccontextmanager
async def lease_http_session(
    session: aiohttp.ClientSession | None = None,
) -> AsyncIterator[aiohttp.ClientSession]:
    if session is not None:
        yield session
        return
    async with lease_http_client() as client:
        yield client.session


@contextlib.asynccontextmanager
async def lease_retry_client(
    client: RetryClient | None = None,
) -> AsyncIterator[RetryClient]:
    if client is not None:
        yield client
        return
    async with lease_http_client() as http_client:
        yield http_client.retry_client


async def init_http_client() -> HttpClient:
    global _http_client
    async with _http_client_lock:
        if _http_client is not None:
            return _http_client.client
        client = await _build_http_client()
        _http_client = _ManagedHttpClient(client=client)
        return client


async def refresh_http_client() -> HttpClient:
    global _http_client
    async with _http_client_lock:
        previous = _http_client
        replacement_client = await _build_http_client()
        replacement = _ManagedHttpClient(client=replacement_client)
        _http_client = replacement
        if previous is not None:
            _request_client_close_locked(previous)
    return replacement_client


async def close_http_client() -> None:
    global _http_client
    async with _http_client_lock:
        client = _http_client
        _http_client = None
        clients = (
            *((client,) if client is not None else ()),
            *_retired_http_clients,
            *_closing_http_clients,
        )
        for managed_client in clients:
            # Global shutdown has already bounded request drain; do not let
            # long-lived streams keep process shutdown waiting on active leases.
            _request_client_close_locked(managed_client, force=True)
    if clients:
        await asyncio.gather(*(managed_client.closed.wait() for managed_client in clients))


def get_http_client() -> HttpClient:
    """Return the current client for compatibility; network use should lease it."""
    if _http_client is None:
        raise RuntimeError("HTTP client not initialized")
    return _http_client.client
