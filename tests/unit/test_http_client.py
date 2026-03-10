from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.core.clients.http as http_module

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_init_http_client_uses_proxy_settings_from_environment() -> None:
    await http_module.close_http_client()

    session = MagicMock()
    retry_client = MagicMock()
    retry_client.close = AsyncMock()

    with (
        patch("app.core.clients.http.aiohttp.ClientSession", return_value=session) as client_session_cls,
        patch("app.core.clients.http.RetryClient", return_value=retry_client) as retry_client_cls,
    ):
        client = await http_module.init_http_client()

    assert client.session is session
    assert client.retry_client is retry_client
    assert client_session_cls.call_args.kwargs["trust_env"] is True
    retry_client_cls.assert_called_once_with(client_session=session, raise_for_status=False)

    await http_module.close_http_client()
