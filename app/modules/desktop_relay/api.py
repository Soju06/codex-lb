from __future__ import annotations

import ipaddress
import logging
from collections.abc import AsyncIterator

from aiohttp import web
from yarl import URL

from app.modules.desktop_relay.transport import RelayTransport, open_transport


def validate_lb_url(value: str) -> URL:
    message = "--lb-url must be an HTTP(S) loopback origin without credentials, path, query or fragment"
    try:
        url = URL(value)
        host = url.host
        loopback = host == "localhost" or (host is not None and ipaddress.ip_address(host).is_loopback)
        valid = url.scheme in {"http", "https"} and loopback and url.port is not None
    except ValueError:
        raise ValueError(message) from None
    if not valid or url.user is not None or url.query_string or url.fragment or url.path != "/":
        raise ValueError(message)
    return url


def create_app(lb_url: str = "http://127.0.0.1:2455") -> web.Application:
    return _create_app(validate_lb_url(lb_url), URL("https://chatgpt.com"))


def _create_app(lb_origin: URL, backend_origin: URL, *, timeout: float = 60) -> web.Application:
    # Destination injection is private and used only by isolated transport tests.
    app = web.Application(handler_args={"auto_decompress": False})
    transport_key = web.AppKey("relay_transport", RelayTransport)

    async def lifespan(application: web.Application) -> AsyncIterator[None]:
        async with open_transport(backend_origin, timeout) as transport:
            application[transport_key] = transport
            yield

    async def relay(request: web.Request) -> web.StreamResponse:
        if request.headers.getall("Host", []) != ["localhost:8000"]:
            raise web.HTTPForbidden(text="Unexpected relay authority")
        if not request.raw_path.startswith("/backend-api/") or not request.path.startswith("/backend-api/"):
            raise web.HTTPNotFound()
        usage = request.method == "GET" and request.path in {"/backend-api/wham/usage", "/backend-api/wham/usage/"}
        raw_path = request.raw_path
        if usage:
            raw_path = "/api/codex/desktop/usage" + (
                "?" + request.rel_url.raw_query_string if request.rel_url.raw_query_string else ""
            )
        target = URL(str(lb_origin if usage else backend_origin).rstrip("/") + raw_path, encoded=True)
        return await request.app[transport_key].forward(request, target, usage=usage)

    app.cleanup_ctx.append(lifespan)
    app.router.add_route("*", "/{path:.*}", relay)
    return app


def run(lb_url: str) -> None:
    app = create_app(lb_url)
    # Error records can contain URL or parser input. This listener deliberately emits none.
    silent_logger = logging.Logger("codex_lb.desktop_relay", level=logging.CRITICAL + 1)
    try:
        web.run_app(
            app,
            host=["127.0.0.1", "::1"],
            port=8000,
            access_log=None,
            handler_cancellation=True,
            logger=silent_logger,
            print=None,
        )
    except Exception:
        # Connector startup errors can contain credentials from proxy URLs.
        raise SystemExit("Desktop relay could not start; check port 8000 and outbound proxy settings") from None
