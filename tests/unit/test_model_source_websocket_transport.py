from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.modules.model_sources.websocket as transport
from app.core.crypto import TokenEncryptor
from app.db.models import ModelSource

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme,expected", [("http", "ws"), ("https", "wss")])
@pytest.mark.parametrize("credential", [None, "source-secret"])
@pytest.mark.parametrize("trust_env", [False, True])
async def test_native_handshake_endpoint_auth_and_proxy_policy(monkeypatch, scheme, expected, credential, trust_env):
    source = ModelSource(
        id="native",
        name="native",
        base_url=f"{scheme}://provider.invalid/custom/v1/",
        api_key_encrypted=TokenEncryptor().encrypt(credential) if credential else None,
    )
    connect = AsyncMock()
    monkeypatch.setattr(transport, "_SourceConnect", connect)
    monkeypatch.setattr(transport, "get_settings", lambda: SimpleNamespace(upstream_websocket_trust_env=trust_env))
    proxy_lookups = []

    def proxy_for(url):
        proxy_lookups.append(url)
        return "http://proxy.invalid:8888"

    monkeypatch.setattr(transport, "resolve_websocket_proxy_from_env", proxy_for)
    await transport.open_source_websocket(source, timeout=3.5)
    handshake = connect.await_args
    assert handshake is not None
    args, options = handshake
    assert args == (f"{expected}://provider.invalid/custom/v1/responses",)
    assert options["additional_headers"] == ({"Authorization": "Bearer source-secret"} if credential else None)
    assert options["proxy"] == ("http://proxy.invalid:8888" if trust_env else None)
    assert proxy_lookups == ([args[0]] if trust_env else [])
    assert options["open_timeout"] == 3.5 and options["close_timeout"] <= 3.5
    assert options["ping_timeout"] is None
    assert options["max_size"] == transport.MAX_MESSAGE_BYTES and options["max_queue"] == 4


@pytest.mark.parametrize("message", [b"{}", "[]", "not json", '{"x":"' + "x" * transport.MAX_MESSAGE_BYTES + '"}'])
def test_native_event_parser_rejects_unsupported_or_oversize_frames(message):
    assert transport.parse_source_event(message) is None
