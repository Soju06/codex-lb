from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.core.clients.http as http_module

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_init_http_client_uses_separate_http_and_websocket_sessions() -> None:
    await http_module.close_http_client()

    http_session = MagicMock()
    websocket_session = MagicMock()
    websocket_session.close = AsyncMock()
    retry_client = MagicMock()
    retry_client.close = AsyncMock()

    with (
        patch("app.core.clients.http.aiohttp.TCPConnector"),
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[http_session, websocket_session],
        ) as client_session_cls,
        patch(
            "app.core.clients.http.get_settings",
            return_value=SimpleNamespace(
                http_connector_limit=100,
                http_connector_limit_per_host=50,
                upstream_websocket_trust_env=False,
            ),
        ),
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

    with (
        patch("app.core.clients.http.aiohttp.TCPConnector", return_value=connector) as tcp_connector_cls,
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[http_session, websocket_session],
        ) as client_session_cls,
        patch(
            "app.core.clients.http.get_settings",
            return_value=SimpleNamespace(
                http_connector_limit=100,
                http_connector_limit_per_host=50,
                upstream_websocket_trust_env=False,
            ),
        ),
        patch("app.core.clients.http.RetryClient", return_value=retry_client),
    ):
        await http_module.init_http_client()

    tcp_connector_cls.assert_called_once_with(limit=100, limit_per_host=50)
    assert client_session_cls.call_args_list[0].kwargs["connector"] is connector

    await http_module.close_http_client()
