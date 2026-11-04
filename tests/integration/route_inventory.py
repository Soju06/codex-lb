"""Enumerate every route an application serves, through FastAPI's public walker.

Since FastAPI 0.137 ``include_router()`` keeps included routers as intermediate
entries, so ``app.routes`` is no longer a flat list of ``APIRoute`` objects
(https://github.com/fastapi/fastapi/releases/tag/0.137.0). ``iter_route_contexts``
is what FastAPI's own OpenAPI generation uses: it yields one context per served
route, carrying the effective path, methods, endpoint and merged dependant
(router-level ``dependencies`` included), and exposes the declared route as
``original_route``.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from starlette.routing import WebSocketRoute


def iter_routes(app: FastAPI) -> Iterator[RouteContext]:
    yield from iter_route_contexts(app.routes)


def iter_api_routes(app: FastAPI) -> Iterator[RouteContext]:
    for context in iter_routes(app):
        if isinstance(context.original_route, APIRoute):
            yield context


def iter_websocket_routes(app: FastAPI) -> Iterator[RouteContext]:
    for context in iter_routes(app):
        if isinstance(context.original_route, WebSocketRoute):
            yield context


def route_path(context: RouteContext) -> str:
    path = context.path
    assert path is not None, f"route {context.name!r} has no path"
    return path
