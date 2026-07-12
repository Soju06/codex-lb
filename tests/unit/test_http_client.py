from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.core.clients.http as http_module

pytestmark = pytest.mark.unit


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        http_connector_limit=100,
        http_connector_limit_per_host=50,
        upstream_websocket_trust_env=False,
    )


async def _drain_close_tasks() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_init_http_client_uses_separate_http_and_websocket_sessions() -> None:
    await http_module.close_http_client()

    http_session = MagicMock()
    websocket_session = MagicMock()
    websocket_session.close = AsyncMock()
    retry_client = MagicMock()
    retry_client.close = AsyncMock()

    with (
        patch("app.core.clients.http.get_settings", return_value=_settings()),
        patch("app.core.clients.http.aiohttp.TCPConnector"),
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[http_session, websocket_session],
        ) as client_session_cls,
        patch("app.core.clients.http.RetryClient", return_value=retry_client) as retry_client_cls,
    ):
        client = await http_module.init_http_client()

    assert client.session is http_session
    assert client.websocket_session is websocket_session
    assert client.retry_client is retry_client
    assert client_session_cls.call_args_list[0].kwargs["trust_env"] is True
    assert client_session_cls.call_args_list[1].kwargs["trust_env"] is False
    retry_client_cls.assert_called_once_with(client_session=http_session, raise_for_status=False)

    await http_module.close_http_client()


@pytest.mark.asyncio
async def test_init_http_client_creates_tcp_connector_with_limits() -> None:
    await http_module.close_http_client()

    http_session = MagicMock()
    websocket_session = MagicMock()
    websocket_session.close = AsyncMock()
    retry_client = MagicMock()
    retry_client.close = AsyncMock()
    connector = MagicMock()
    websocket_connector = MagicMock()
    ssl_context = MagicMock()

    with (
        patch("app.core.clients.http.get_settings", return_value=_settings()),
        patch("app.core.clients.http._build_ssl_context", return_value=ssl_context) as ssl_context_factory,
        patch(
            "app.core.clients.http.aiohttp.TCPConnector",
            side_effect=[connector, websocket_connector],
        ) as tcp_connector_cls,
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[http_session, websocket_session],
        ) as client_session_cls,
        patch("app.core.clients.http.RetryClient", return_value=retry_client),
    ):
        await http_module.init_http_client()

    assert ssl_context_factory.call_count == 1
    assert tcp_connector_cls.call_args_list[0].kwargs == {
        "limit": 100,
        "limit_per_host": 50,
        "ssl": ssl_context,
        "keepalive_timeout": 90,
        "ttl_dns_cache": 300,
    }
    assert tcp_connector_cls.call_args_list[1].kwargs == {
        "ssl": ssl_context,
        "keepalive_timeout": 90,
        "ttl_dns_cache": 300,
    }
    assert client_session_cls.call_args_list[0].kwargs["connector"] is connector
    assert client_session_cls.call_args_list[1].kwargs["connector"] is websocket_connector

    await http_module.close_http_client()


def test_socks_proxy_url_detects_lowercase_socks_proxy() -> None:
    with patch.dict("os.environ", {"socks_proxy": "socks5://proxy.example.com:1080"}, clear=True):
        url = http_module._socks_proxy_url()
    assert url == "socks5://proxy.example.com:1080"


def test_socks_proxy_url_strips_whitespace_from_env_value() -> None:
    with patch.dict("os.environ", {"SOCKS_PROXY": "  socks5://proxy.example.com:1080  "}, clear=True):
        url = http_module._socks_proxy_url()
    assert url == "socks5://proxy.example.com:1080"


def test_socks_proxy_url_normalizes_http_scheme_for_socks_proxy_env() -> None:
    with patch.dict("os.environ", {"socks_proxy": "http://proxy.example.com:1080"}, clear=True):
        url = http_module._socks_proxy_url()
    assert url == "socks5://proxy.example.com:1080"


def test_socks_proxy_url_normalizes_socks5h_for_proxy_connector() -> None:
    with patch.dict("os.environ", {"SOCKS_PROXY": "socks5h://proxy.example.com:1080"}, clear=True):
        url = http_module._socks_proxy_url()
    assert url == "socks5://proxy.example.com:1080"


