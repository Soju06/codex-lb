from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import socket
import ssl
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from types import TracebackType

import aiohttp
import certifi
from aiohappyeyeballs.types import AddrInfoType
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
_last_generationless_network_rotation_at: float | None = None
_GENERATIONLESS_NETWORK_ROTATION_COOLDOWN_SECONDS = 1.0

# Pooled upstream connections outlive the request that opened them, so a socket
# dropped by an intermediary (NAT rebind, tunnel reconnect, route change) is
# otherwise only discovered when an application-level timeout fires. Probes turn
# that silent black hole into a transport error the failover paths already
# handle. Idle/interval/count are chosen to declare a dead peer in ~90s, which
# matches the pooled keepalive window below.
_TCP_KEEPALIVE_IDLE_SECONDS = 30
_TCP_KEEPALIVE_INTERVAL_SECONDS = 10
_TCP_KEEPALIVE_PROBE_COUNT = 6


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


@functools.cache
def _shared_ssl_context() -> ssl.SSLContext:
    """Return the process-wide verification context shared by every outbound connector.

    Building a context parses the system store plus the certifi bundle
    (~7 ms CPU and ~700 KB per copy), and nothing mutates the context after
    construction, so per-call sessions, SOCKS connectors, and the shared
    client generations all reuse this one instance. ``_build_ssl_context`` is
    looked up at call time so tests can still patch the constructor; the
    cache is cleared by ``_reset_shared_ssl_context``.
    """

    return _build_ssl_context()


def _reset_shared_ssl_context() -> None:
    _shared_ssl_context.cache_clear()


def _apply_tcp_keepalive(sock: socket.socket) -> None:
    """Enable OS keepalive probes on an upstream socket.

    Probe tuning is best-effort by design: ``TCP_KEEPIDLE`` is Linux-only,
    macOS spells the same knob ``TCP_KEEPALIVE``, and other platforms may
    expose neither. Failing client construction over a missing socket option
    would trade a rare hang for a certain outage, so unsupported knobs are
    skipped and only the enable step is required.
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        logger.debug("Upstream socket rejected SO_KEEPALIVE", exc_info=True)
        return
    for option_name, option_value in (
        ("TCP_KEEPIDLE", _TCP_KEEPALIVE_IDLE_SECONDS),
        ("TCP_KEEPALIVE", _TCP_KEEPALIVE_IDLE_SECONDS),
        ("TCP_KEEPINTVL", _TCP_KEEPALIVE_INTERVAL_SECONDS),
        ("TCP_KEEPCNT", _TCP_KEEPALIVE_PROBE_COUNT),
    ):
        option = getattr(socket, option_name, None)
        if option is None:
            continue
        try:
            sock.setsockopt(socket.IPPROTO_TCP, option, option_value)
        except OSError:
            logger.debug("Upstream socket rejected %s", option_name, exc_info=True)


def _keepalive_socket_factory(addr_info: AddrInfoType) -> socket.socket:
    family, socket_type, proto = addr_info[0], addr_info[1], addr_info[2]
    sock = socket.socket(family=family, type=socket_type, proto=proto)
    try:
        _apply_tcp_keepalive(sock)
    except BaseException:
        sock.close()
        raise
    return sock


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
    ssl_context = _shared_ssl_context()
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
            socket_factory=_keepalive_socket_factory,
        )
    else:
        connector = aiohttp.TCPConnector(
            limit=settings.http_connector_limit,
            limit_per_host=settings.http_connector_limit_per_host,
            ssl=ssl_context,
            # aiohttp defaults (15s keepalive, 10s DNS TTL) are shorter than
            # typical interactive Codex turn gaps, so nearly every turn paid a
            # fresh DNS lookup + TCP/TLS handshake to the upstream host
            # (~100-300ms of TTFT). Keep idle connections and resolved names
            # around across turns instead.
            keepalive_timeout=90,
            ttl_dns_cache=300,
            socket_factory=_keepalive_socket_factory,
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
                socket_factory=_keepalive_socket_factory,
            )
            ws_trust_env = False
        else:
            ws_connector = aiohttp.TCPConnector(
                ssl=ssl_context,
                keepalive_timeout=90,
                ttl_dns_cache=300,
                socket_factory=_keepalive_socket_factory,
            )
            ws_trust_env = settings.upstream_websocket_trust_env
        try:
            websocket_session = aiohttp.ClientSession(
                connector=ws_connector,
                timeout=aiohttp.ClientTimeout(total=None),
                trust_env=ws_trust_env,
            )
        except BaseException:
            await asyncio.shield(ws_connector.close())
            raise
    except BaseException:
        await asyncio.shield(session.close())
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


async def refresh_http_client_after_network_failure(
    *,
    failed_session: aiohttp.ClientSession | None = None,
) -> str:
    """Rotate stale shared transport state once for a failed client generation."""

    global _http_client, _last_generationless_network_rotation_at
    async with _http_client_lock:
        current = _http_client
        if current is None:
            return "not_initialized"
        if (
            failed_session is not None
            and failed_session is not current.client.session
            and failed_session is not current.client.websocket_session
        ):
            return "already_rotated"
        now = time.monotonic()
        if (
            failed_session is None
            and _last_generationless_network_rotation_at is not None
            and now - _last_generationless_network_rotation_at < _GENERATIONLESS_NETWORK_ROTATION_COOLDOWN_SECONDS
        ):
            return "coalesced"
        replacement_client = await _build_http_client()
        replacement = _ManagedHttpClient(client=replacement_client)
        _http_client = replacement
        _last_generationless_network_rotation_at = now
        _request_client_close_locked(current)
        return "rotated"


async def close_http_client() -> None:
    global _http_client, _last_generationless_network_rotation_at
    async with _http_client_lock:
        client = _http_client
        _http_client = None
        _last_generationless_network_rotation_at = None
        _reset_shared_ssl_context()
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
