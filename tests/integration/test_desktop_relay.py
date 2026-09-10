from contextlib import asynccontextmanager

import pytest
from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer
from yarl import URL

from app.modules.desktop_relay.api import _create_app, validate_lb_url

pytestmark = pytest.mark.integration


@asynccontextmanager
async def servers(handler, *, timeout: float = 60):
    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/{path:.*}", handler)
    async with TestServer(upstream_app) as upstream:
        async with TestServer(_create_app(upstream.make_url(""), upstream.make_url(""), timeout=timeout)) as relay:
            async with ClientSession() as client:
                yield client, relay


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://user@localhost:2455",
        "http://localhost/a",
        "http://localhost?x=1",
        "http://localhost#x",
        "ftp://localhost",
        "http://127.0.0.1.example.org",
    ],
)
def test_rejects_unsafe_origins(url):
    with pytest.raises(ValueError):
        validate_lb_url(url)


@pytest.mark.parametrize("url", ["http://localhost:2455", "https://127.0.0.1:2455", "http://[::1]:2455"])
def test_loopback_origins(url):
    assert str(validate_lb_url(url)) == url


@pytest.mark.parametrize("path", ["/backend-api/wham/usage", "/backend-api/wham/usage/"])
async def test_usage_alias_and_original_identity(path):
    async def upstream(request):
        assert request.path == "/api/codex/desktop/usage"
        assert request.headers["Authorization"] == "Bearer original"
        assert request.headers["chatgpt-account-id"] == "original-account"
        return web.json_response({"allowed": False}, status=503)

    async with servers(upstream) as (client, relay):
        async with client.get(
            relay.make_url(path),
            headers={
                "Host": "localhost:8000",
                "Authorization": "Bearer original",
                "chatgpt-account-id": "original-account",
            },
        ) as response:
            assert response.status == 503
            assert await response.json() == {"allowed": False}


async def test_preserves_http_raw_path_duplicates_cookies_body_and_no_redirect():
    async def upstream(request):
        assert request.raw_path == "/backend-api/a%2Fb?q=%2F&x=1&x=2"
        assert request.method == "POST"
        assert await request.read() == b"original body"
        assert request.headers.getall("X-Duplicate") == ["one", "two"]
        assert request.headers["Cookie"] == "original=yes"
        assert "X-Hop" not in request.headers
        return web.Response(
            status=307,
            headers=[
                ("Location", "https://invalid.example"),
                ("Set-Cookie", "a=1"),
                ("Set-Cookie", "b=2"),
                ("X-Integrity", "signed"),
            ],
            body=b"redirect",
        )

    async with servers(upstream) as (client, relay):
        url = URL(str(relay.make_url("")) + "/backend-api/a%2Fb?q=%2F&x=1&x=2", encoded=True)
        async with client.post(
            url,
            headers=[
                ("Host", "localhost:8000"),
                ("Cookie", "original=yes"),
                ("X-Duplicate", "one"),
                ("X-Duplicate", "two"),
                ("Connection", "X-Hop"),
                ("X-Hop", "secret"),
            ],
            data=b"original body",
            allow_redirects=False,
        ) as response:
            assert response.status == 307
            assert response.headers.getall("Set-Cookie") == ["a=1", "b=2"]
            assert response.headers["X-Integrity"] == "signed"
            assert await response.read() == b"redirect"


@pytest.mark.parametrize(
    "host,path,status", [("evil.example", "/backend-api/a", 403), ("localhost:8000", "/other", 404)]
)
async def test_rejects_unsafe_requests(host, path, status):
    async def upstream(request):
        pytest.fail("Denied request reached upstream")

    async with servers(upstream) as (client, relay):
        async with client.get(relay.make_url(path), headers={"Host": host}) as response:
            assert response.status == status