def test_socks_proxy_url_normalizes_socks4a_for_proxy_connector() -> None:
    with patch.dict("os.environ", {"SOCKS_PROXY": "socks4a://proxy.example.com:1080"}, clear=True):
        url = http_module._socks_proxy_url()
    assert url == "socks4://proxy.example.com:1080"


def test_socks_proxy_config_preserves_socks4a_remote_dns() -> None:
    config = http_module._socks_proxy_config({"SOCKS_PROXY": "socks4a://proxy.example.com:1080"})
    assert config is not None
    assert config.connector_url == "socks4://proxy.example.com:1080"
    assert config.rdns is True


def test_socks_proxy_url_skips_http_proxy_when_request_method_set() -> None:
    env = {"REQUEST_METHOD": "GET", "HTTP_PROXY": "socks5://proxy.example.com:1080"}
    with patch.dict("os.environ", env, clear=True):
        url = http_module._socks_proxy_url()
    assert url is None


@pytest.mark.asyncio
async def test_init_http_client_uses_proxy_connector_for_socks_url() -> None:
    await http_module.close_http_client()

    http_session = MagicMock()
    websocket_session = MagicMock()
    websocket_session.close = AsyncMock()
    retry_client = MagicMock()
    retry_client.close = AsyncMock()
    proxy_connector = MagicMock()
    ssl_context = MagicMock()
    socks_settings = SimpleNamespace(
        http_connector_limit=100,
        http_connector_limit_per_host=50,
        upstream_websocket_trust_env=True,
    )

    with (
        patch("app.core.clients.http.get_settings", return_value=socks_settings),
        patch("app.core.clients.http._build_ssl_context", return_value=ssl_context),
        patch("app.core.clients.http.ProxyConnector") as proxy_connector_cls,
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[http_session, websocket_session],
        ) as client_session_cls,
        patch("app.core.clients.http.RetryClient", return_value=retry_client),
        patch.dict("os.environ", {"socks_proxy": "http://proxy.example.com:1080"}, clear=True),
    ):
        ws_proxy_connector = MagicMock()
        proxy_connector_cls.from_url.side_effect = [proxy_connector, ws_proxy_connector]
        client = await http_module.init_http_client()

    assert client.session is http_session
    assert client.websocket_session is websocket_session
    assert proxy_connector_cls.from_url.call_count == 2
    assert [call.args[0] for call in proxy_connector_cls.from_url.call_args_list] == [
        "socks5://proxy.example.com:1080",
        "socks5://proxy.example.com:1080",
    ]
    assert client_session_cls.call_args_list[0].kwargs["connector"] is proxy_connector
    assert client_session_cls.call_args_list[1].kwargs["connector"] is ws_proxy_connector
    # trust_env must be False for both sessions when SOCKS proxy is active (avoids double-proxying)
    assert client_session_cls.call_args_list[0].kwargs["trust_env"] is False
    assert client_session_cls.call_args_list[1].kwargs["trust_env"] is False

    await http_module.close_http_client()


@pytest.mark.asyncio
async def test_init_http_client_uses_settings_proxy_env_for_socks_url() -> None:
    await http_module.close_http_client()

    http_session = MagicMock()
    websocket_session = MagicMock()
    websocket_session.close = AsyncMock()
    retry_client = MagicMock()
    retry_client.close = AsyncMock()
    proxy_connector = MagicMock()
    ssl_context = MagicMock()

    class _Settings(SimpleNamespace):
        def upstream_websocket_proxy_env(self):
            return {"SOCKS_PROXY": "socks5://settings-proxy.example.com:1080"}

    socks_settings = _Settings(
        http_connector_limit=100,
        http_connector_limit_per_host=50,
        upstream_websocket_trust_env=True,
    )

    with (
        patch("app.core.clients.http.get_settings", return_value=socks_settings),
        patch("app.core.clients.http._build_ssl_context", return_value=ssl_context),
        patch("app.core.clients.http.ProxyConnector") as proxy_connector_cls,
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[http_session, websocket_session],
        ) as client_session_cls,
        patch("app.core.clients.http.RetryClient", return_value=retry_client),
        patch.dict("os.environ", {}, clear=True),
    ):
        ws_proxy_connector = MagicMock()
        proxy_connector_cls.from_url.side_effect = [proxy_connector, ws_proxy_connector]
        await http_module.init_http_client()

    assert [call.args[0] for call in proxy_connector_cls.from_url.call_args_list] == [
        "socks5://settings-proxy.example.com:1080",
        "socks5://settings-proxy.example.com:1080",
    ]
    assert client_session_cls.call_args_list[0].kwargs["connector"] is proxy_connector
    assert client_session_cls.call_args_list[0].kwargs["trust_env"] is False
    assert client_session_cls.call_args_list[1].kwargs["connector"] is ws_proxy_connector
    assert client_session_cls.call_args_list[1].kwargs["trust_env"] is False

    await http_module.close_http_client()


