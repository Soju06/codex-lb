"""Per-account outbound HTTP client registry.

This module mirrors the lifecycle of the global :mod:`app.core.clients.http`
managed client, but keys clients by ``account_id`` so each account that has a
SOCKS5 proxy configured gets its own pooled :class:`aiohttp.ClientSession`
backed by an :class:`aiohttp_socks.ProxyConnector`. Accounts without a proxy
also get a dedicated direct session so account-bound traffic never shares
TCP/TLS connection pools across accounts.

The registry exposes:

- :func:`lease_account_http_client` / :func:`acquire_account_http_client` —
  the primary entry points used by every account-bound outbound call site.
- :func:`lease_account_http_session` / :func:`lease_account_retry_client` —
  thin wrappers analogous to the global helpers in
  :mod:`app.core.clients.http`.
- :func:`invalidate_account_client` — drop the cached client for an account
  (e.g. after the proxy configuration changes or the account is deactivated).
- :func:`close_all_account_clients` — best-effort drain on shutdown; called
  from ``app/main.py`` before :func:`close_http_client`.

The proxy configuration is read through a pluggable
:class:`ProxyConfigProvider`. The default provider (installed lazily on
first use) reads the configuration from ``AccountsRepository`` and decrypts
the password via :class:`TokenEncryptor`. Tests inject a stub provider via
:func:`set_proxy_config_provider` to avoid touching the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Protocol

import aiohttp
from aiohttp_retry import RetryClient
from aiohttp_socks import ProxyConnector, ProxyType

from app.core.clients.account_tls import (
    cached_codex_ssl_context,
)
from app.core.clients.http import HttpClient, _close_client, acquire_http_client
from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AccountProxyConnection:
    """Effective egress proxy parameters used for one account.

    The fingerprint covers everything that influences which ``ProxyConnector``
    to build. If two consecutive lease requests for the same account observe
    different fingerprints, the previously cached managed client is retired
    and a new one is built.
    """

    host: str
    port: int
    username: str | None
    password: str | None  # plaintext, decrypted from storage
    remote_dns: bool


@dataclass(frozen=True, slots=True)
class DirectEgress:
    """Sentinel egress fingerprint: account uses a dedicated direct session.

    Accounts that don't have a SOCKS5 proxy still get their own pooled
    :class:`aiohttp.ClientSession`; they just don't have a
    :class:`ProxyConnector`. The fingerprint type lets the registry
    cache a single direct managed client per account using the same
    lifecycle machinery as the SOCKS5 path.
    """


@dataclass(frozen=True, slots=True)
class EgressContext:
    """Full per-account egress descriptor."""

    proxy: AccountProxyConnection | None


# Discriminated union of the egress shapes the registry caches by.
# ``DirectEgress`` is unit-typed, so two instances compare equal — that
# keeps cache-hit semantics correct on subsequent leases.
EgressFingerprint = AccountProxyConnection | DirectEgress
_DIRECT_EGRESS: DirectEgress = DirectEgress()


class ProxyConfigProvider(Protocol):
    """Plug point for resolving the per-account proxy configuration.

    Implementations MUST return ``None`` from :py:meth:`get` when the
    account does not have a proxy configured (or the account does not
    exist). They MUST return a fully-populated
    :class:`AccountProxyConnection` otherwise (with the password
    already decrypted to plaintext).

    Implementations SHOULD also implement :py:meth:`get_egress` to
    return the egress context in one DB roundtrip. The registry falls
    back to :py:meth:`get` for backwards-compatible test stubs that
    haven't been updated.
    """

    async def get(self, account_id: str) -> AccountProxyConnection | None:  # pragma: no cover - protocol
        ...

    async def get_egress(self, account_id: str) -> EgressContext:  # pragma: no cover - protocol
        ...


@dataclass(slots=True, eq=False)
class _ManagedAccountHttpClient:
    """Lifecycle-tracked per-account managed client.

    Mirrors :class:`app.core.clients.http._ManagedHttpClient`: lease counted,
    retired on configuration change, force-closed on shutdown, with a single
    ``closed`` event used by :func:`close_all_account_clients` to wait for
    the underlying ``aiohttp.ClientSession`` to drain.
    """

    account_id: str
    fingerprint: EgressFingerprint
    client: HttpClient
    active_leases: int = 0
    close_requested: bool = False
    close_task: asyncio.Task[None] | None = None
    closed: asyncio.Event = field(default_factory=asyncio.Event)


_account_clients: dict[str, _ManagedAccountHttpClient] = {}
_account_clients_lock = asyncio.Lock()
_account_retired_clients: list[_ManagedAccountHttpClient] = []
_account_closing_clients: list[_ManagedAccountHttpClient] = []
_proxy_config_provider: ProxyConfigProvider | None = None

# Cache for the WebSocket-side resolved proxy URI. Same trust model as
# the HTTP-side managed-client cache: every site that mutates the
# stored proxy config calls ``invalidate_account_client(account_id)``,
# which clears this entry. The sentinel ``_UNSET`` lets us distinguish
# "never resolved" from "resolved to None (no proxy)" without a second
# membership check.
_UNSET_WS_URI: object = object()
_account_websocket_proxy_uri_cache: dict[str, str | None] = {}
_account_websocket_proxy_uri_lock = asyncio.Lock()


class AccountHttpClientLease:
    """Lease handle for an account-bound outbound HTTP client.

    Wraps either the shared global client for non-account traffic or a
    per-account managed client for account-bound traffic. Either way the
    holder gets the same :class:`HttpClient` shape and closes the lease via
    ``await lease.close()`` (or the ``async with`` helper).
    """

    __slots__ = ("client", "uses_account_proxy", "_releaser", "_released")

    def __init__(
        self,
        client: HttpClient,
        releaser: Callable[[], Awaitable[None]],
        *,
        uses_account_proxy: bool,
    ) -> None:
        self.client = client
        self.uses_account_proxy = uses_account_proxy
        self._releaser = releaser
        self._released = False

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
        if self._released:
            return
        self._released = True
        await self._releaser()


def set_proxy_config_provider(provider: ProxyConfigProvider | None) -> None:
    """Install a custom :class:`ProxyConfigProvider` (for tests / injection).

    Passing ``None`` restores the default provider that reads the
    configuration from the database.
    """

    global _proxy_config_provider
    _proxy_config_provider = provider


def _account_ssl_context() -> ssl.SSLContext:
    """Return the singleton codex SSL context for account-bound traffic."""

    return cached_codex_ssl_context()


def _build_proxy_connector(connection: AccountProxyConnection) -> ProxyConnector:
    return ProxyConnector(
        proxy_type=ProxyType.SOCKS5,
        host=connection.host,
        port=connection.port,
        username=connection.username,
        password=connection.password,
        rdns=connection.remote_dns,
        # SSL context for the upstream-TLS hop. The SOCKS5
        # negotiation itself is plain TCP; this controls the
        # ClientHello sent to the destination after the tunnel opens.
        ssl=_account_ssl_context(),
    )


async def _build_account_http_client(fingerprint: EgressFingerprint) -> HttpClient:
    """Construct an :class:`HttpClient` for one account.

    Dispatches only on the egress shape. ``codex`` is the only active
    account TLS profile, so both direct and SOCKS5 account clients use
    the singleton Codex SSL context.
    """

    if isinstance(fingerprint, DirectEgress):
        return await _build_direct_account_http_client()
    return await _build_socks_account_http_client(fingerprint)


async def _build_socks_account_http_client(connection: AccountProxyConnection) -> HttpClient:
    """Per-account session backed by ``aiohttp_socks.ProxyConnector``."""

    http_connector = _build_proxy_connector(connection)
    session = aiohttp.ClientSession(
        connector=http_connector,
        timeout=aiohttp.ClientTimeout(total=None),
        # An explicit per-account proxy MUST override any HTTP_PROXY /
        # HTTPS_PROXY environment variables — otherwise traffic could leak
        # through the host's default egress.
        trust_env=False,
    )
    try:
        ws_connector = _build_proxy_connector(connection)
        websocket_session = aiohttp.ClientSession(
            connector=ws_connector,
            timeout=aiohttp.ClientTimeout(total=None),
            trust_env=False,
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


async def _build_direct_account_http_client() -> HttpClient:
    """Per-account direct session (no SOCKS5 proxy).

    Uses smaller connector pool limits than the global client because a
    single account does not need 100 connections per host. ``trust_env``
    stays ``True`` to preserve the existing global-client behavior — if
    an operator sets ``HTTP_PROXY`` at the host level they expect direct
    accounts to honor it.

    Every account uses the singleton Codex SSL context.
    """

    settings = get_settings()
    direct_ssl_ctx: ssl.SSLContext | None = _account_ssl_context()
    http_connector = aiohttp.TCPConnector(
        limit=settings.http_connector_limit_per_account_direct,
        limit_per_host=settings.http_connector_limit_per_host_per_account_direct,
        ssl=direct_ssl_ctx,
    )
    session = aiohttp.ClientSession(
        connector=http_connector,
        timeout=aiohttp.ClientTimeout(total=None),
        trust_env=True,
    )
    try:
        ws_connector = aiohttp.TCPConnector(
            limit=settings.http_connector_limit_per_account_direct,
            limit_per_host=settings.http_connector_limit_per_host_per_account_direct,
            ssl=direct_ssl_ctx,
        )
        websocket_session = aiohttp.ClientSession(
            connector=ws_connector,
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


async def _resolve_proxy_config(account_id: str) -> AccountProxyConnection | None:
    provider = _proxy_config_provider
    if provider is None:
        provider = await _install_default_provider()
    return await provider.get(account_id)


async def _resolve_egress(account_id: str) -> EgressContext:
    """Resolve the account's SOCKS5 egress configuration in one shot.

    The provider's ``get_egress`` method is preferred when available
    (the production database-backed provider implements it). Test stubs
    that only implement ``get`` still work by falling back to that
    legacy method.
    """

    provider = _proxy_config_provider
    if provider is None:
        provider = await _install_default_provider()
    get_egress = getattr(provider, "get_egress", None)
    if get_egress is not None:
        # ``get_egress`` is typed to return :class:`EgressContext`, never
        # ``None``. The legacy ``.get()`` fallback below covers test stubs
        # that haven't implemented ``get_egress``.
        return await get_egress(account_id)
    proxy = await provider.get(account_id)
    return EgressContext(proxy=proxy)


async def _install_default_provider() -> ProxyConfigProvider:
    """Lazily install a database-backed default provider.

    Importing the DB-backed provider eagerly creates an import cycle with
    ``app.modules.accounts.repository`` -> ``app.db.session`` -> outbound
    clients. Resolving it on first use breaks that cycle without forcing
    every test to install its own provider.
    """

    global _proxy_config_provider
    if _proxy_config_provider is not None:
        return _proxy_config_provider
    from app.core.clients.account_http_default_provider import (  # noqa: PLC0415
        DatabaseProxyConfigProvider,
    )

    _proxy_config_provider = DatabaseProxyConfigProvider()
    return _proxy_config_provider


def _start_account_client_close_locked(managed: _ManagedAccountHttpClient) -> asyncio.Task[None]:
    if managed.close_task is not None:
        return managed.close_task
    with contextlib.suppress(ValueError):
        _account_retired_clients.remove(managed)
    _account_closing_clients.append(managed)
    task = asyncio.create_task(_close_managed_account_client(managed))
    managed.close_task = task
    task.add_done_callback(lambda completed: _complete_account_client_close(managed, completed))
    return task


async def _close_managed_account_client(managed: _ManagedAccountHttpClient) -> None:
    try:
        await _close_client(managed.client)
    finally:
        managed.closed.set()


def _complete_account_client_close(managed: _ManagedAccountHttpClient, task: asyncio.Task[None]) -> None:
    with contextlib.suppress(ValueError):
        _account_closing_clients.remove(managed)
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Per-account HTTP client close failed account_id=%s", managed.account_id)


def _request_account_client_close_locked(managed: _ManagedAccountHttpClient, *, force: bool = False) -> None:
    managed.close_requested = True
    if managed.active_leases > 0 and not force:
        if managed not in _account_retired_clients:
            _account_retired_clients.append(managed)
        return
    _start_account_client_close_locked(managed)


async def _release_account_client(managed: _ManagedAccountHttpClient) -> None:
    async with _account_clients_lock:
        managed.active_leases -= 1
        if managed.active_leases < 0:
            raise RuntimeError("Account HTTP client lease released too many times")
        if managed.close_requested and managed.active_leases == 0:
            _start_account_client_close_locked(managed)


async def acquire_account_http_client(account_id: str) -> AccountHttpClientLease:
    """Acquire a lease on the appropriate outbound client for ``account_id``.

    Every non-empty ``account_id`` materializes a per-account managed
    client — direct or SOCKS5. The cached client is keyed by an
    :class:`EgressFingerprint`; we trust that any change to the stored
    proxy configuration has already been pushed via
    :func:`invalidate_account_client`, so cache hits MUST NOT re-fetch
    the configuration from the database (every call site that mutates
    the proxy config invalidates the cache before returning).

    ``account_id == ""`` (login bootstrap, release / version check,
    dashboard internals) keeps using the shared global client. That is
    the only "non-account" path; every account-bound call site MUST
    propagate a non-empty ``account_id``.
    """

    if not account_id:
        # Genuinely non-account flows. Keep using the shared global
        # client so we don't hold per-account state for traffic that
        # has no account context.
        return await _acquire_global_passthrough_lease()

    # Cache check before any DB work. The hot path is a request that
    # finds a live managed client, increments the lease counter, and
    # returns — no provider call, no DB session.
    async with _account_clients_lock:
        managed = _account_clients.get(account_id)
        if managed is not None and not managed.close_requested:
            managed.active_leases += 1
            return _build_lease_for_managed(managed)

    # Cache miss: resolve the egress fingerprint and build a managed
    # client. We drop the lock for the resolve because the default
    # provider opens a database session and we don't want to serialize
    # the registry across that latency.
    egress_ctx = await _resolve_egress(account_id)
    fingerprint: EgressFingerprint = egress_ctx.proxy if egress_ctx.proxy is not None else _DIRECT_EGRESS

    async with _account_clients_lock:
        # Re-check inside the lock in case another waiter built it
        # while we were resolving the config.
        managed = _account_clients.get(account_id)
        if managed is not None and managed.fingerprint == fingerprint and not managed.close_requested:
            managed.active_leases += 1
            return _build_lease_for_managed(managed)
        if managed is not None:
            # Defensive: in the rare race where we observed an empty
            # cache, resolved config, and another waiter populated the
            # cache with a *different* fingerprint, retire the loser.
            _request_account_client_close_locked(managed)

        new_client = await _build_account_http_client(fingerprint)
        new_managed = _ManagedAccountHttpClient(
            account_id=account_id,
            fingerprint=fingerprint,
            client=new_client,
        )
        _account_clients[account_id] = new_managed
        new_managed.active_leases += 1
        return _build_lease_for_managed(new_managed)


def _build_lease_for_managed(managed: _ManagedAccountHttpClient) -> AccountHttpClientLease:
    async def _release() -> None:
        await _release_account_client(managed)

    return AccountHttpClientLease(
        managed.client,
        _release,
        uses_account_proxy=isinstance(managed.fingerprint, AccountProxyConnection),
    )


async def _acquire_global_passthrough_lease() -> AccountHttpClientLease:
    global_lease = await acquire_http_client()

    async def _release() -> None:
        await global_lease.close()

    return AccountHttpClientLease(global_lease.client, _release, uses_account_proxy=False)


@contextlib.asynccontextmanager
async def lease_account_http_client(account_id: str) -> AsyncIterator[HttpClient]:
    lease = await acquire_account_http_client(account_id)
    try:
        if lease.uses_account_proxy:
            async with _record_proxy_errors(account_id):
                yield lease.client
        else:
            yield lease.client
    finally:
        await lease.close()


@contextlib.asynccontextmanager
async def lease_account_http_session(
    account_id: str,
    session: aiohttp.ClientSession | None = None,
) -> AsyncIterator[aiohttp.ClientSession]:
    if session is not None:
        async with _record_proxy_errors(account_id):
            yield session
        return
    async with lease_account_http_client(account_id) as client:
        yield client.session


@contextlib.asynccontextmanager
async def lease_account_retry_client(
    account_id: str,
    client: RetryClient | None = None,
) -> AsyncIterator[RetryClient]:
    if client is not None:
        async with _record_proxy_errors(account_id):
            yield client
        return
    async with lease_account_http_client(account_id) as http_client:
        yield http_client.retry_client


@contextlib.asynccontextmanager
async def _record_proxy_errors(account_id: str) -> AsyncIterator[None]:
    """Lazy-imported wrapper around the shared proxy failure tracker.

    The lazy import avoids a circular module dependency between
    :mod:`account_http` and :mod:`account_proxy_failures` (the failure
    tracker calls :func:`invalidate_account_client` from this module on
    deactivation).
    """

    from app.core.clients.account_proxy_failures import (  # noqa: PLC0415
        record_proxy_errors_for_account,
    )

    async with record_proxy_errors_for_account(account_id):
        yield


async def invalidate_account_client(account_id: str) -> None:
    """Drop the cached client for ``account_id`` and retire any in-flight one.

    Callers MUST invoke this whenever the stored proxy configuration for an
    account changes (set / clear / replace) and whenever the account is
    transitioned to a status that should stop traffic (e.g. ``DEACTIVATED``
    by the proxy failure tracker).

    Side effects:
    - Retires the cached managed client (waits for in-flight leases to
      complete).
    - Resets the runtime proxy failure tracker's rolling window for this
      account so a freshly-reconfigured account does not inherit stale
      failure timestamps.
    """

    async with _account_clients_lock:
        managed = _account_clients.pop(account_id, None)
        if managed is not None:
            _request_account_client_close_locked(managed)

    # Drop the WS proxy URI cache entry too — same invalidation contract.
    async with _account_websocket_proxy_uri_lock:
        _account_websocket_proxy_uri_cache.pop(account_id, None)
    # Lazy import to avoid a cycle: ``account_proxy_failures`` imports this
    # module's ``invalidate_account_client`` from its deactivation callback.
    try:
        from app.core.clients.account_proxy_failures import (  # noqa: PLC0415
            get_default_tracker,
        )

        await get_default_tracker().reset_for_account(account_id)
    except Exception:  # pragma: no cover - defensive; failures are non-fatal
        logger.warning("Failed to reset proxy failure tracker for account_id=%s", account_id, exc_info=True)


async def close_all_account_clients() -> None:
    """Force-close every per-account client. Called from global shutdown.

    Active leases are cut short the same way :func:`close_http_client` cuts
    short global leases at shutdown time, since the surrounding shutdown
    sequence has already bounded request drain.
    """

    async with _account_clients_lock:
        managed_list: list[_ManagedAccountHttpClient] = []
        managed_list.extend(_account_clients.values())
        managed_list.extend(_account_retired_clients)
        managed_list.extend(_account_closing_clients)
        _account_clients.clear()
        for managed in managed_list:
            _request_account_client_close_locked(managed, force=True)
    async with _account_websocket_proxy_uri_lock:
        _account_websocket_proxy_uri_cache.clear()
    if managed_list:
        await asyncio.gather(*(managed.closed.wait() for managed in managed_list))


def _running_managed_clients_for_test() -> tuple[_ManagedAccountHttpClient, ...]:
    """Test helper: snapshot of currently cached managed clients."""

    return tuple(_account_clients.values())


async def get_account_websocket_proxy_uri(account_id: str) -> str | None:
    """Return a SOCKS5 proxy URI for the websocket transport, or ``None``.

    The ``websockets`` library accepts a ``proxy=`` URI directly, so we
    convert the stored :class:`AccountProxyConnection` into the URL form
    it expects (``socks5h://`` when ``remote_dns`` is ``True`` so the
    proxy resolves the upstream hostname). Returns ``None`` for accounts
    without a proxy.

    Cached per account; the cache is cleared by
    :func:`invalidate_account_client`. Cache hits never query the
    database.
    """

    if not account_id:
        return None
    if account_id in _account_websocket_proxy_uri_cache:
        return _account_websocket_proxy_uri_cache[account_id]
    async with _account_websocket_proxy_uri_lock:
        if account_id in _account_websocket_proxy_uri_cache:
            return _account_websocket_proxy_uri_cache[account_id]
        config = await _resolve_proxy_config(account_id)
        uri: str | None
        if config is None:
            uri = None
        else:
            scheme = "socks5h" if config.remote_dns else "socks5"
            if config.username:
                from urllib.parse import quote  # noqa: PLC0415

                user = quote(config.username, safe="")
                password = quote(config.password or "", safe="")
                userinfo = f"{user}:{password}@" if password else f"{user}@"
            else:
                userinfo = ""
            uri = f"{scheme}://{userinfo}{config.host}:{int(config.port)}"
        _account_websocket_proxy_uri_cache[account_id] = uri
        return uri
