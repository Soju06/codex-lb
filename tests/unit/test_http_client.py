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

    with (
        patch("app.core.clients.http.get_settings", return_value=_settings()),
        patch("app.core.clients.http.aiohttp.TCPConnector", return_value=connector) as tcp_connector_cls,
        patch(
            "app.core.clients.http.aiohttp.ClientSession",
            side_effect=[http_session, websocket_session],
        ) as client_session_cls,
        patch("app.core.clients.http.RetryClient", return_value=retry_client),
    ):
        await http_module.init_http_client()

    tcp_connector_cls.assert_called_once_with(limit=100, limit_per_host=50)
    assert client_session_cls.call_args_list[0].kwargs["connector"] is connector

    await http_module.close_http_client()


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

    await asyncio.wait_for(http_module.close_http_client(), timeout=0.1)

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