@pytest.mark.asyncio
async def test_init_http_client_preserves_socks4a_remote_dns_for_proxy_connector() -> None:
    await http_module.close_http_client()

    http_session = MagicMock()
    websocket_session = MagicMock()
    websocket_session.close = AsyncMock()
    retry_client = MagicMock()
    retry_client.close = AsyncMock()
    proxy_connector = MagicMock()
    ssl_context = MagicMock()
    socks_settings = SimpleNamespace(
        http_connector_limit=100,
        http_connector_limit_per_host=50,
        upstream_websocket_trust_env=True,
    )

    with (
        patch("app.core.clients.http.get_settings", return_value=socks_settings),
        patch("app.core.clients.http._build_ssl_context", return_value=ssl_context),
        patch("app.core.clients.http.ProxyConnector") as proxy_connector_cls,
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[http_session, websocket_session],
        ),
        patch("app.core.clients.http.RetryClient", return_value=retry_client),
        patch.dict("os.environ", {"SOCKS_PROXY": "socks4a://proxy.example.com:1080"}, clear=True),
    ):
        ws_proxy_connector = MagicMock()
        proxy_connector_cls.from_url.side_effect = [proxy_connector, ws_proxy_connector]
        await http_module.init_http_client()

    assert [call.args[0] for call in proxy_connector_cls.from_url.call_args_list] == [
        "socks4://proxy.example.com:1080",
        "socks4://proxy.example.com:1080",
    ]
    assert [call.kwargs["rdns"] for call in proxy_connector_cls.from_url.call_args_list] == [True, True]

    await http_module.close_http_client()


def test_build_ssl_context_preserves_default_roots_and_adds_certifi_bundle() -> None:
    with (
        patch("app.core.clients.http.certifi.where", return_value="/tmp/cacert.pem") as certifi_where,
        patch("app.core.clients.http.ssl.create_default_context") as create_default_context,
    ):
        context = http_module._build_ssl_context()

    ssl_context = create_default_context.return_value
    certifi_where.assert_called_once_with()
    create_default_context.assert_called_once_with()
    ssl_context.load_verify_locations.assert_called_once_with(cafile="/tmp/cacert.pem")
    assert context is ssl_context


