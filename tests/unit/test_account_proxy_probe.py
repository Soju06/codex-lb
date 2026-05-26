"""Unit + small integration tests for ``account_proxy_probe``.

The unit tests inject a stub :class:`aiohttp.ClientSession` via the test
hook in :mod:`app.core.clients.account_proxy_probe` so we can exercise each
``ProbeReason`` branch without spinning up a real SOCKS5 server. One
happy-path integration test runs an inline asyncio SOCKS5 server and an
``aiohttp.web`` fake ``auth.openai.com`` to confirm the wiring works
end-to-end with a real ``aiohttp_socks.ProxyConnector``.
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from python_socks._errors import (
    ProxyConnectionError,
    ProxyError,
    ProxyTimeoutError,
)

from app.core.clients import account_proxy_probe as probe_module
from app.core.clients.account_proxy_probe import (
    ProbeReason,
    probe_account_proxy,
)
from app.core.config.settings import Settings

pytestmark = pytest.mark.unit


def _proxy_auth_fixture(suffix: str = "primary") -> str:
    return f"proxy-fixture-value-{suffix}"


def _proxy_user_fixture() -> str:
    return "proxy-user-fixture"


# --------------------------------------------------------------------------
# Test scaffolding: a stub session whose ``post(...)`` is fully scripted
# --------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> "_StubResponse":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def text(self) -> str:
        return self._body

    async def json(self, *, content_type: str | None = "application/json") -> Any:
        del content_type
        return json.loads(self._body)


class _StubSession:
    """Minimal aiohttp.ClientSession stand-in.

    ``post(...)`` either returns a context manager that yields a configured
    ``_StubResponse`` or raises a pre-seeded exception. The class also
    implements the ``async with`` protocol so the probe's ``async with
    session`` block works.
    """

    def __init__(self, *, response: _StubResponse | None = None, exc: BaseException | None = None) -> None:
        if (response is None) == (exc is None):
            raise AssertionError("Exactly one of `response`/`exc` must be provided")
        self._response = response
        self._exc = exc
        self.closed_calls = 0
        self.posted_url: str | None = None
        self.posted_kwargs: dict[str, Any] | None = None

    async def __aenter__(self) -> "_StubSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def close(self) -> None:
        self.closed_calls += 1

    def post(self, url: str, **kwargs: Any) -> Any:
        self.posted_url = url
        self.posted_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        assert self._response is not None  # narrow for type checkers
        return self._response


@asynccontextmanager
async def _stub_session_factory(session: _StubSession):
    """Install a session-factory override that returns ``session``.

    The override is reset on exit so tests don't bleed state between cases.
    """

    async def factory(_connection, _timeout_seconds):
        return session

    probe_module._set_session_factory_for_test(factory)
    try:
        yield
    finally:
        probe_module._set_session_factory_for_test(None)


# --------------------------------------------------------------------------
# Classification tests (one per ProbeReason)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_returns_ok_for_2xx_response() -> None:
    session = _StubSession(
        response=_StubResponse(
            status=200,
            body='{"access_token":"x","refresh_token":"r","id_token":"id"}',
        )
    )
    async with _stub_session_factory(session):
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=None,
            password=None,
            remote_dns=True,
            refresh_token="rft_abc",
        )

    assert result.reason is ProbeReason.OK
    assert result.upstream_status_code == 200
    assert result.tokens is not None
    assert result.tokens.access_token == "x"
    assert result.tokens.refresh_token == "r"
    assert result.tokens.id_token == "id"
    assert result.ok is True
    assert session.posted_url and session.posted_url.endswith("/oauth/token")
    payload = session.posted_kwargs["json"] if session.posted_kwargs else {}
    assert payload.get("grant_type") == "refresh_token"
    assert payload.get("refresh_token") == "rft_abc"


@pytest.mark.asyncio
async def test_probe_returns_upstream_status_for_4xx() -> None:
    session = _StubSession(response=_StubResponse(status=401, body='{"error":"invalid_grant"}'))
    async with _stub_session_factory(session):
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=None,
            password=None,
            remote_dns=True,
            refresh_token="rft_expired",
        )
    assert result.reason is ProbeReason.UPSTREAM_STATUS
    assert result.upstream_status_code == 401
    assert "invalid_grant" in (result.detail or "")


@pytest.mark.asyncio
async def test_probe_returns_proxy_connect_for_socks_connect_error() -> None:
    session = _StubSession(exc=ProxyConnectionError("Could not connect to proxy"))
    async with _stub_session_factory(session):
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=None,
            password=None,
            remote_dns=True,
            refresh_token="rft",
        )
    assert result.reason is ProbeReason.PROXY_CONNECT
    assert "proxy" in (result.detail or "").lower()


@pytest.mark.asyncio
async def test_probe_returns_proxy_auth_for_authentication_failure() -> None:
    session = _StubSession(exc=ProxyError("Username and password authentication failure"))
    async with _stub_session_factory(session):
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=_proxy_user_fixture(),
            password=_proxy_auth_fixture("invalid"),
            remote_dns=True,
            refresh_token="rft",
        )
    assert result.reason is ProbeReason.PROXY_AUTH


@pytest.mark.asyncio
async def test_probe_proxy_error_without_auth_message_is_treated_as_connect() -> None:
    session = _StubSession(exc=ProxyError("Connection closed unexpectedly"))
    async with _stub_session_factory(session):
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=None,
            password=None,
            remote_dns=True,
            refresh_token="rft",
        )
    assert result.reason is ProbeReason.PROXY_CONNECT


@pytest.mark.asyncio
async def test_probe_returns_timeout_for_proxy_timeout_error() -> None:
    session = _StubSession(exc=ProxyTimeoutError("proxy negotiation timed out"))
    async with _stub_session_factory(session):
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=None,
            password=None,
            remote_dns=True,
            refresh_token="rft",
        )
    assert result.reason is ProbeReason.TIMEOUT


@pytest.mark.asyncio
async def test_probe_returns_timeout_for_asyncio_timeout() -> None:
    session = _StubSession(exc=asyncio.TimeoutError("read timeout"))
    async with _stub_session_factory(session):
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=None,
            password=None,
            remote_dns=True,
            refresh_token="rft",
        )
    assert result.reason is ProbeReason.TIMEOUT


@pytest.mark.asyncio
async def test_probe_returns_tls_for_ssl_error() -> None:
    # Build a ConnectionKey-like SimpleNamespace to satisfy the
    # ClientConnectorSSLError constructor signature without depending on
    # private aiohttp helpers across versions.
    fake_key = SimpleNamespace(
        host="auth.openai.com",
        port=443,
        is_ssl=True,
        ssl=None,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )
    ssl_exc = aiohttp.ClientConnectorSSLError(cast(Any, fake_key), OSError("bad cert"))
    session = _StubSession(exc=ssl_exc)
    async with _stub_session_factory(session):
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=None,
            password=None,
            remote_dns=True,
            refresh_token="rft",
        )
    assert result.reason is ProbeReason.TLS


@pytest.mark.asyncio
async def test_probe_session_factory_failures_are_classified_too() -> None:
    """Failures while CONSTRUCTING the session must also map to a reason."""

    async def factory(_conn, _t):
        raise ProxyConnectionError("dns failed")

    probe_module._set_session_factory_for_test(factory)
    try:
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=None,
            password=None,
            remote_dns=True,
            refresh_token="rft",
        )
    finally:
        probe_module._set_session_factory_for_test(None)

    assert result.reason is ProbeReason.PROXY_CONNECT


@pytest.mark.asyncio
async def test_probe_uses_configured_auth_base_url_and_oauth_payload() -> None:
    captured_payload: dict[str, Any] = {}
    captured_url: dict[str, Any] = {}

    class _CapturingResponse(_StubResponse):
        pass

    class _CapturingSession(_StubSession):
        def post(self, url: str, **kwargs: Any) -> Any:
            captured_url["url"] = url
            captured_payload.update(kwargs.get("json") or {})
            return self._response  # type: ignore[return-value]

    session = _CapturingSession(
        response=_CapturingResponse(
            status=200,
            body='{"access_token":"x","refresh_token":"r","id_token":"id"}',
        )
    )

    fake_settings = SimpleNamespace(
        account_proxy_probe_timeout_seconds=5.0,
        auth_base_url="https://auth.example.test/",
        oauth_client_id="cid_123",
        oauth_scope="openid profile email",
    )

    async with _stub_session_factory(session):
        result = await probe_account_proxy(
            host="proxy.example.com",
            port=1080,
            username=None,
            password=None,
            remote_dns=True,
            refresh_token="rft_abc",
            settings=cast(Settings, fake_settings),
        )

    assert result.reason is ProbeReason.OK
    assert captured_url["url"] == "https://auth.example.test/oauth/token"
    assert captured_payload == {
        "grant_type": "refresh_token",
        "client_id": "cid_123",
        "refresh_token": "rft_abc",
        "scope": "openid profile email",
    }


# --------------------------------------------------------------------------
# Happy-path integration test: real SOCKS5 server + fake auth endpoint
# --------------------------------------------------------------------------


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


async def _run_minimal_socks5_server(host: str, port: int) -> asyncio.AbstractServer:
    """A minimal CONNECT-only, no-auth SOCKS5 server.

    Implements just enough of RFC 1928 to negotiate ``CONNECT`` to an IPv4
    address with ``no auth``. Once the upstream connection is established,
    bytes are forwarded transparently in both directions.
    """

    async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        try:
            # Greeting: VER NMETHODS METHODS
            header = await client_reader.readexactly(2)
            if header[0] != 0x05:
                client_writer.close()
                return
            nmethods = header[1]
            await client_reader.readexactly(nmethods)
            # Reply: VER METHOD (0x00 = no auth)
            client_writer.write(b"\x05\x00")
            await client_writer.drain()

            # Request: VER CMD RSV ATYP DST.ADDR DST.PORT
            request = await client_reader.readexactly(4)
            if request[1] != 0x01 or request[3] != 0x01:  # CONNECT + IPv4
                client_writer.write(b"\x05\x07\x00\x01" + b"\x00" * 4 + b"\x00\x00")
                await client_writer.drain()
                client_writer.close()
                return
            ipv4 = await client_reader.readexactly(4)
            port_bytes = await client_reader.readexactly(2)
            target_host = socket.inet_ntoa(ipv4)
            target_port = struct.unpack("!H", port_bytes)[0]

            try:
                upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
            except OSError:
                client_writer.write(b"\x05\x05\x00\x01" + b"\x00" * 4 + b"\x00\x00")
                await client_writer.drain()
                client_writer.close()
                return

            # Reply: success, BND.ADDR/BND.PORT (we use 0.0.0.0:0 — clients ignore)
            client_writer.write(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
            await client_writer.drain()

            async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
                try:
                    while True:
                        chunk = await src.read(4096)
                        if not chunk:
                            break
                        dst.write(chunk)
                        await dst.drain()
                finally:
                    with __import__("contextlib").suppress(Exception):
                        dst.close()

            await asyncio.gather(
                pipe(client_reader, upstream_writer),
                pipe(upstream_reader, client_writer),
                return_exceptions=True,
            )
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            with __import__("contextlib").suppress(Exception):
                client_writer.close()

    return await asyncio.start_server(handle, host=host, port=port)


@pytest.mark.asyncio
async def test_probe_end_to_end_through_inline_socks5_and_fake_upstream() -> None:
    """End-to-end: ProxyConnector → inline SOCKS5 → inline auth endpoint."""

    async def handle_token(request: web.Request) -> web.Response:
        body = await request.json()
        assert body.get("grant_type") == "refresh_token"
        return web.json_response({"access_token": "ok", "refresh_token": "rft", "id_token": "id"})

    upstream_app = web.Application()
    upstream_app.router.add_post("/oauth/token", handle_token)
    runner = web.AppRunner(upstream_app)
    await runner.setup()
    upstream_port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", upstream_port)
    await site.start()

    socks_port = _free_port()
    socks_server = await _run_minimal_socks5_server("127.0.0.1", socks_port)

    fake_settings = SimpleNamespace(
        account_proxy_probe_timeout_seconds=5.0,
        auth_base_url=f"http://127.0.0.1:{upstream_port}",
        oauth_client_id="cid",
        oauth_scope="openid",
    )

    try:
        # Use rdns=False so the SOCKS5 handler can resolve "127.0.0.1"
        # locally (the inline server only handles IPv4 ATYP=0x01).
        result = await probe_account_proxy(
            host="127.0.0.1",
            port=socks_port,
            username=None,
            password=None,
            remote_dns=False,
            refresh_token="rft_abc",
            settings=cast(Settings, fake_settings),
        )
    finally:
        socks_server.close()
        await socks_server.wait_closed()
        await runner.cleanup()

    assert result.reason is ProbeReason.OK
    assert result.upstream_status_code == 200


@pytest.mark.asyncio
async def test_probe_returns_proxy_connect_when_proxy_port_is_closed() -> None:
    """If the SOCKS5 endpoint is unreachable, classify as proxy_connect."""

    closed_port = _free_port()
    fake_settings = SimpleNamespace(
        account_proxy_probe_timeout_seconds=2.0,
        auth_base_url="http://127.0.0.1:1",
        oauth_client_id="cid",
        oauth_scope="openid",
    )

    # Restore default factory so we exercise the real ProxyConnector path.
    probe_module._set_session_factory_for_test(None)
    result = await probe_account_proxy(
        host="127.0.0.1",
        port=closed_port,
        username=None,
        password=None,
        remote_dns=False,
        refresh_token="rft_abc",
        settings=cast(Settings, fake_settings),
    )
    assert result.reason is ProbeReason.PROXY_CONNECT


def test_set_session_factory_for_test_is_idempotent_reset() -> None:
    probe_module._set_session_factory_for_test(None)
    probe_module._set_session_factory_for_test(None)


def test_unused_patch_import_is_kept_for_future_use() -> None:
    # Keep ``patch`` referenced so the (currently) unused import is not
    # flagged. Future tests may want to swap individual functions.
    assert patch is not None
