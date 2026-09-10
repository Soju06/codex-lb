from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from aiohttp import web

from app.modules.desktop_relay.api import create_app

RelayMode = Literal["off", "loopback", "container"]
_SHUTDOWN_TIMEOUT = 5.0


@asynccontextmanager
async def serve_relay(mode: RelayMode, lb_origin: str | None) -> AsyncIterator[None]:
    if mode == "off":
        yield
        return
    if lb_origin is None:
        raise ValueError("Embedded Desktop relay requires a validated local LB origin")
    # HTTP parser and connector errors may contain caller or proxy credentials.
    silent_logger = logging.Logger("codex_lb.desktop_relay", level=logging.CRITICAL + 1)
    runner = web.AppRunner(
        create_app(lb_origin),
        access_log=None,
        logger=silent_logger,
        handler_cancellation=True,
        shutdown_timeout=_SHUTDOWN_TIMEOUT,
    )
    try:
        try:
            await runner.setup()
            hosts = ("0.0.0.0",) if mode == "container" else ("127.0.0.1", "::1")
            for host in hosts:
                await web.TCPSite(runner, host, 8000).start()
        except Exception:
            raise RuntimeError("Desktop relay could not start; check port 8000 and outbound proxy settings") from None
        yield
    finally:
        # AppRunner stops admission, cancels requests after the grace period,
        # then closes the transport cleanup context and all its client sessions.
        await runner.cleanup()
