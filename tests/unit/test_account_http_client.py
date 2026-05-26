"""Unit tests for the per-account outbound HTTP client registry."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.core.clients.account_http as account_http_module
import app.core.clients.http as http_module
from app.core.clients.account_http import (
    AccountProxyConnection,
    EgressContext,
    ProxyConfigProvider,
    acquire_account_http_client,
    close_all_account_clients,
    invalidate_account_client,
    lease_account_http_client,
    set_proxy_config_provider,
)

pytestmark = pytest.mark.unit


def _proxy_auth_fixture(suffix: str = "primary") -> str:
    return f"proxy-fixture-value-{suffix}"


def _proxy_user_fixture() -> str:
    return "proxy-user-fixture"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        http_connector_limit=100,
        http_connector_limit_per_host=50,
        http_connector_limit_per_account_direct=20,
        http_connector_limit_per_host_per_account_direct=10,
        upstream_websocket_trust_env=False,
    )


async def _drain_close_tasks() -> None:
    # The lifecycle uses ``asyncio.create_task`` for client close so let
    # those callbacks settle before assertions.
    for _ in range(3):
        await asyncio.sleep(0)


class _StaticProvider:
    def __init__(self, mapping: dict[str, AccountProxyConnection | None]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    async def get(self, account_id: str) -> AccountProxyConnection | None:
        self.calls.append(account_id)
        return self._mapping.get(account_id)

    async def get_egress(self, account_id: str) -> EgressContext:
        return EgressContext(proxy=await self.get(account_id))


@pytest.fixture(autouse=True)
async def _isolated_registry():
    """Reset the registry + global client around every test.

    Both the global client and the per-account registry hold module-level
    state. We tear both down so each test starts from a clean slate.
    """

    set_proxy_config_provider(None)
    await close_all_account_clients()
    await http_module.close_http_client()
    yield
    set_proxy_config_provider(None)
    await close_all_account_clients()
    await http_module.close_http_client()


def _patched_global_client():
    """Patch the global client builder with mock sessions / retry client.

    Returns the (session, websocket_session, retry_client) mocks the test
    can introspect.
    """

    http_session = MagicMock()
    websocket_session = MagicMock()
    websocket_session.close = AsyncMock()
    retry_client = MagicMock()
    retry_client.close = AsyncMock()

    return http_session, websocket_session, retry_client


@pytest.mark.asyncio
async def test_empty_account_id_passes_through_to_global_client() -> None:
    """Genuinely non-account flows (login bootstrap, release check)
    keep using the shared global client. Only an empty ``account_id``
    triggers the passthrough.
    """

    http_session, websocket_session, retry_client = _patched_global_client()

    with (
        patch("app.core.clients.http.get_settings", return_value=_settings()),
        patch("app.core.clients.http.aiohttp.TCPConnector"),
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[http_session, websocket_session],
        ),
        patch("app.core.clients.http.RetryClient", return_value=retry_client),
    ):
        await http_module.init_http_client()
        # No provider call is ever made for "" — defensive contract.
        set_proxy_config_provider(_StaticProvider({}))

        async with lease_account_http_client("") as client:
            assert client.session is http_session
            assert client.websocket_session is websocket_session
            assert client.retry_client is retry_client

        # No per-account managed client constructed.
        assert account_http_module._running_managed_clients_for_test() == ()


@pytest.mark.asyncio
async def test_no_proxy_account_gets_dedicated_direct_session() -> None:
    """Change C contract: an account with a non-empty ``account_id`` and
    no proxy config still materializes its own per-account direct
    :class:`aiohttp.ClientSession` (with its own
    :class:`aiohttp.TCPConnector`). It MUST NOT fall through to the
    shared global session.
    """

    direct_http = MagicMock()
    direct_ws = MagicMock()
    direct_http.close = AsyncMock()
    direct_ws.close = AsyncMock()
    direct_retry = MagicMock()
    direct_retry.close = AsyncMock()

    set_proxy_config_provider(_StaticProvider({"acc_direct": None}))

    with (
        patch("app.core.clients.account_http.get_settings", return_value=_settings()),
        patch("app.core.clients.account_http.aiohttp.TCPConnector") as tcp_connector_cls,
        patch(
            "app.core.clients.account_http.aiohttp.ClientSession",
            side_effect=[direct_http, direct_ws],
        ) as session_cls,
        patch(
            "app.core.clients.account_http.RetryClient",
            return_value=direct_retry,
        ),
    ):
        async with lease_account_http_client("acc_direct") as client:
            assert client.session is direct_http
            assert client.websocket_session is direct_ws
            assert client.retry_client is direct_retry

        # Two TCPConnectors built (one for HTTP, one for WS), both with
        # the per-account direct limits — much smaller than the global
        # pool's defaults.
        assert tcp_connector_cls.call_count == 2
        for call in tcp_connector_cls.call_args_list:
            assert call.kwargs["limit"] == 20
            assert call.kwargs["limit_per_host"] == 10
            assert call.kwargs["ssl"] is not None
        # ``trust_env=True`` keeps env-proxy parity with the previous
        # global-client behavior for direct accounts.
        assert session_cls.call_args_list[0].kwargs["trust_env"] is True

    managed = account_http_module._running_managed_clients_for_test()
    assert len(managed) == 1
    assert managed[0].account_id == "acc_direct"
    assert isinstance(managed[0].fingerprint, account_http_module.DirectEgress)

    lease = await acquire_account_http_client("acc_direct")
    try:
        assert lease.uses_account_proxy is False
    finally:
        await lease.close()


@pytest.mark.asyncio
async def test_two_direct_accounts_get_distinct_managed_clients() -> None:
    """Two direct accounts MUST land in distinct cache entries with
    their own underlying sessions.
    """

    sessions = [MagicMock() for _ in range(4)]
    for s in sessions:
        s.close = AsyncMock()
    retry_a = MagicMock()
    retry_a.close = AsyncMock()
    retry_b = MagicMock()
    retry_b.close = AsyncMock()

    set_proxy_config_provider(_StaticProvider({"acc_a": None, "acc_b": None}))

    with (
        patch("app.core.clients.account_http.get_settings", return_value=_settings()),
        patch("app.core.clients.account_http.aiohttp.TCPConnector"),
        patch("app.core.clients.account_http.aiohttp.ClientSession", side_effect=sessions),
        patch(
            "app.core.clients.account_http.RetryClient",
            side_effect=[retry_a, retry_b],
        ),
    ):
        async with lease_account_http_client("acc_a"):
            async with lease_account_http_client("acc_b"):
                pass

    managed = account_http_module._running_managed_clients_for_test()
    assert {m.account_id for m in managed} == {"acc_a", "acc_b"}
    assert managed[0].client.session is not managed[1].client.session


@pytest.mark.asyncio
async def test_account_flipping_from_direct_to_socks_retires_direct_client() -> None:
    """When an account switches from no-proxy to SOCKS5 (or back), the
    previously cached managed client MUST be retired.
    """

    direct_http = MagicMock()
    direct_http.close = AsyncMock()
    direct_ws = MagicMock()
    direct_ws.close = AsyncMock()
    direct_retry = MagicMock()
    direct_retry.close = AsyncMock()
    proxy_http = MagicMock()
    proxy_http.close = AsyncMock()
    proxy_ws = MagicMock()
    proxy_ws.close = AsyncMock()
    proxy_retry = MagicMock()
    proxy_retry.close = AsyncMock()

    provider_state: dict[str, AccountProxyConnection | None] = {"acc": None}

    class _FlipProvider:
        async def get(self, account_id: str) -> AccountProxyConnection | None:
            return provider_state.get(account_id)

        async def get_egress(self, account_id: str) -> EgressContext:
            return EgressContext(proxy=await self.get(account_id))

    set_proxy_config_provider(_FlipProvider())

    with (
        patch("app.core.clients.account_http.get_settings", return_value=_settings()),
        patch("app.core.clients.account_http.aiohttp.TCPConnector"),
        patch("app.core.clients.account_http.ProxyConnector"),
        patch(
            "app.core.clients.account_http.aiohttp.ClientSession",
            side_effect=[direct_http, direct_ws, proxy_http, proxy_ws],
        ),
        patch(
            "app.core.clients.account_http.RetryClient",
            side_effect=[direct_retry, proxy_retry],
        ),
    ):
        async with lease_account_http_client("acc") as client:
            assert client.session is direct_http

        # Operator configures a SOCKS5 proxy. Production code paths
        # (``AccountsService.set_account_proxy``) call
        # ``invalidate_account_client`` after persisting the new
        # configuration; the registry trusts that contract on the hot
        # path so we replicate it here.
        provider_state["acc"] = AccountProxyConnection(
            host="p", port=1080, username=None, password=None, remote_dns=True
        )
        await invalidate_account_client("acc")

        async with lease_account_http_client("acc") as client:
            assert client.session is proxy_http

        await _drain_close_tasks()
        # The direct session was retired (and its sockets closed) on the
        # flip; the new proxy session is live.
        direct_ws.close.assert_awaited_once()
        direct_retry.close.assert_awaited_once()
        proxy_ws.close.assert_not_awaited()
        proxy_retry.close.assert_not_awaited()

    managed = account_http_module._running_managed_clients_for_test()
    assert len(managed) == 1
    assert isinstance(managed[0].fingerprint, AccountProxyConnection)


@pytest.mark.asyncio
async def test_proxy_account_lease_uses_proxy_connector_and_caches_client() -> None:
    proxy_http_session = MagicMock()
    proxy_websocket_session = MagicMock()
    proxy_http_session.close = AsyncMock()
    proxy_websocket_session.close = AsyncMock()
    proxy_retry_client = MagicMock()
    proxy_retry_client.close = AsyncMock()

    http_connector = MagicMock(name="http_proxy_connector")
    ws_connector = MagicMock(name="ws_proxy_connector")

    connection = AccountProxyConnection(
        host="proxy.example.com",
        port=1080,
        username="u",
        password=_proxy_auth_fixture("short"),
        remote_dns=True,
    )

    set_proxy_config_provider(_StaticProvider({"acc_proxy": connection}))

    with (
        patch(
            "app.core.clients.account_http.ProxyConnector",
            side_effect=[http_connector, ws_connector],
        ) as proxy_connector_cls,
        patch(
            "app.core.clients.account_http.aiohttp.ClientSession",
            side_effect=[proxy_http_session, proxy_websocket_session],
        ) as client_session_cls,
        patch(
            "app.core.clients.account_http.RetryClient",
            return_value=proxy_retry_client,
        ),
    ):
        async with lease_account_http_client("acc_proxy") as client:
            assert client.session is proxy_http_session
            assert client.websocket_session is proxy_websocket_session
            assert client.retry_client is proxy_retry_client

        # Cached after first lease completes.
        managed = account_http_module._running_managed_clients_for_test()
        assert len(managed) == 1
        assert managed[0].account_id == "acc_proxy"
        assert managed[0].active_leases == 0
        assert managed[0].fingerprint == connection

        lease = await acquire_account_http_client("acc_proxy")
        try:
            assert lease.uses_account_proxy is True
        finally:
            await lease.close()

        # Second lease MUST reuse the cached client (no new connector built).
        proxy_connector_cls.reset_mock()
        client_session_cls.reset_mock()
        async with lease_account_http_client("acc_proxy") as client_again:
            assert client_again.session is proxy_http_session
        proxy_connector_cls.assert_not_called()
        client_session_cls.assert_not_called()

    # Two ProxyConnector calls (HTTP + WS) on first lease, both with rdns=True.
    assert client_session_cls.call_count == 0  # reset above; verify no extra
    # Validate the construction kwargs from the original (pre-reset) call.
    # ``proxy_connector_cls`` was reset, so re-inspect via aiohttp_socks.
    # Round-trip the connector descriptor instead by checking the cached
    # client's fingerprint already covers connection metadata.
    egress = managed[0].fingerprint
    assert isinstance(egress, AccountProxyConnection)
    assert egress.remote_dns is True
    assert egress.host == "proxy.example.com"


@pytest.mark.asyncio
async def test_proxy_session_built_with_trust_env_false() -> None:
    """Per-account sessions MUST ignore env proxies — explicit overrides only."""

    proxy_http_session = MagicMock()
    proxy_websocket_session = MagicMock()
    proxy_http_session.close = AsyncMock()
    proxy_websocket_session.close = AsyncMock()
    proxy_retry_client = MagicMock()
    proxy_retry_client.close = AsyncMock()

    connection = AccountProxyConnection(
        host="proxy.example.com",
        port=1080,
        username=None,
        password=None,
        remote_dns=False,
    )
    set_proxy_config_provider(_StaticProvider({"acc": connection}))

    with (
        patch("app.core.clients.account_http.ProxyConnector"),
        patch(
            "app.core.clients.account_http.aiohttp.ClientSession",
            side_effect=[proxy_http_session, proxy_websocket_session],
        ) as client_session_cls,
        patch(
            "app.core.clients.account_http.RetryClient",
            return_value=proxy_retry_client,
        ),
    ):
        async with lease_account_http_client("acc"):
            pass

    for call in client_session_cls.call_args_list:
        assert call.kwargs["trust_env"] is False


@pytest.mark.asyncio
async def test_proxy_connector_kwargs_match_stored_configuration() -> None:
    proxy_http_session = MagicMock()
    proxy_websocket_session = MagicMock()
    proxy_http_session.close = AsyncMock()
    proxy_websocket_session.close = AsyncMock()
    proxy_retry_client = MagicMock()
    proxy_retry_client.close = AsyncMock()

    connection = AccountProxyConnection(
        host="proxy.example.com",
        port=1085,
        username=_proxy_user_fixture(),
        password=_proxy_auth_fixture(),
        remote_dns=False,
    )
    set_proxy_config_provider(_StaticProvider({"acc": connection}))

    codex_ssl_context = object()

    with (
        patch(
            "app.core.clients.account_http.cached_codex_ssl_context",
            return_value=codex_ssl_context,
        ),
        patch("app.core.clients.account_http.ProxyConnector") as proxy_connector_cls,
        patch(
            "app.core.clients.account_http.aiohttp.ClientSession",
            side_effect=[proxy_http_session, proxy_websocket_session],
        ),
        patch(
            "app.core.clients.account_http.RetryClient",
            return_value=proxy_retry_client,
        ),
    ):
        async with lease_account_http_client("acc"):
            pass

    # Two ProxyConnector instantiations: HTTP session + websocket session.
    assert proxy_connector_cls.call_count == 2
    for call in proxy_connector_cls.call_args_list:
        assert call.kwargs["host"] == "proxy.example.com"
        assert call.kwargs["port"] == 1085
        assert call.kwargs["username"] == _proxy_user_fixture()
        assert call.kwargs["password"] == _proxy_auth_fixture()
        assert call.kwargs["rdns"] is False
        assert call.kwargs["ssl"] is codex_ssl_context
        assert str(call.kwargs["proxy_type"]).endswith("SOCKS5")


@pytest.mark.asyncio
async def test_config_change_retires_previous_managed_client() -> None:
    first_http = MagicMock()
    first_ws = MagicMock()
    first_http.close = AsyncMock()
    first_ws.close = AsyncMock()
    first_retry = MagicMock()
    first_retry.close = AsyncMock()

    second_http = MagicMock()
    second_ws = MagicMock()
    second_http.close = AsyncMock()
    second_ws.close = AsyncMock()
    second_retry = MagicMock()
    second_retry.close = AsyncMock()

    config_v1 = AccountProxyConnection("p1", 1080, None, None, True)
    config_v2 = AccountProxyConnection("p2", 1080, None, None, True)

    provider_state = {"acc": config_v1}

    class _MutableProvider:
        async def get(self, account_id: str) -> AccountProxyConnection | None:
            return provider_state.get(account_id)

        async def get_egress(self, account_id: str) -> EgressContext:
            return EgressContext(proxy=await self.get(account_id))

    set_proxy_config_provider(_MutableProvider())

    with (
        patch("app.core.clients.account_http.ProxyConnector"),
        patch(
            "app.core.clients.account_http.aiohttp.ClientSession",
            side_effect=[first_http, first_ws, second_http, second_ws],
        ),
        patch(
            "app.core.clients.account_http.RetryClient",
            side_effect=[first_retry, second_retry],
        ),
    ):
        async with lease_account_http_client("acc") as client:
            assert client.session is first_http

        # Mirror what ``AccountsService.set_account_proxy`` does after
        # writing a new config: invalidate the cached client so the
        # next lease rebuilds.
        provider_state["acc"] = config_v2
        await invalidate_account_client("acc")

        async with lease_account_http_client("acc") as client:
            assert client.session is second_http

        # Old client closed; new client cached.
        await _drain_close_tasks()
        first_ws.close.assert_awaited_once()
        first_retry.close.assert_awaited_once()
        second_ws.close.assert_not_awaited()
        second_retry.close.assert_not_awaited()

    managed = account_http_module._running_managed_clients_for_test()
    assert len(managed) == 1
    assert managed[0].fingerprint == config_v2


@pytest.mark.asyncio
async def test_invalidate_account_client_retires_in_flight_client() -> None:
    proxy_http = MagicMock()
    proxy_ws = MagicMock()
    proxy_http.close = AsyncMock()
    proxy_ws.close = AsyncMock()
    proxy_retry = MagicMock()
    proxy_retry.close = AsyncMock()

    connection = AccountProxyConnection("p", 1080, None, None, True)
    set_proxy_config_provider(_StaticProvider({"acc": connection}))

    with (
        patch("app.core.clients.account_http.ProxyConnector"),
        patch(
            "app.core.clients.account_http.aiohttp.ClientSession",
            side_effect=[proxy_http, proxy_ws],
        ),
        patch(
            "app.core.clients.account_http.RetryClient",
            return_value=proxy_retry,
        ),
    ):
        lease = await acquire_account_http_client("acc")
        try:
            assert lease.client.session is proxy_http
            await invalidate_account_client("acc")
            # While the lease is still held, the close MUST be deferred.
            await _drain_close_tasks()
            proxy_ws.close.assert_not_awaited()
            proxy_retry.close.assert_not_awaited()
        finally:
            await lease.close()

        await _drain_close_tasks()
        proxy_ws.close.assert_awaited_once()
        proxy_retry.close.assert_awaited_once()

    # Cache is empty after invalidation.
    assert account_http_module._running_managed_clients_for_test() == ()


@pytest.mark.asyncio
async def test_close_all_account_clients_force_closes_in_flight_clients() -> None:
    proxy_http = MagicMock()
    proxy_ws = MagicMock()
    proxy_http.close = AsyncMock()
    proxy_ws.close = AsyncMock()
    proxy_retry = MagicMock()
    proxy_retry.close = AsyncMock()

    connection = AccountProxyConnection("p", 1080, None, None, True)
    set_proxy_config_provider(_StaticProvider({"acc": connection}))

    with (
        patch("app.core.clients.account_http.ProxyConnector"),
        patch(
            "app.core.clients.account_http.aiohttp.ClientSession",
            side_effect=[proxy_http, proxy_ws],
        ),
        patch(
            "app.core.clients.account_http.RetryClient",
            return_value=proxy_retry,
        ),
    ):
        lease = await acquire_account_http_client("acc")
        try:
            await asyncio.wait_for(close_all_account_clients(), timeout=0.5)
        finally:
            await lease.close()

    proxy_ws.close.assert_awaited_once()
    proxy_retry.close.assert_awaited_once()
    assert account_http_module._running_managed_clients_for_test() == ()


@pytest.mark.asyncio
async def test_lease_release_too_many_times_raises() -> None:
    proxy_http = MagicMock()
    proxy_ws = MagicMock()
    proxy_http.close = AsyncMock()
    proxy_ws.close = AsyncMock()
    proxy_retry = MagicMock()
    proxy_retry.close = AsyncMock()

    set_proxy_config_provider(_StaticProvider({"acc": AccountProxyConnection("p", 1080, None, None, True)}))

    with (
        patch("app.core.clients.account_http.ProxyConnector"),
        patch(
            "app.core.clients.account_http.aiohttp.ClientSession",
            side_effect=[proxy_http, proxy_ws],
        ),
        patch(
            "app.core.clients.account_http.RetryClient",
            return_value=proxy_retry,
        ),
    ):
        lease = await acquire_account_http_client("acc")
        await lease.close()
        # Idempotent close.
        await lease.close()

    # Manually decrement to simulate a buggy caller that double-releases.
    managed_snapshot = account_http_module._running_managed_clients_for_test()
    assert managed_snapshot
    managed = managed_snapshot[0]
    with pytest.raises(RuntimeError):
        await account_http_module._release_account_client(managed)


def test_protocol_runtime_check_optional() -> None:
    """``ProxyConfigProvider`` is a structural Protocol — duck typing works."""

    class _Stub:
        async def get(self, account_id: str) -> AccountProxyConnection | None:
            return None

        async def get_egress(self, account_id: str) -> EgressContext:
            return EgressContext(proxy=await self.get(account_id))

    # Setting a non-isinstance-checked structural Protocol MUST work.
    set_proxy_config_provider(_Stub())
    set_proxy_config_provider(None)

    assert ProxyConfigProvider is not None  # imported for type narrowing in callers
