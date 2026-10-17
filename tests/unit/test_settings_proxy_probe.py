from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.settings.api as settings_api

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_socks_probe_uses_shared_ssl_context() -> None:
    endpoint = SimpleNamespace(
        scheme="socks5h",
        host="proxy.test",
        port=1080,
        username=None,
        password=None,
        proxy_url="socks5h://proxy.test:1080",
    )
    shared_context = MagicMock()
    session = MagicMock()
    session.__aenter__.return_value = session
    session.get = AsyncMock(return_value=SimpleNamespace(status=204))

    with (
        patch("app.modules.settings.api._shared_ssl_context", return_value=shared_context) as shared_factory,
        patch("app.modules.settings.api.ProxyConnector") as proxy_connector_cls,
        patch("app.modules.settings.api.aiohttp.ClientSession", return_value=session),
    ):
        status = await settings_api._probe_upstream_proxy_endpoint(endpoint)

    assert status == 204
    shared_factory.assert_called_once_with()
    assert proxy_connector_cls.call_args.kwargs["ssl"] is shared_context
    assert proxy_connector_cls.call_args.kwargs["rdns"] is True