@pytest.mark.asyncio
async def test_refresh_http_client_closes_idle_previous_sessions() -> None:
    await http_module.close_http_client()

    first_http_session = MagicMock()
    first_websocket_session = MagicMock()
    first_websocket_session.close = AsyncMock()
    first_retry_client = MagicMock()
    first_retry_client.close = AsyncMock()

    second_http_session = MagicMock()
    second_websocket_session = MagicMock()
    second_websocket_session.close = AsyncMock()
    second_retry_client = MagicMock()
    second_retry_client.close = AsyncMock()

    with (
        patch("app.core.clients.http.get_settings", return_value=_settings()),
        patch("app.core.clients.http.aiohttp.TCPConnector"),
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[
                first_http_session,
                first_websocket_session,
                second_http_session,
                second_websocket_session,
            ],
        ),
        patch(
            "app.core.clients.http.RetryClient",
            side_effect=[first_retry_client, second_retry_client],
        ),
    ):
        initial = await http_module.init_http_client()
        refreshed = await http_module.refresh_http_client()

    assert initial.session is first_http_session
    assert refreshed.session is second_http_session

    await _drain_close_tasks()

    first_websocket_session.close.assert_awaited_once()
    first_retry_client.close.assert_awaited_once()
    second_websocket_session.close.assert_not_awaited()
    second_retry_client.close.assert_not_awaited()

    await http_module.close_http_client()

    first_websocket_session.close.assert_awaited_once()
    first_retry_client.close.assert_awaited_once()
    second_websocket_session.close.assert_awaited_once()
    second_retry_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_http_client_keeps_active_previous_session_open_until_lease_released() -> None:
    await http_module.close_http_client()

    first_http_session = MagicMock()
    first_websocket_session = MagicMock()
    first_websocket_session.close = AsyncMock()
    first_retry_client = MagicMock()
    first_retry_client.close = AsyncMock()

    second_http_session = MagicMock()
    second_websocket_session = MagicMock()
    second_websocket_session.close = AsyncMock()
    second_retry_client = MagicMock()
    second_retry_client.close = AsyncMock()

    with (
        patch("app.core.clients.http.get_settings", return_value=_settings()),
        patch("app.core.clients.http.aiohttp.TCPConnector"),
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[
                first_http_session,
                first_websocket_session,
                second_http_session,
                second_websocket_session,
            ],
        ),
        patch(
            "app.core.clients.http.RetryClient",
            side_effect=[first_retry_client, second_retry_client],
        ),
    ):
        initial = await http_module.init_http_client()
        lease = await http_module.acquire_http_client()
        refreshed = await http_module.refresh_http_client()

    assert lease.client is initial
    assert refreshed.session is second_http_session

    await _drain_close_tasks()

    first_websocket_session.close.assert_not_awaited()
    first_retry_client.close.assert_not_awaited()
    second_websocket_session.close.assert_not_awaited()
    second_retry_client.close.assert_not_awaited()

    await lease.close()
    await _drain_close_tasks()

    first_websocket_session.close.assert_awaited_once()
    first_retry_client.close.assert_awaited_once()
    second_websocket_session.close.assert_not_awaited()
    second_retry_client.close.assert_not_awaited()

    await http_module.close_http_client()

    second_websocket_session.close.assert_awaited_once()
    second_retry_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_http_client_force_closes_active_current_and_retired_sessions() -> None:
    await http_module.close_http_client()

    first_http_session = MagicMock()
    first_websocket_session = MagicMock()
    first_websocket_session.close = AsyncMock()
    first_retry_client = MagicMock()
    first_retry_client.close = AsyncMock()

    second_http_session = MagicMock()
    second_websocket_session = MagicMock()
    second_websocket_session.close = AsyncMock()
    second_retry_client = MagicMock()
    second_retry_client.close = AsyncMock()

    with (
        patch("app.core.clients.http.get_settings", return_value=_settings()),
        patch("app.core.clients.http.aiohttp.TCPConnector"),
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[
                first_http_session,
                first_websocket_session,
                second_http_session,
                second_websocket_session,
            ],
        ),
        patch(
            "app.core.clients.http.RetryClient",
            side_effect=[first_retry_client, second_retry_client],
        ),
    ):
        initial = await http_module.init_http_client()
        first_lease = await http_module.acquire_http_client()
        refreshed = await http_module.refresh_http_client()
        second_lease = await http_module.acquire_http_client()

    assert first_lease.client is initial
    assert second_lease.client is refreshed

    await asyncio.wait_for(http_module.close_http_client(), timeout=1.0)

    first_websocket_session.close.assert_awaited_once()
    first_retry_client.close.assert_awaited_once()
    second_websocket_session.close.assert_awaited_once()
    second_retry_client.close.assert_awaited_once()

    await first_lease.close()
    await second_lease.close()
    await _drain_close_tasks()

    first_websocket_session.close.assert_awaited_once()
    first_retry_client.close.assert_awaited_once()
    second_websocket_session.close.assert_awaited_once()
    second_retry_client.close.assert_awaited_once()