async def test_sse_stream_is_not_buffered_and_disconnect_cancels_upstream():
    import asyncio

    disconnected = asyncio.Event()

    async def upstream(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        try:
            while True:
                await response.write(b"data: first\n\n")
                await asyncio.sleep(0.01)
        except (ConnectionError, asyncio.CancelledError):
            disconnected.set()
        return response

    async with servers(upstream) as (client, relay):
        async with client.get(relay.make_url("/backend-api/events"), headers={"Host": "localhost:8000"}) as response:
            assert await response.content.readuntil(b"\n\n") == b"data: first\n\n"
        await asyncio.wait_for(disconnected.wait(), 2)


async def test_websocket_identity_messages_and_close():
    async def upstream(request):
        assert request.headers["Authorization"] == "Bearer original"
        ws = web.WebSocketResponse(protocols=["test"])
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await ws.send_str(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await ws.send_bytes(msg.data)
        return ws

    async with servers(upstream) as (client, relay):
        async with client.ws_connect(
            relay.make_url("/backend-api/ws"),
            headers={"Host": "localhost:8000", "Authorization": "Bearer original"},
            protocols=["test"],
        ) as ws:
            assert ws.protocol == "test"
            await ws.send_str("text")
            assert (await ws.receive()).data == "text"
            await ws.send_bytes(b"binary")
            assert (await ws.receive()).data == b"binary"


async def test_timeout_is_generic():
    import asyncio

    async def upstream(request):
        await asyncio.sleep(0.1)
        return web.Response(text="late")

    async with servers(upstream, timeout=0.01) as (client, relay):
        async with client.get(
            relay.make_url("/backend-api/secret?token=private"), headers={"Host": "localhost:8000"}
        ) as response:
            assert response.status == 502
            assert await response.text() == "Desktop relay upstream unavailable"


async def test_websocket_redirect_does_not_reach_redirect_target():
    calls = []

    async def upstream(request):
        calls.append(request.path)
        return web.Response(status=307, headers={"Location": "/redirected"})

    async with servers(upstream) as (client, relay):
        async with client.get(
            relay.make_url("/backend-api/ws"),
            headers={"Host": "localhost:8000", "Upgrade": "websocket"},
            allow_redirects=False,
        ) as response:
            assert response.status == 307
            assert response.headers["Location"] == "/redirected"
    assert calls == ["/backend-api/ws"]


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_websocket_handshake_errors_preserve_status_body_and_identity_headers(status):
    async def upstream(request):
        assert request.headers["Authorization"] == "Bearer original"
        return web.Response(
            status=status,
            body=b'{"error":{"code":"upstream_code"}}',
            headers={"Content-Type": "application/json", "X-OAI-IS-Update": "original-integrity"},
        )

    async with servers(upstream) as (client, relay):
        async with client.get(
            relay.make_url("/backend-api/ws"),
            headers={"Host": "localhost:8000", "Upgrade": "websocket", "Authorization": "Bearer original"},
        ) as response:
            assert response.status == status
            assert await response.read() == b'{"error":{"code":"upstream_code"}}'
            assert response.headers["X-OAI-IS-Update"] == "original-integrity"


async def test_relay_does_not_store_upstream_cookies():
    async def upstream(request):
        assert "Cookie" not in request.headers
        return web.Response(headers={"Set-Cookie": "private=yes; Path=/"})

    async with servers(upstream) as (client, relay):
        for _ in range(2):
            async with client.get(
                relay.make_url("/backend-api/cookies"), headers={"Host": "localhost:8000"}
            ) as response:
                assert response.status == 200
                await response.read()


async def test_compressed_body_remains_encoded():
    import gzip

    payload = gzip.compress(b"original compressed payload")

    async def upstream(request):
        return web.Response(body=payload, headers={"Content-Encoding": "gzip"})

    async with servers(upstream) as (client, relay):
        async with client.get(
            relay.make_url("/backend-api/encoded"), headers={"Host": "localhost:8000"}, auto_decompress=False
        ) as response:
            assert response.headers["Content-Encoding"] == "gzip"
            assert await response.read() == payload


async def test_websocket_preserves_upstream_close_code_and_reason():
    async def upstream(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close(code=4001, message=b"expired")
        return ws

    async with servers(upstream) as (client, relay):
        async with client.ws_connect(relay.make_url("/backend-api/ws"), headers={"Host": "localhost:8000"}) as ws:
            message = await ws.receive()
            assert message.type == WSMsgType.CLOSE
            assert message.data == 4001
            assert message.extra == "expired"


async def test_websocket_empty_upstream_close_becomes_normal_close():
    async def upstream(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        assert request.transport is not None
        # RFC 6455 permits a CLOSE frame with no status payload.
        request.transport.write(b"\x88\x00")
        await ws.receive()
        return ws

    async with servers(upstream) as (client, relay):
        async with client.ws_connect(relay.make_url("/backend-api/ws"), headers={"Host": "localhost:8000"}) as ws:
            message = await ws.receive(timeout=2)
            assert message.type == WSMsgType.CLOSE
            assert message.data == 1000
            assert message.extra == ""


async def test_compressed_request_body_retains_encoding():
    import gzip

    payload = gzip.compress(b"original request")

    async def upstream(request):
        assert request.headers["Content-Encoding"] == "gzip"
        # The fake upstream decodes its own incoming HTTP body.
        assert await request.read() == b"original request"
        return web.Response(status=204)

    async with servers(upstream) as (client, relay):
        async with client.post(
            relay.make_url("/backend-api/upload"),
            data=payload,
            headers={"Host": "localhost:8000", "Content-Encoding": "gzip"},
        ) as response:
            assert response.status == 204


@pytest.mark.parametrize(
    "cookie,expected",
    [
        (
            "_devicecheck=value; Domain=.chatgpt.com; Path=/; Secure; HttpOnly; SameSite=None",
            "_devicecheck=value; Path=/; Secure; HttpOnly; SameSite=None",
        ),
        ("__cf_bm=value; domain=chatgpt.com; Max-Age=30; Secure", "__cf_bm=value; Max-Age=30; Secure"),
        ("a=value; DOMAIN = ChatGPT.COM ; Path=/backend-api", "a=value; Path=/backend-api"),
        ("a=value; Path=/; Secure", "a=value; Path=/; Secure"),
        ("a=value; Domain=evil.example; Secure", "a=value; Domain=evil.example; Secure"),
        ("a=value; Domain=chatgpt.com.evil.example", "a=value; Domain=chatgpt.com.evil.example"),
        ("a=value; Domain=..chatgpt.com", "a=value; Domain=..chatgpt.com"),
        ("__Host-a=value; Domain=chatgpt.com; Secure", "__Host-a=value; Domain=chatgpt.com; Secure"),
        (
            "a=; Domain=.chatgpt.com; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Partitioned; Secure",
            "a=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Partitioned; Secure",
        ),
        (
            'a="value; Domain=chatgpt.com; still-value"; Domain=.chatgpt.com; Secure',
            'a="value; Domain=chatgpt.com; still-value"; Secure',
        ),
    ],
)
async def test_official_cookie_domain_becomes_loopback_host_only(cookie, expected):
    async def upstream(request):
        return web.Response(headers=[("Set-Cookie", cookie), ("Set-Cookie", "other=unchanged; Secure")])

    async with servers(upstream) as (client, relay):
        async with client.get(
            relay.make_url("/backend-api/devicecheck"), headers={"Host": "localhost:8000"}
        ) as response:
            assert response.headers.getall("Set-Cookie") == [expected, "other=unchanged; Secure"]


async def test_usage_response_cookie_domain_is_not_translated():
    cookie = "a=value; Domain=chatgpt.com; Secure"

    async def upstream(request):
        return web.Response(headers={"Set-Cookie": cookie})

    async with servers(upstream) as (client, relay):
        async with client.get(
            relay.make_url("/backend-api/wham/usage"), headers={"Host": "localhost:8000"}
        ) as response:
            assert response.headers["Set-Cookie"] == cookie


@pytest.mark.parametrize("suffix", ["", "/"])
@pytest.mark.parametrize(
    "method,path,mapped",
    [
        ("GET", "/backend-api/wham/rate-limit-reset-credits", "/api/codex/desktop/reset-credits"),
        ("POST", "/backend-api/wham/rate-limit-reset-credits/consume", "/api/codex/desktop/reset-credits/consume"),
    ],
)
async def test_native_reset_routes_to_lb_with_original_request(method, path, mapped, suffix):
    async def local(request):
        assert request.raw_path == mapped + "?x=1&x=2"
        assert request.method == method
        assert request.headers["Authorization"] == "Bearer original"
        assert request.headers["chatgpt-account-id"] == "original-account"
        assert await request.read() == b'{"redeem_request_id":"native-request"}'
        return web.json_response({"code": "reset"}, headers={"Cache-Control": "no-store"})

    async def backend(request):
        pytest.fail("Native reset request escaped to the original backend")

    local_app = web.Application()
    local_app.router.add_route("*", "/{path:.*}", local)
    backend_app = web.Application()
    backend_app.router.add_route("*", "/{path:.*}", backend)
    async with TestServer(local_app) as lb, TestServer(backend_app) as original:
        async with TestServer(_create_app(lb.make_url(""), original.make_url(""))) as relay:
            async with ClientSession() as client:
                async with client.request(
                    method,
                    relay.make_url(path + suffix + "?x=1&x=2"),
                    headers={
                        "Host": "localhost:8000",
                        "Authorization": "Bearer original",
                        "chatgpt-account-id": "original-account",
                    },
                    data=b'{"redeem_request_id":"native-request"}',
                ) as response:
                    assert response.status == 200
                    assert await response.json() == {"code": "reset"}
                    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/backend-api/wham/rate-limit-reset-credits"),
        ("GET", "/backend-api/wham/rate-limit-reset-credits/consume"),
        ("GET", "/backend-api/wham/rate-limit-reset-credits/other"),
        ("GET", "/backend-api/wham/rate-limit-reset-credits-extra"),
        ("GET", "/backend-api/wham/rate-limit-reset-credits//"),
    ],
)
async def test_reset_lookalikes_remain_on_original_backend(method, path):
    async def backend(request):
        assert request.path == path
        assert request.method == method
        return web.Response(status=202)

    # Any accidental local routing fails to connect to this unused origin.
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", backend)
    async with TestServer(app) as original:
        async with TestServer(_create_app(URL("http://127.0.0.1:1"), original.make_url(""))) as relay:
            async with ClientSession() as client:
                async with client.request(method, relay.make_url(path), headers={"Host": "localhost:8000"}) as response:
                    assert response.status == 202
