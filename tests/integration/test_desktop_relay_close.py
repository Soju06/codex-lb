import pytest
from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer

from app.modules.desktop_relay.api import _create_app

pytestmark = pytest.mark.integration


async def test_empty_upstream_close_reaches_desktop_as_normal_closure():
    async def upstream(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Emit a valid close frame with no status payload at the peer boundary.
        assert ws._writer is not None
        await ws._writer.send_frame(b"", WSMsgType.CLOSE)
        await ws.receive()
        return ws

    app = web.Application()
    app.router.add_get("/backend-api/ws", upstream)
    async with TestServer(app) as origin:
        async with TestServer(_create_app(origin.make_url(""), origin.make_url(""))) as relay:
            async with ClientSession() as client:
                async with client.ws_connect(
                    relay.make_url("/backend-api/ws"), headers={"Host": "localhost:8000"}
                ) as ws:
                    message = await ws.receive(timeout=2)
                    assert message.type == WSMsgType.CLOSE
                    assert message.data == 1000
                    assert message.extra == ""
